import os
import sys
import io
import json
import sqlite3
import csv
from datetime import datetime
import re
from flask import Flask, request, jsonify, render_template, send_file, redirect, send_from_directory, session, url_for
try:
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
except Exception:
    mm = 1.0
    class _DummyCanvas:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("Reportlab no disponible")
    canvas = type("canvas", (), {"Canvas": _DummyCanvas})
try:
    from num2words import num2words
except Exception:
    def num2words(n): return str(n)
from werkzeug.utils import secure_filename
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except Exception:
    pd = None
    PANDAS_AVAILABLE = False
try:
    from barcode_lookup import ProductLookup
    product_lookup = ProductLookup()
except Exception:
    class ProductLookup:
        def buscar_producto(self, codigo_barras: str):
            return None

# Paths
APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "database.db")
PARENT_DIR = os.path.dirname(APP_DIR)
ICONOS_DIR = os.path.join(PARENT_DIR, "Iconos")
FACTURAS_DIR = os.path.join(APP_DIR, "facturas")
os.makedirs(FACTURAS_DIR, exist_ok=True)
UPLOAD_FOLDER = os.path.join(APP_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
ALLOWED_EXTENSIONS = {'csv', 'xlsx', 'xls'}

def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def query_all(sql, params=()):
    with get_db() as conn:
        cur = conn.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

def query_one(sql, params=()):
    with get_db() as conn:
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

def execute(sql, params=()):
    with get_db() as conn:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid

def calcular_totales_detalle(items):
    exento = 0.0
    gravado15 = 0.0
    gravado18 = 0.0
    isv15 = 0.0
    isv18 = 0.0

    # id_isv: 1=15%, 2=18%, 3=exento
    for it in items:
        subtotal = float(it["cantidad"]) * float(it["precio"])
        id_isv = int(it.get("id_isv", 3))
        if id_isv == 3:
            exento += subtotal
        elif id_isv == 1:
            base = subtotal / 1.15
            gravado15 += base
            isv15 += base * 0.15
        elif id_isv == 2:
            base = subtotal / 1.18
            gravado18 += base
            isv18 += base * 0.18

    total = exento + gravado15 + gravado18 + isv15 + isv18
    return dict(exento=round(exento,2), gravado15=round(gravado15,2), gravado18=round(gravado18,2),
                isv15=round(isv15,2), isv18=round(isv18,2), total=round(total,2))

def generar_pdf_factura(factura_id, cliente_nombre, items, totales, efectivo=None, cambio=None, numero_factura=None, cai_str=None, rtn_cliente=None):
    width = 80 * mm
    height = 297 * mm
    nombre_pdf = os.path.join(FACTURAS_DIR, f"factura_{factura_id}.pdf")
    pdf = canvas.Canvas(nombre_pdf, pagesize=(width, height))
    
    y = height - 15 * mm

    # ============ LOGO ============
    try:
        posibles = [
            os.path.join(PARENT_DIR, "ventas_web", "static", "logo.png"),
            os.path.join(PARENT_DIR, "Imagenes", "logo.png"),
            os.path.join(PARENT_DIR, "Imagenes", "logo.jpg"),
            os.path.join(PARENT_DIR, "Imagenes", "Odus.png")
        ]
        for p in posibles:
            if os.path.exists(p):
                iw = 60 * mm
                ih = 25 * mm
                x_logo = (width - iw) / 2
                pdf.drawImage(p, x_logo, y - ih, iw, ih, preserveAspectRatio=True, mask='auto')
                y -= (ih + 5 * mm)
                break
    except Exception:
        pass

    # ============ INFORMACIÓN DE LA COMPAÑÍA ============
    cia = query_one("SELECT nombre_cia, direccion1, direccion2, rtn_cia AS rtn, correo, telefono FROM compania LIMIT 1") or {}
    nombre_cia = cia.get("nombre_cia", "INVERSIONES ISABELLA")
    direccion1 = cia.get("direccion1", "Bo. El Centro, Desvio al Mochito")
    direccion2 = cia.get("direccion2", "Peña Blanca, Santa Cruz")
    rtn = cia.get("rtn", "40519850003362")
    correo = cia.get("correo", "sandrar@live.com")
    telefono = cia.get("telefono", "+504 9781-3861")

    # Nombre de la compañía (centrado, negrita)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawCentredString(width/2, y, nombre_cia.upper())
    y -= 4 * mm

    # Dirección y datos (centrado, normal)
    pdf.setFont("Helvetica", 7)
    if direccion1:
        pdf.drawCentredString(width/2, y, direccion1)
        y -= 3.5 * mm
    if direccion2:
        pdf.drawCentredString(width/2, y, direccion2)
        y -= 3.5 * mm
    if rtn:
        pdf.drawCentredString(width/2, y, f"RTN: {rtn}")
        y -= 3.5 * mm
    if telefono:
        pdf.drawCentredString(width/2, y, f"Tel: {telefono}")
        y -= 3.5 * mm
    if correo:
        pdf.drawCentredString(width/2, y, correo)
        y -= 5 * mm

    # ============ INFORMACIÓN DEL CAI ============
    est = pem = tip = ndoc = None
    rangoi = rangof = flim = None
    
    # Determinar si es factura exenta (solo si hay exento y no hay gravado)
    tipo_req = 'G'
    try:
        t_ex = float(totales.get('exento', 0) or 0)
        t_g15 = float(totales.get('gravado15', 0) or 0)
        t_g18 = float(totales.get('gravado18', 0) or 0)
        if t_ex > 0 and t_g15 == 0 and t_g18 == 0:
            tipo_req = 'E'
    except Exception:
        pass

    if conectar_mysql is not None:
        try:
            connm = conectar_mysql()
            curm = connm.cursor()
            # Intentar buscar CAI activo del tipo requerido
            curm.execute("SELECT cai, establecimiento, punto_emision, tipo_doc, numero_documento, rango_i, rango_f, f_limite FROM info_cai WHERE activo=1 AND tipo=%s ORDER BY id DESC LIMIT 1", (tipo_req,))
            r = curm.fetchone()
            # Si es Exenta y no encuentra, intentar con General (fallback) o viceversa? 
            # Mejor estricto, si no hay Exenta configurada, usar General si existe?
            # Por ahora, si no encuentra del tipo especifico, busquemos cualquiera activo (comportamiento anterior)
            if not r:
                 curm.execute("SELECT cai, establecimiento, punto_emision, tipo_doc, numero_documento, rango_i, rango_f, f_limite FROM info_cai WHERE activo=1 ORDER BY id DESC LIMIT 1")
                 r = curm.fetchone()
            
            if r:
                cai_str = cai_str or r[0]
                est, pem, tip, ndoc, rangoi, rangof, flim = r[1], r[2], r[3], r[4], r[5], r[6], r[7]
            connm.close()
        except Exception as e:
            return jsonify({"error": f"Error MySQL al registrar venta: {str(e)}"}), 500
    # (CAI y rango se muestran al final del documento)


    # ============ NÚMERO DE FACTURA Y DATOS ============
    # Construir número de factura
    if not numero_factura:
        try:
            if est is not None and pem is not None and tip is not None and ndoc is not None:
                numero_factura = f"{int(est):03d}-{int(pem):03d}-{int(tip):02d}-{int(ndoc):08d}"
        except Exception:
            numero_factura = f"000-001-01-{str(factura_id).zfill(8)}"

    pdf.setFont("Helvetica", 7)
    pdf.drawString(5 * mm, y, f"Factura #:")
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawRightString(width - 5 * mm, y, numero_factura or "000-001-01-00000000")
    y -= 5 * mm
    pdf.setFont("Helvetica", 7)

    # Fecha y hora
    fecha_hora = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    pdf.drawString(5 * mm, y, f"Fecha:")
    pdf.drawRightString(width - 5 * mm, y, fecha_hora)
    y -= 3.5 * mm

    # Número de referencia interno
    try:
        ref = str(factura_id).zfill(6)
    except Exception:
        ref = str(factura_id).zfill(6)
    pdf.drawString(5 * mm, y, f"Nro ref:")
    pdf.drawRightString(width - 5 * mm, y, ref)
    y -= 3.5 * mm

    # Cliente
    pdf.drawString(5 * mm, y, f"Cliente:")
    pdf.drawRightString(width - 5 * mm, y, cliente_nombre or "CONSUMIDOR FINAL")
    y -= 3.5 * mm

    # RTN
    pdf.drawString(5 * mm, y, f"RTN:")
    pdf.drawRightString(width - 5 * mm, y, str(rtn_cliente or ""))
    y -= 3.5 * mm

    # Cajero/Usuario
    pdf.drawString(5 * mm, y, f"Cajero:")
    pdf.drawRightString(width - 5 * mm, y, "Admin")
    y -= 5 * mm

    # Línea separadora
    pdf.line(5 * mm, y, width - 5 * mm, y)
    y -= 4 * mm

    # ============ ENCABEZADOS DE TABLA ============
    pdf.setFont("Helvetica-Bold", 7)
    pdf.drawString(5 * mm, y, "Cod.")
    pdf.drawString(14 * mm, y, "Desc")
    pdf.drawRightString(width - 27 * mm, y, "Cant")
    pdf.drawRightString(width - 15 * mm, y, "Precio")
    pdf.drawRightString(width - 5 * mm, y, "Total")
    y -= 4 * mm

    # ============ ITEMS DE LA FACTURA ============
    pdf.setFont("Helvetica", 7)
    def _clip_text(text, max_w):
        s = str(text or "")
        try:
            if pdf.stringWidth(s, "Helvetica", 7) <= max_w:
                return s
        except Exception:
            return s[:int(max_w // 3)]
        out = ""
        for ch in s:
            try:
                if pdf.stringWidth(out + ch, "Helvetica", 7) <= max_w:
                    out += ch
                else:
                    break
            except Exception:
                break
        if len(out) > 3:
            out = out[:-3] + "..."
        return out
    
    for it in items:
        try:
            codigo = str(it.get("codigo", ""))
            nombre = str(it.get("descripcion", ""))
            cantidad = float(it.get("cantidad", 0))
            precio = float(it.get("precio", 0))
        except Exception:
            continue
        
        subtotal = cantidad * precio
        
        if not nombre.strip():
            continue

        x_code = 5 * mm
        x_desc = 14 * mm
        x_cant_right = width - 27 * mm
        x_precio_right = width - 15 * mm
        x_total_right = width - 5 * mm
        code_max_w = x_desc - x_code
        precio_str = f"L {precio:.2f}"
        q_str = f"{cantidad:.1f}"
        try:
            precio_w = pdf.stringWidth(precio_str, "Helvetica", 7)
        except Exception:
            precio_w = 20 * mm
        try:
            q_w = pdf.stringWidth(q_str, "Helvetica", 7)
        except Exception:
            q_w = 8 * mm
        margin = 3 * mm
        desc_max_w = (x_cant_right - margin - q_w) - x_desc
        if desc_max_w < (10 * mm):
            desc_max_w = 10 * mm
        codigo_linea = _clip_text(codigo, code_max_w)
        nombre_linea = _clip_text(nombre, desc_max_w)
        pdf.drawString(x_code, y, codigo_linea)
        pdf.drawString(x_desc, y, nombre_linea)
        pdf.drawRightString(x_cant_right, y, q_str)
        pdf.drawRightString(x_precio_right, y, precio_str)
        pdf.drawRightString(x_total_right, y, f"L {subtotal:.2f}")
        y -= 3.5 * mm

    # Línea separadora
    y -= 1 * mm
    pdf.line(5 * mm, y, width - 5 * mm, y)
    y -= 3 * mm

    # ============ TOTALES ============
    pdf.setFont("Helvetica", 7)
    
    ex_val = float(totales.get('exento', 0.00) or 0.0)
    g15_val = float(totales.get('gravado15', 0.00) or 0.0)
    g18_val = float(totales.get('gravado18', 0.00) or 0.0)
    i15_val = float(totales.get('isv15', 0.00) or 0.0)
    i18_val = float(totales.get('isv18', 0.00) or 0.0)
    desc_val = float(totales.get('descuento', 0.00) or 0.0)
    pdf.drawString(5 * mm, y, "Importe Exento:")
    pdf.drawRightString(width - 5 * mm, y, f"L {ex_val:.2f}")
    y -= 4 * mm
    pdf.drawString(5 * mm, y, "Gravado 15%:")
    pdf.drawRightString(width - 5 * mm, y, f"L {g15_val:.2f}")
    y -= 4 * mm
    pdf.drawString(5 * mm, y, "Gravado 18%:")
    pdf.drawRightString(width - 5 * mm, y, f"L {g18_val:.2f}")
    y -= 4 * mm
    pdf.drawString(5 * mm, y, "ISV 15%:")
    pdf.drawRightString(width - 5 * mm, y, f"L {i15_val:.2f}")
    y -= 4 * mm
    pdf.drawString(5 * mm, y, "ISV 18%:")
    pdf.drawRightString(width - 5 * mm, y, f"L {i18_val:.2f}")
    y -= 4 * mm
    pdf.drawString(5 * mm, y, "Descuento:")
    pdf.drawRightString(width - 5 * mm, y, f"L {desc_val:.2f}")
    y -= 5 * mm

    # Línea punteada
    pdf.setDash(1, 2)
    pdf.line(5 * mm, y, width - 5 * mm, y)
    pdf.setDash([])
    y -= 4 * mm

    # TOTAL (negrita, más grande)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawString(5 * mm, y, "TOTAL:")
    pdf.drawRightString(width - 5 * mm, y, f"L {totales['total']:.2f}")
    y -= 5 * mm

    # Línea punteada
    pdf.setDash(1, 2)
    pdf.line(5 * mm, y, width - 5 * mm, y)
    pdf.setDash([])
    y -= 4 * mm

    # Efectivo y cambio
    if efectivo is None:
        efectivo = totales["total"]
    if cambio is None:
        cambio = round(float(efectivo) - float(totales["total"]), 2)

    pdf.setFont("Helvetica", 7)
    pdf.drawString(5 * mm, y, "Efectivo:")
    pdf.drawRightString(width - 5 * mm, y, f"L {efectivo:.2f}")
    y -= 3.5 * mm

    pdf.drawString(5 * mm, y, "Cambio:")
    pdf.drawRightString(width - 5 * mm, y, f"L {cambio:.2f}")
    y -= 6 * mm
    try:
        from num2words import num2words
        total_val = float(totales.get("total", 0.0))
        entero = int(total_val)
        centavos = int(round((total_val - entero) * 100))
        if centavos == 100:
            entero += 1
            centavos = 0
        texto = num2words(entero, lang='es').upper()
        if centavos == 0:
            literal = f"*** {texto} LEMPIRAS EXACTOS ***"
        else:
            literal = f"*** {texto} LEMPIRAS {centavos:02d}/100 ***"
        pdf.setFont("Helvetica", 7)
        pdf.drawCentredString(width/2, y, literal)
        y -= 5 * mm
    except Exception:
        pass
    # Información CAI y rango al pie
    try:
        pdf.setFont("Helvetica", 7)
        if rangoi is not None and rangof is not None:
            try:
                ri = f"{str(rangoi).zfill(8)}"
                rf = f"{str(rangof).zfill(8)}"
            except Exception:
                ri = f"{str(rangoi).zfill(8)}"
                rf = f"{str(rangof).zfill(8)}"
            pdf.drawString(5 * mm, y, f"Rango Autorizado: {ri} al {rf}")
            y -= 4 * mm
        if cai_str:
            pdf.drawString(5 * mm, y, f"CAI: {cai_str}")
            y -= 4 * mm
        if flim:
            pdf.drawString(5 * mm, y, f"Fecha Límite Emisión: {flim}")
            y -= 6 * mm
        # Orden Exenta - datos adicionales
        try:
            pdf.setFont("Helvetica", 7)
            oe_val = str(totales.get("orden_exenta", "") or "").strip()
            ce_val = str(totales.get("constancia_exonerada", "") or "").strip()
            sag_val = str(totales.get("registro_sag", "") or "").strip()
            pdf.drawString(5 * mm, y, "Orden Exenta:")
            if oe_val:
                pdf.drawRightString(width - 5 * mm, y, oe_val)
            y -= 4 * mm
            pdf.drawString(5 * mm, y, "No. Constancia Exonerada:")
            if ce_val:
                pdf.drawRightString(width - 5 * mm, y, ce_val)
            y -= 4 * mm
            pdf.drawString(5 * mm, y, "No. Registro SAG:")
            if sag_val:
                pdf.drawRightString(width - 5 * mm, y, sag_val)
            y -= 6 * mm
        except Exception:
            pass
    except Exception:
        pass
    # ============ MENSAJE FINAL ============
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawCentredString(width/2, y, "¡GRACIAS POR SU COMPRA!")
    y -= 5 * mm

    pdf.setFont("Helvetica", 6)
    pdf.drawCentredString(width/2, y, "LA FACTURA ES BENEFICIO DE TODOS, EXIJALA")
    y -= 5 * mm

    pdf.setFont("Helvetica", 6)
    pdf.drawCentredString(width/2, y, "Original: Cliente / Copia: Emisor")

    # Guardar PDF
    pdf.save()
    return nombre_pdf
        
app = Flask(__name__)
app.secret_key = os.getenv("APP_SECRET_KEY", "dev")

# Importar MySQL del proyecto principal
try:
    if PARENT_DIR not in sys.path:
        sys.path.append(PARENT_DIR)
    from db import (
        conectar_mysql,
        asegurar_tablas_mysql,
        MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_DB,
    )
    try:
        from db import insertar_venta_encabezado_mysql, insertar_venta_detalle_mysql, asegurar_tabla_ventas_mysql
        from db import insertar_sar_venta_encabezado_mysql, insertar_sar_venta_detalle_mysql, asegurar_tabla_sar_ventas_mysql
    except Exception:
        insertar_venta_encabezado_mysql = None
        insertar_venta_detalle_mysql = None
        asegurar_tabla_ventas_mysql = None
        insertar_sar_venta_encabezado_mysql = None
        insertar_sar_venta_detalle_mysql = None
        asegurar_tabla_sar_ventas_mysql = None
except Exception:
    conectar_mysql = None
    asegurar_tablas_mysql = None
    MYSQL_HOST = None
    MYSQL_PORT = None
    MYSQL_USER = None
    MYSQL_DB = None
SKIP_MYSQL_INIT = (os.getenv("APP_SKIP_MYSQL_INIT") == "1")
def _asegurar_tabla_categorias_local():
    try:
        with get_db() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS categorias (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT UNIQUE NOT NULL)")
    except Exception:
        pass
def _asegurar_tabla_inventario_barras_local():
    try:
        with get_db() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS inventario_barras (id INTEGER PRIMARY KEY AUTOINCREMENT, producto_id INTEGER NOT NULL, barra TEXT UNIQUE NOT NULL)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_inventario_barras_producto ON inventario_barras (producto_id)")
    except Exception:
        pass
def _asegurar_tabla_info_cai_local():
    try:
        with get_db() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS info_cai (
                    autorizacion INTEGER PRIMARY KEY AUTOINCREMENT,
                    cai TEXT NOT NULL,
                    fecha_solicitud TEXT,
                    rango_i INTEGER,
                    rango_f INTEGER,
                    f_limite TEXT,
                    establecimiento INTEGER,
                    punto_emision INTEGER,
                    tipo_doc INTEGER,
                    numero_documento INTEGER,
                    activo INTEGER DEFAULT 1,
                    tipo TEXT DEFAULT 'G'
                )
            """)
            try:
                conn.execute("ALTER TABLE info_cai ADD COLUMN activo INTEGER DEFAULT 1")
            except Exception:
                pass
            try:
                conn.execute("ALTER TABLE info_cai ADD COLUMN tipo TEXT DEFAULT 'G'")
            except Exception:
                pass
    except Exception:
        pass
def _asegurar_tabla_info_cai_mysql():
    if conectar_mysql is None:
        return
    try:
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS info_cai (
                id INT AUTO_INCREMENT PRIMARY KEY,
                cai VARCHAR(255) NOT NULL,
                fecha_solicitud VARCHAR(32),
                rango_i INT,
                rango_f INT,
                f_limite VARCHAR(32),
                establecimiento INT,
                punto_emision INT,
                tipo_doc INT,
                numero_documento INT,
                activo TINYINT DEFAULT 1,
                tipo CHAR(1) DEFAULT 'G'
            )
        """)
        try:
            cur.execute("SHOW COLUMNS FROM info_cai")
            cols = [r[0].lower() for r in cur.fetchall()]
            
            if 'rango_inicial' in cols and 'rango_i' not in cols:
                try: cur.execute("ALTER TABLE info_cai CHANGE rango_inicial rango_i INT")
                except: pass
            if 'rango_final' in cols and 'rango_f' not in cols:
                try: cur.execute("ALTER TABLE info_cai CHANGE rango_final rango_f INT")
                except: pass
            if 'fecha_limite' in cols and 'f_limite' not in cols:
                try: cur.execute("ALTER TABLE info_cai CHANGE fecha_limite f_limite VARCHAR(32)")
                except: pass
                
            # Refresh cols
            cur.execute("SHOW COLUMNS FROM info_cai")
            cols = [r[0].lower() for r in cur.fetchall()]
            
            if 'fecha_solicitud' not in cols:
                try: cur.execute("ALTER TABLE info_cai ADD COLUMN fecha_solicitud VARCHAR(32)")
                except: pass
            if 'establecimiento' not in cols:
                try: cur.execute("ALTER TABLE info_cai ADD COLUMN establecimiento INT")
                except: pass
            if 'punto_emision' not in cols:
                try: cur.execute("ALTER TABLE info_cai ADD COLUMN punto_emision INT")
                except: pass
            if 'tipo_doc' not in cols:
                try: cur.execute("ALTER TABLE info_cai ADD COLUMN tipo_doc INT")
                except: pass
            if 'numero_documento' not in cols:
                try: cur.execute("ALTER TABLE info_cai ADD COLUMN numero_documento INT")
                except: pass
            if 'tipo' not in cols:
                try: cur.execute("ALTER TABLE info_cai ADD COLUMN tipo CHAR(1) DEFAULT 'G'")
                except: pass
        except Exception:
            pass
        connm.commit()
        connm.close()
    except Exception:
        try:
            connm.close()
        except Exception:
            pass
def _tabla_cai(tipo: str) -> str:
    t = (tipo or "G").strip().upper()
    return "info_cai_general" if t == "G" else "info_cai_exenta"
def _asegurar_tablas_cai_separadas_mysql():
    if conectar_mysql is None:
        return
    try:
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS info_cai_general (
                id INT AUTO_INCREMENT PRIMARY KEY,
                cai VARCHAR(255) NOT NULL,
                fecha_solicitud VARCHAR(32),
                rango_i INT,
                rango_f INT,
                f_limite VARCHAR(32),
                establecimiento INT,
                punto_emision INT,
                tipo_doc INT,
                numero_documento INT,
                activo TINYINT DEFAULT 1
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS info_cai_exenta (
                id INT AUTO_INCREMENT PRIMARY KEY,
                cai VARCHAR(255) NOT NULL,
                fecha_solicitud VARCHAR(32),
                rango_i INT,
                rango_f INT,
                f_limite VARCHAR(32),
                establecimiento INT,
                punto_emision INT,
                tipo_doc INT,
                numero_documento INT,
                activo TINYINT DEFAULT 1
            )
        """)
        try:
            cur.execute("SELECT COUNT(*) FROM info_cai_general")
            cnt_g = int(cur.fetchone()[0] or 0)
        except Exception:
            cnt_g = 0
        try:
            cur.execute("SELECT COUNT(*) FROM info_cai_exenta")
            cnt_e = int(cur.fetchone()[0] or 0)
        except Exception:
            cnt_e = 0
        # Migrar datos antiguos si existen y las tablas nuevas están vacías
        if cnt_g == 0 or cnt_e == 0:
            try:
                cur.execute("SHOW TABLES LIKE 'info_cai'")
                has_old = cur.fetchone()
                if has_old:
                    if cnt_g == 0:
                        cur.execute("""
                            INSERT INTO info_cai_general (cai, fecha_solicitud, rango_i, rango_f, f_limite, establecimiento, punto_emision, tipo_doc, numero_documento, activo)
                            SELECT cai, fecha_solicitud, rango_i, rango_f, f_limite, establecimiento, punto_emision, tipo_doc, numero_documento, activo
                            FROM info_cai WHERE tipo='G'
                        """)
                    if cnt_e == 0:
                        cur.execute("""
                            INSERT INTO info_cai_exenta (cai, fecha_solicitud, rango_i, rango_f, f_limite, establecimiento, punto_emision, tipo_doc, numero_documento, activo)
                            SELECT cai, fecha_solicitud, rango_i, rango_f, f_limite, establecimiento, punto_emision, tipo_doc, numero_documento, activo
                            FROM info_cai WHERE tipo='E'
                        """)
            except Exception:
                pass
        connm.commit()
        connm.close()
    except Exception:
        try:
            connm.close()
        except Exception:
            pass
def _asegurar_tabla_cierres_caja_mysql():
    if conectar_mysql is None:
        return
    try:
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS cierres_caja (
                id_cierre INT AUTO_INCREMENT PRIMARY KEY,
                fecha_inicio DATETIME,
                fecha_fin DATETIME NULL,
                monto_apertura DECIMAL(10,2) DEFAULT 0,
                monto_cierre DECIMAL(10,2) DEFAULT 0,
                usuario VARCHAR(255)
            )
        """)
        try:
            cur.execute("ALTER TABLE cierres_caja ADD COLUMN fecha_inicio DATETIME")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE cierres_caja ADD COLUMN fecha_fin DATETIME NULL")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE cierres_caja ADD COLUMN monto_apertura DECIMAL(10,2) DEFAULT 0")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE cierres_caja ADD COLUMN monto_cierre DECIMAL(10,2) DEFAULT 0")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE cierres_caja ADD COLUMN usuario VARCHAR(255)")
        except Exception:
            pass
        connm.commit()
        connm.close()
    except Exception:
        try:
            connm.close()
        except Exception:
            pass
if not SKIP_MYSQL_INIT:
    try:
        if asegurar_tablas_mysql is not None:
            asegurar_tablas_mysql()
    except Exception:
        pass
_asegurar_tabla_categorias_local()
_asegurar_tabla_inventario_barras_local()
_asegurar_tabla_info_cai_local()
if not SKIP_MYSQL_INIT:
    _asegurar_tabla_info_cai_mysql()

def _hash_password(password: str) -> str:
    try:
        import bcrypt
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    except Exception:
        return password

def _verify_password(hashed: str, password: str) -> bool:
    try:
        import bcrypt
        if isinstance(hashed, bytes):
            hashed = hashed.decode("utf-8")
        if str(hashed).startswith("$2"):
            return bcrypt.checkpw(password.encode("utf-8"), str(hashed).encode("utf-8"))
        return str(hashed) == password
    except Exception:
        return str(hashed) == password

def _asegurar_tabla_usuarios_mysql():
    if conectar_mysql is None:
        return
    connm = None
    try:
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS usuarios (usuario VARCHAR(64) PRIMARY KEY, nombre VARCHAR(255) NOT NULL, contrasena VARCHAR(255) NOT NULL, rol VARCHAR(32) NOT NULL, activo TINYINT(1) NOT NULL DEFAULT 1)")
        try:
            cur.execute("SHOW COLUMNS FROM usuarios")
            cols = [r[0].lower() for r in cur.fetchall() if r and len(r) > 0]
        except Exception:
            cols = []
        # Renombrar columnas comunes si existen
        try:
            if 'password' in cols and 'contrasena' not in cols:
                cur.execute("ALTER TABLE usuarios CHANGE COLUMN password contrasena VARCHAR(255) NOT NULL")
        except Exception:
            pass
        try:
            cur.execute("SHOW COLUMNS FROM usuarios")
            cols = [r[0].lower() for r in cur.fetchall() if r and len(r) > 0]
        except Exception:
            cols = []
        try:
            if 'username' in cols and 'usuario' not in cols:
                cur.execute("ALTER TABLE usuarios CHANGE COLUMN username usuario VARCHAR(64) NOT NULL")
        except Exception:
            pass
        try:
            cur.execute("SHOW COLUMNS FROM usuarios")
            cols = [r[0].lower() for r in cur.fetchall() if r and len(r) > 0]
        except Exception:
            cols = []
        try:
            if 'user' in cols and 'usuario' not in cols:
                cur.execute("ALTER TABLE usuarios CHANGE COLUMN user usuario VARCHAR(64) NOT NULL")
        except Exception:
            pass
        try:
            cur.execute("SHOW COLUMNS FROM usuarios")
            cols = [r[0].lower() for r in cur.fetchall() if r and len(r) > 0]
        except Exception:
            cols = []
        try:
            if 'role' in cols and 'rol' not in cols:
                cur.execute("ALTER TABLE usuarios CHANGE COLUMN role rol VARCHAR(32) NOT NULL")
        except Exception:
            pass
        try:
            cur.execute("SHOW COLUMNS FROM usuarios")
            cols = [r[0].lower() for r in cur.fetchall() if r and len(r) > 0]
        except Exception:
            cols = []
        try:
            if 'name' in cols and 'nombre' not in cols:
                cur.execute("ALTER TABLE usuarios CHANGE COLUMN name nombre VARCHAR(255) NOT NULL")
        except Exception:
            pass
        try:
            cur.execute("SHOW COLUMNS FROM usuarios")
            cols = [r[0].lower() for r in cur.fetchall() if r and len(r) > 0]
        except Exception:
            cols = []
        # Agregar columnas faltantes con valores por defecto seguros
        try:
            if 'contrasena' not in cols:
                cur.execute("ALTER TABLE usuarios ADD COLUMN contrasena VARCHAR(255) NOT NULL DEFAULT ''")
        except Exception:
            pass
        try:
            if 'rol' not in cols:
                cur.execute("ALTER TABLE usuarios ADD COLUMN rol VARCHAR(32) NOT NULL DEFAULT 'cajero'")
        except Exception:
            pass
        try:
            if 'activo' not in cols:
                cur.execute("ALTER TABLE usuarios ADD COLUMN activo TINYINT(1) NOT NULL DEFAULT 1")
        except Exception:
            pass
        connm.commit()
    finally:
        try:
            if connm:
                connm.close()
        except Exception:
            pass

@app.get("/login")
def login_view():
    msg = request.args.get("msg")
    msg_type = request.args.get("type", "info")
    return render_template("login.html", msg=msg, msg_type=msg_type)

@app.post("/login")
def login_post():
    usuario = (request.form.get("usuario") or "").strip()
    contrasena = (request.form.get("contrasena") or "").strip()
    if not usuario or not contrasena:
        return render_template("login.html", msg="Debe ingresar usuario y contraseña", msg_type="danger")
    if conectar_mysql is None:
        return render_template("login.html", msg="MySQL no disponible", msg_type="danger")
    try:
        connm = conectar_mysql()
        cur = connm.cursor()
        _asegurar_tabla_usuarios_mysql()
        try:
            cur.execute("SELECT COUNT(*) FROM usuarios")
            cnt = int(cur.fetchone()[0] or 0)
        except Exception:
            cnt = 0
        if cnt == 0:
            admin_user = os.getenv("APP_ADMIN_USER", "admin")
            admin_name = os.getenv("APP_ADMIN_NAME", "Administrador")
            admin_pass = os.getenv("APP_ADMIN_PASSWORD", "admin123")
            admin_role = os.getenv("APP_ADMIN_ROLE", "admin")
            hashed = _hash_password(admin_pass)
            try:
                cur.execute("INSERT INTO usuarios (usuario, nombre, contrasena, rol) VALUES (%s, %s, %s, %s)", (admin_user, admin_name, hashed, admin_role))
                connm.commit()
            except Exception:
                pass
        cur.execute("SELECT nombre, usuario, contrasena, rol FROM usuarios WHERE usuario = %s", (usuario,))
        r = cur.fetchone()
        if not r:
            connm.close()
            return render_template("login.html", msg="Usuario o contraseña incorrectos", msg_type="danger")
        nombre, user, hashed, rol = r[0], r[1], r[2], r[3]
        ok = _verify_password(hashed, contrasena)
        if not ok and str(hashed) == contrasena:
            nuevo_hash = _hash_password(contrasena)
            try:
                cur.execute("UPDATE usuarios SET contrasena = %s WHERE usuario = %s", (nuevo_hash, usuario))
                connm.commit()
                ok = True
            except Exception:
                ok = False
        connm.close()
        if not ok:
            return render_template("login.html", msg="Usuario o contraseña incorrectos", msg_type="danger")
        session["usuario"] = {"nombre": nombre, "usuario": user, "rol": rol}
        destino = request.args.get("next") or url_for("menu_principal_view")
        return redirect(destino)
    except Exception as e:
        try:
            connm.close()
        except Exception:
            pass
        return render_template("login.html", msg=f"Error de conexión: {str(e)}", msg_type="danger")

@app.get("/logout")
def logout():
    try:
        session.pop("usuario", None)
    except Exception:
        session.clear()
    return redirect(url_for("login_view"))

@app.before_request
def _require_login():
    endpoint = (request.endpoint or "").strip()
    if endpoint in {"login_view", "login_post", "logout", "handle_404"}:
        return
    path = request.path or ""
    if path.startswith("/static/") or path.startswith("/iconos/"):
        return
    user = session.get("usuario")
    if not user:
        nxt = request.full_path if request.full_path else request.path
        return redirect(url_for("login_view", next=nxt))

def _is_admin():
    u = session.get("usuario")
    return bool(u and str(u.get("rol","")).lower() == "admin")

def _to_julian(date_str):
    if not date_str: return None
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        a = (14 - dt.month) // 12
        y = dt.year + 4800 - a
        m = dt.month + 12 * a - 3
        return dt.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045
    except Exception:
        return None

def _from_julian(val):
    if not val: return ""
    try:
        # Si ya es string con formato fecha, retornar
        if isinstance(val, str) and "-" in val and len(val) == 10:
            return val
        J = int(val)
        f = J + 1401 + (((4 * J + 274277) // 146097) * 3) // 4 - 38
        e = 4 * f + 3
        g = (e % 1461) // 4
        h = 5 * g + 2
        D = (h % 153) // 5 + 1
        M = (h // 153 + 2) % 12 + 1
        Y = (e // 1461) - 4716 + (12 + 2 - M) // 12
        return f"{Y:04d}-{M:02d}-{D:02d}"
    except Exception:
        return str(val)

@app.before_request
def _force_https():
    # No forzar HTTPS para API ni en modo debug/desarrollo
    if app.debug or (request.path or "").startswith("/api/"):
        return
    proto = request.headers.get("X-Forwarded-Proto", "").lower()
    if request.is_secure or proto == "https":
        return
    url = request.url.replace("http://", "https://", 1)
    return redirect(url, code=301)

@app.route("/configuracion/cai", methods=["GET", "POST"])
def configuracion_cai():
    msg = None
    msg_type = None
    tipo_seleccionado = request.args.get("tipo", "G")
    ok_flag = request.args.get("ok")
    if ok_flag:
        msg = "Configuración guardada exitosamente"
        msg_type = "success"

    if request.method == "POST":
        print("DEBUG: POST /configuracion/cai received")
        cai = (request.form.get("cai") or "").strip().upper()
        fecha_sol = (request.form.get("fecha_solicitud") or "").strip()
        fecha_lim = (request.form.get("fecha_limite") or "").strip()
        rango_i = request.form.get("rango_inicial")
        rango_f = request.form.get("rango_final")
        est = request.form.get("establecimiento")
        pem = request.form.get("punto_emision")
        tip = request.form.get("tipo_doc")
        num = request.form.get("numero_documento")
        tipo_cai = request.form.get("tipo") or "G"
        print(f"DEBUG: Data: cai={cai}, sol={fecha_sol}, lim={fecha_lim}, ri={rango_i}, rf={rango_f}, est={est}, pem={pem}, tip={tip}, num={num}, tipo={tipo_cai}")
        
        if not validar_formato_cai(cai):
            msg = "Formato CAI incorrecto. Debe ser: XXXXXX-XXXXXX-XXXXXX-XXXXXX-XXXXXX-XX"
            msg_type = "danger"
            actual = {
                "cai": cai, "fecha_solicitud": fecha_sol, "rango_inicial": rango_i, "rango_final": rango_f,
                "fecha_limite": fecha_lim, "establecimiento": est, "punto_emision": pem, "tipo_doc": tip,
                "numero_documento": num, "tipo": tipo_cai
            }
            historial = []
            return render_template("gestion_cai.html", actual=actual, historial=historial, msg=msg, msg_type=msg_type, tipo_seleccionado=tipo_seleccionado)
        try:
            parsed_from_rangoi = False
            if rango_i and "-" in str(rango_i):
                m = re.match(r"^(\d{3})-(\d{3})-(\d{2})-(\d{8})$", str(rango_i))
                if m:
                    est = est or m.group(1)
                    pem = pem or m.group(2)
                    tip = tip or m.group(3)
                    num = num or m.group(4)
                    rango_i = m.group(4)
                    parsed_from_rangoi = True
            if rango_f and "-" in str(rango_f):
                m = re.match(r"^(\d{3})-(\d{3})-(\d{2})-(\d{8})$", str(rango_f))
                if m:
                    rango_f = m.group(4)
            # Validar que rangos sean dígitos
            if rango_i and "-" not in str(rango_i):
                if not str(rango_i).isdigit():
                    msg = "Rango inicial debe ser numérico"
                    msg_type = "danger"
                    actual = {
                        "cai": cai, "fecha_solicitud": fecha_sol, "rango_inicial": rango_i, "rango_final": rango_f,
                        "fecha_limite": fecha_lim, "establecimiento": est, "punto_emision": pem, "tipo_doc": tip,
                        "numero_documento": num, "tipo": tipo_cai
                    }
                    historial = []
                    return render_template("gestion_cai.html", actual=actual, historial=historial, msg=msg, msg_type=msg_type, tipo_seleccionado=tipo_seleccionado)
            if rango_f and "-" not in str(rango_f):
                if not str(rango_f).isdigit():
                    msg = "Rango final debe ser numérico"
                    msg_type = "danger"
                    actual = {
                        "cai": cai, "fecha_solicitud": fecha_sol, "rango_inicial": rango_i, "rango_final": rango_f,
                        "fecha_limite": fecha_lim, "establecimiento": est, "punto_emision": pem, "tipo_doc": tip,
                        "numero_documento": num, "tipo": tipo_cai
                    }
                    historial = []
                    return render_template("gestion_cai.html", actual=actual, historial=historial, msg=msg, msg_type=msg_type, tipo_seleccionado=tipo_seleccionado)
        except Exception:
            pass
        if conectar_mysql is None:
            msg = "MySQL no disponible"
        else:
            try:
                print("DEBUG: Connecting to MySQL...")
                _asegurar_tablas_cai_separadas_mysql()
                connm = conectar_mysql()
                cur = connm.cursor()
                try:
                    tbl = _tabla_cai(tipo_cai)
                    cur.execute(f"UPDATE {tbl} SET activo=0")
                    connm.commit()
                except Exception as e:
                    print(f"DEBUG: Error updating active status: {e}")
                    pass
                num_insert = None
                try:
                    if num:
                        ni = int(num)
                        if parsed_from_rangoi:
                            ni = max(0, ni - 1)
                        num_insert = ni
                except Exception:
                    num_insert = None
                
                print("DEBUG: Executing INSERT...")
                tbl = _tabla_cai(tipo_cai)
                cur.execute(
                    f"INSERT INTO {tbl} (cai, fecha_solicitud, rango_i, rango_f, f_limite, establecimiento, punto_emision, tipo_doc, numero_documento, activo) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 1)",
                    (
                        cai or None,
                        _to_julian(fecha_sol) if fecha_sol else None,
                        int(rango_i) if rango_i else None,
                        int(rango_f) if rango_f else None,
                        _to_julian(fecha_lim) if fecha_lim else None,
                        int(est) if est else None,
                        int(pem) if pem else None,
                        int(tip) if tip else None,
                        int(num_insert) if num_insert is not None else None
                    )
                )
                connm.commit()
                print(f"DEBUG: INSERT success. ID: {cur.lastrowid}")
                connm.close()
                msg = "Configuración guardada exitosamente"
                msg_type = "success"
                tipo_seleccionado = tipo_cai
                # Fall through to render logic to ensure message is shown
                # return redirect(f"/configuracion/cai?tipo={tipo_cai}&ok=1")
            except Exception as e:
                print(f"DEBUG: Exception during save: {e}")
                try:
                    connm.close()
                except Exception:
                    pass
                msg = f"Error al guardar en BD: {str(e)}"
                msg_type = "danger"
                actual = {
                    "cai": cai, "fecha_solicitud": fecha_sol, "rango_inicial": rango_i, "rango_final": rango_f,
                    "fecha_limite": fecha_lim, "establecimiento": est, "punto_emision": pem, "tipo_doc": tip,
                    "numero_documento": num, "tipo": tipo_cai
                }
                historial = []
                return render_template("gestion_cai.html", actual=actual, historial=historial, msg=msg, msg_type=msg_type, tipo_seleccionado=tipo_seleccionado)

    actual = None
    historial = []
    if conectar_mysql is not None:
        try:
            _asegurar_tablas_cai_separadas_mysql()
            connm = conectar_mysql()
            cur = connm.cursor()
            tbl_sel = _tabla_cai(tipo_seleccionado)
            cur.execute(f"SELECT cai, fecha_solicitud, rango_i, rango_f, f_limite, establecimiento, punto_emision, tipo_doc, numero_documento, activo FROM {tbl_sel} WHERE activo=1 ORDER BY id DESC LIMIT 1")
            r = cur.fetchone()
            if not r:
                cur.execute(f"SELECT cai, fecha_solicitud, rango_i, rango_f, f_limite, establecimiento, punto_emision, tipo_doc, numero_documento, activo FROM {('info_cai_exenta' if tbl_sel=='info_cai_general' else 'info_cai_general')} ORDER BY activo DESC, id DESC LIMIT 1")
                r = cur.fetchone()
            if r:
                try:
                    ri_pad = ("%08d" % int(r[2])) if r[2] is not None else ""
                except Exception:
                    ri_pad = str(r[2] or "")
                try:
                    rf_pad = ("%08d" % int(r[3])) if r[3] is not None else ""
                except Exception:
                    rf_pad = str(r[3] or "")
                actual = {
                    "cai": r[0],
                    "fecha_solicitud": _from_julian(r[1]),
                    "rango_i": r[2],
                    "rango_f": r[3],
                    "rango_inicial": ri_pad,
                    "rango_final": rf_pad,
                    "f_limite": _from_julian(r[4]),
                    "establecimiento": r[5],
                    "punto_emision": r[6],
                    "tipo_doc": r[7],
                    "numero_documento": r[8],
                    "activo": r[9],
                    "tipo": tipo_seleccionado
                }
            cur.execute(f"SELECT cai, fecha_solicitud, rango_i, rango_f, f_limite, establecimiento, punto_emision, tipo_doc, numero_documento, activo FROM {tbl_sel} ORDER BY activo DESC, id DESC LIMIT 10")
            rows = cur.fetchall()
            for r in rows:
                historial.append({
                    "cai": r[0],
                    "fecha_solicitud": _from_julian(r[1]),
                    "rango_i": r[2],
                    "rango_f": r[3],
                    "f_limite": _from_julian(r[4]),
                    "establecimiento": r[5],
                    "punto_emision": r[6],
                    "tipo_doc": r[7],
                    "numero_documento": r[8],
                    "activo": r[9],
                    "tipo": tipo_seleccionado
                })
            connm.close()
        except Exception:
            try:
                connm.close()
            except Exception:
                pass
    if not historial:
        historial = []
    
    return render_template("gestion_cai.html", actual=actual, historial=historial, msg=msg, msg_type=msg_type, tipo_seleccionado=tipo_seleccionado)

@app.post("/configuracion/cai/importar-pdf")
def configuracion_cai_importar_pdf():
    msg = None
    tipo_seleccionado = (request.args.get("tipo") or request.form.get("tipo") or "G")
    f = request.files.get("archivo") or request.files.get("autorizacion_pdf")
    actual = None
    historial = []
    if not f:
        return redirect(f"/configuracion/cai?tipo={tipo_seleccionado}")
    if not f.filename.lower().endswith(".pdf"):
        msg = "Seleccione un archivo PDF válido"
        return render_template("gestion_cai.html", actual=actual, historial=historial, msg=msg, tipo_seleccionado=tipo_seleccionado)
    try:
        nombre = secure_filename(f.filename)
        ruta = os.path.join(UPLOAD_FOLDER, nombre)
        f.save(ruta)
        parsed = _parse_autorizacion_pdf(ruta)
        if conectar_mysql is not None:
            try:
                _asegurar_tablas_cai_separadas_mysql()
                connm = conectar_mysql()
                cur = connm.cursor()
                tbl_sel = _tabla_cai(tipo_seleccionado)
                cur.execute(f"SELECT cai, fecha_solicitud, rango_i, rango_f, f_limite, establecimiento, punto_emision, tipo_doc, numero_documento, activo FROM {tbl_sel} ORDER BY activo DESC, id DESC LIMIT 10")
                rows = cur.fetchall()
                for r in rows:
                    historial.append({
                        "cai": r[0],
                        "fecha_solicitud": _from_julian(r[1]),
                        "rango_i": r[2],
                        "rango_f": r[3],
                        "f_limite": _from_julian(r[4]),
                        "establecimiento": r[5],
                        "punto_emision": r[6],
                        "tipo_doc": r[7],
                        "numero_documento": r[8],
                        "activo": r[9],
                        "tipo": tipo_seleccionado
                    })
                connm.close()
            except Exception:
                try:
                    connm.close()
                except Exception:
                    pass
        if parsed:
            def _last8(val):
                try:
                    s = str(val or "")
                    if "-" in s:
                        return s.split("-")[-1]
                    return s
                except Exception:
                    return str(val or "")
            actual = {
                "cai": parsed.get("cai") or "",
                "fecha_solicitud": parsed.get("fecha_solicitud") or "",
                "fecha_limite": parsed.get("fecha_limite") or "",
                "rango_inicial": _last8(parsed.get("rango_inicial")),
                "rango_final": _last8(parsed.get("rango_final")),
                "establecimiento": parsed.get("establecimiento"),
                "punto_emision": parsed.get("punto_emision"),
                "tipo_doc": parsed.get("tipo_doc"),
                "numero_documento": parsed.get("numero_documento")
            }
            msg = "Datos importados desde PDF"
        else:
            msg = "No se pudo leer datos del PDF"
        return render_template("gestion_cai.html", actual=actual, historial=historial, msg=msg, tipo_seleccionado=tipo_seleccionado)
    except Exception as e:
        msg = str(e)
        return render_template("gestion_cai.html", actual=actual, historial=historial, msg=msg, tipo_seleccionado=tipo_seleccionado)

@app.post("/configuracion/cai/inactivar")
def configuracion_cai_inactivar():
    msg = None
    cai = (request.form.get("cai") or "").strip().upper()
    est = request.form.get("establecimiento")
    pem = request.form.get("punto_emision")
    tip = request.form.get("tipo_doc")
    num = request.form.get("numero_documento")
    tipo_cai = request.form.get("tipo") or "G"
    if conectar_mysql is None:
        msg = "MySQL no disponible"
    else:
        try:
            _asegurar_tablas_cai_separadas_mysql()
            connm = conectar_mysql()
            cur = connm.cursor()
            tbl = _tabla_cai(tipo_cai)
            cur.execute(
                f"UPDATE {tbl} SET activo=0 WHERE cai=%s AND establecimiento=%s AND punto_emision=%s AND tipo_doc=%s AND numero_documento=%s",
                (cai, int(est) if est else None, int(pem) if pem else None, int(tip) if tip else None, int(num) if num else None)
            )
            connm.commit()
            connm.close()
            msg = "CAI inactivado"
        except Exception as e:
            try:
                connm.close()
            except Exception:
                pass
            msg = str(e)
    return redirect("/configuracion/cai")

@app.post("/configuracion/cai/eliminar")
def configuracion_cai_eliminar():
    msg = None
    cai = (request.form.get("cai") or "").strip().upper()
    est = request.form.get("establecimiento")
    pem = request.form.get("punto_emision")
    tip = request.form.get("tipo_doc")
    num = request.form.get("numero_documento")
    tipo_cai = request.form.get("tipo") or "G"
    if conectar_mysql is None:
        msg = "MySQL no disponible"
    else:
        try:
            _asegurar_tablas_cai_separadas_mysql()
            connm = conectar_mysql()
            cur = connm.cursor()
            tbl = _tabla_cai(tipo_cai)
            cur.execute(
                f"DELETE FROM {tbl} WHERE cai=%s AND establecimiento=%s AND punto_emision=%s AND tipo_doc=%s AND numero_documento=%s",
                (cai, int(est) if est else None, int(pem) if pem else None, int(tip) if tip else None, int(num) if num else None)
            )
            connm.commit()
            connm.close()
            msg = "CAI eliminado"
        except Exception as e:
            try:
                connm.close()
            except Exception:
                pass
            msg = str(e)
    return redirect("/configuracion/cai")

@app.post("/configuracion/cai/activar")
def configuracion_cai_activar():
    cai = (request.form.get("cai") or "").strip().upper()
    est = request.form.get("establecimiento")
    pem = request.form.get("punto_emision")
    tip = request.form.get("tipo_doc")
    num = request.form.get("numero_documento")
    tipo_cai = request.form.get("tipo") or "G"
    if conectar_mysql is not None:
        try:
            _asegurar_tablas_cai_separadas_mysql()
            connm = conectar_mysql()
            cur = connm.cursor()
            tbl = _tabla_cai(tipo_cai)
            cur.execute(f"UPDATE {tbl} SET activo=0")
            cur.execute(
                f"UPDATE {tbl} SET activo=1 WHERE cai=%s AND establecimiento=%s AND punto_emision=%s AND tipo_doc=%s AND numero_documento=%s",
                (cai, int(est) if est else None, int(pem) if pem else None, int(tip) if tip else None, int(num) if num else None)
            )
            connm.commit()
            connm.close()
        except Exception:
            try:
                connm.close()
            except Exception:
                pass
    return redirect("/configuracion/cai")
@app.route("/")
def index():
    return redirect("/menu")

@app.route("/configuracion/compania", methods=["GET", "POST"])
def configuracion_compania():
    msg = None
    if request.method == "POST":
        nombre_cia = (request.form.get("nombre_cia") or "").strip()
        direccion1 = (request.form.get("direccion1") or "").strip()
        direccion2 = (request.form.get("direccion2") or "").strip()
        rtn_cia = (request.form.get("rtn_cia") or "").strip()
        correo = (request.form.get("correo") or "").strip()
        telefono = (request.form.get("telefono") or "").strip()
        try:
            row = query_one("SELECT nombre_cia FROM compania LIMIT 1")
            if row:
                execute(
                    "UPDATE compania SET nombre_cia = ?, direccion1 = ?, direccion2 = ?, rtn_cia = ?, correo = ?, telefono = ?",
                    (nombre_cia, direccion1, direccion2, rtn_cia, correo, telefono)
                )
            else:
                execute(
                    "INSERT INTO compania (nombre_cia, direccion1, direccion2, rtn_cia, correo, telefono) VALUES (?, ?, ?, ?, ?, ?)",
                    (nombre_cia, direccion1, direccion2, rtn_cia, correo, telefono)
                )
            msg = "Datos de la empresa guardados"
        except Exception as e:
            msg = f"No se pudo guardar: {e}"
    try:
        execute("""
            CREATE TABLE IF NOT EXISTS compania (
              nombre_cia TEXT NOT NULL,
              direccion1 TEXT,
              direccion2 TEXT,
              rtn_cia TEXT,
              correo TEXT,
              telefono TEXT
            )
        """)
    except Exception:
        pass
    actual = query_one("SELECT nombre_cia, direccion1, direccion2, rtn_cia, correo, telefono FROM compania LIMIT 1")
    return render_template("gestion_compania.html", actual=actual, msg=msg)

@app.route("/health")
def health():
    return jsonify({"ok": True}), 200
@app.get("/api/mysql/health")
def api_mysql_health():
    if conectar_mysql is None:
        return jsonify({"ok": False, "available": False, "error": "driver"}), 503
    try:
        conn = conectar_mysql()
        cur = conn.cursor()
        cur.execute("SELECT DATABASE()")
        dbname = cur.fetchone()[0]
        version = None
        try:
            cur.execute("SELECT VERSION()")
            version = cur.fetchone()[0]
        except Exception:
            pass
        inv_count = None
        try:
            cur.execute("SELECT COUNT(*) FROM inventario")
            inv_count = cur.fetchone()[0]
        except Exception:
            pass
        conn.close()
        return jsonify({
            "ok": True,
            "available": True,
            "host": MYSQL_HOST,
            "port": MYSQL_PORT,
            "user": MYSQL_USER,
            "database": dbname,
            "version": version,
            "inventario_count": inv_count
        })
    except Exception as e:
        return jsonify({"ok": False, "available": True, "error": str(e)}), 500
@app.post("/api/mysql/ensure")
def api_mysql_ensure():
    try:
        if asegurar_tablas_mysql is not None:
            asegurar_tablas_mysql()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
@app.post("/api/mysql/cleanup-duplicados-barra")
def api_mysql_cleanup_duplicados_barra():
    if conectar_mysql is None:
        return jsonify({"ok": False, "error": "MySQL no disponible"}), 503
    try:
        conn = conectar_mysql()
        cur = conn.cursor()
        cur.execute("DELETE t1 FROM inventario t1 JOIN inventario t2 ON t1.barra = t2.barra AND t1.id > t2.id")
        eliminados = cur.rowcount
        conn.commit()
        conn.close()
        return jsonify({"ok": True, "eliminados": int(eliminados)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

def _asegurar_tablas_pedidos_mysql():
    if conectar_mysql is None:
        return
    connm = None
    try:
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pedidos (
                id_pedido INT AUTO_INCREMENT PRIMARY KEY,
                numero_pedido VARCHAR(64),
                fecha DATETIME,
                cliente VARCHAR(255),
                rtn_cliente VARCHAR(64),
                total DECIMAL(10,2),
                estado VARCHAR(32) DEFAULT 'pendiente',
                usuario VARCHAR(255)
            ) ENGINE=InnoDB
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS pedidos_detalle (
                id_detalle INT AUTO_INCREMENT PRIMARY KEY,
                id_pedido INT,
                numero_pedido VARCHAR(64),
                id VARCHAR(50),
                nombre_articulo VARCHAR(255),
                valor_articulo DECIMAL(10,2),
                cantidad DECIMAL(10,2),
                subtotal DECIMAL(10,2),
                gravado15 DECIMAL(10,2),
                gravado18 DECIMAL(10,2),
                totalexento DECIMAL(10,2),
                isv15 DECIMAL(10,2),
                isv18 DECIMAL(10,2),
                grantotal DECIMAL(10,2),
                INDEX idx_numero_pedido (numero_pedido),
                INDEX idx_id_pedido (id_pedido)
            ) ENGINE=InnoDB
        """)
        connm.commit()
    except Exception:
        try:
            connm.rollback()
        except Exception:
            pass
    finally:
        try:
            connm.close()
        except Exception:
            pass

@app.get("/api/pedidos/next")
def api_pedidos_next():
    if conectar_mysql is None:
        return jsonify({"error":"MySQL no disponible"}), 503
    try:
        _asegurar_tablas_pedidos_mysql()
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("SELECT COALESCE(MAX(id_pedido),0)+1 FROM pedidos")
        nxt = int(cur.fetchone()[0] or 1)
        connm.close()
        numero = f"PED-{str(nxt).zfill(6)}"
        return jsonify({"numero_pedido": numero})
    except Exception as e:
        try:
            connm.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

@app.post("/api/registrar-pedido")
def api_registrar_pedido():
    data = request.get_json(force=True) or {}
    items = data.get("items", [])
    if not items:
        return jsonify({"error":"Sin items"}), 400
    if conectar_mysql is None:
        return jsonify({"error":"MySQL no disponible"}), 503
    try:
        _asegurar_tablas_pedidos_mysql()
        connm = conectar_mysql()
        cur = connm.cursor()
        totales = calcular_totales_detalle(items)
        cliente_nombre = (data.get("cliente_nombre") or "CONSUMIDOR FINAL")
        cliente_rtn = (data.get("cliente_rtn") or "")
        usuario = ""
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute("INSERT INTO pedidos (numero_pedido, fecha, cliente, rtn_cliente, total, estado, usuario) VALUES (%s,%s,%s,%s,%s,%s,%s)", ("", fecha, cliente_nombre, cliente_rtn, totales["total"], "pendiente", usuario))
        connm.commit()
        cur.execute("SELECT LAST_INSERT_ID()")
        rid = int(cur.fetchone()[0])
        numero_pedido = f"PED-{str(rid).zfill(6)}"
        cur.execute("UPDATE pedidos SET numero_pedido=%s WHERE id_pedido=%s", (numero_pedido, rid))
        detalle = []
        for it in items:
            subtotal = float(it["precio"]) * float(it["cantidad"])
            id_isv = int(it.get("id_isv",3))
            grav15=grav18=ex=iv15=iv18=0.0
            if id_isv == 3:
                ex = subtotal
            elif id_isv == 1:
                base = subtotal/1.15; grav15 = base; iv15 = base*0.15
            elif id_isv == 2:
                base = subtotal/1.18; grav18 = base; iv18 = base*0.18
            detalle.append((
                rid, numero_pedido, it["codigo"], it["descripcion"], float(it["precio"]), float(it["cantidad"]),
                subtotal, grav15, grav18, ex, iv15, iv18, (grav15+grav18+ex+iv15+iv18)
            ))
        if detalle:
            cur.executemany("""
                INSERT INTO pedidos_detalle
                (id_pedido, numero_pedido, id, nombre_articulo, valor_articulo, cantidad, subtotal, gravado15, gravado18, totalexento, isv15, isv18, grantotal)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, detalle)
        connm.commit()
        connm.close()
        return jsonify({"ok": True, "pedido_id": rid, "numero_pedido": numero_pedido})
    except Exception as e:
        try:
            connm.rollback()
        except Exception:
            pass
        try:
            connm.close()
        except Exception:
            pass
        return jsonify({"error": f"MySQL error al registrar pedido: {str(e)}"}), 500
@app.get("/api/pedidos")
def api_pedidos_list():
    if conectar_mysql is None:
        return jsonify({"error":"MySQL no disponible"}), 503
    try:
        _asegurar_tablas_pedidos_mysql()
        q = (request.args.get("q") or "").strip()
        estado = (request.args.get("estado") or "").strip()
        limit = 100
        try:
            l = int(request.args.get("limit", "100"))
            if l > 0 and l <= 500:
                limit = l
        except Exception:
            pass
        connm = conectar_mysql()
        cur = connm.cursor()
        base = "SELECT id_pedido, numero_pedido, fecha, cliente, rtn_cliente, total, estado FROM pedidos WHERE 1=1"
        params = []
        if q:
            base += " AND (numero_pedido LIKE %s OR cliente LIKE %s OR rtn_cliente LIKE %s)"
            params.extend([f"%{q}%", f"%{q}%", f"%{q}%"])
        if estado:
            base += " AND estado = %s"
            params.append(estado)
        base += " ORDER BY id_pedido DESC LIMIT %s"
        params.append(limit)
        cur.execute(base, tuple(params))
        rows = cur.fetchall()
        connm.close()
        data = []
        for r in rows:
            data.append({
                "id_pedido": int(r[0]),
                "numero_pedido": str(r[1] or ""),
                "fecha": str(r[2] or ""),
                "cliente": str(r[3] or ""),
                "rtn_cliente": str(r[4] or ""),
                "total": float(r[5] or 0),
                "estado": str(r[6] or "pendiente"),
            })
        return jsonify(data)
    except Exception as e:
        try:
            connm.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500
@app.get("/api/pedidos/<numero>")
def api_pedido_get(numero):
    if conectar_mysql is None:
        return jsonify({"error":"MySQL no disponible"}), 503
    try:
        _asegurar_tablas_pedidos_mysql()
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("SELECT id_pedido, numero_pedido, fecha, cliente, rtn_cliente, total, estado FROM pedidos WHERE numero_pedido=%s", (numero,))
        h = cur.fetchone()
        if not h:
            connm.close()
            return jsonify({"error":"Pedido no encontrado"}), 404
        cur.execute("""
            SELECT id, nombre_articulo, valor_articulo, cantidad, gravado15, gravado18, totalexento, isv15, isv18, subtotal
            FROM pedidos_detalle
            WHERE numero_pedido=%s
            ORDER BY id_detalle ASC
        """, (numero,))
        det = cur.fetchall()
        connm.close()
        header = {
            "id_pedido": int(h[0]),
            "numero_pedido": str(h[1] or ""),
            "fecha": str(h[2] or ""),
            "cliente": str(h[3] or ""),
            "rtn_cliente": str(h[4] or ""),
            "total": float(h[5] or 0),
            "estado": str(h[6] or "pendiente"),
        }
        items = []
        for d in det:
            id_code = str(d[0] or "")
            nombre = str(d[1] or "")
            precio = float(d[2] or 0)
            cantidad = float(d[3] or 0)
            g15 = float(d[4] or 0)
            g18 = float(d[5] or 0)
            ex = float(d[6] or 0)
            iv15 = float(d[7] or 0)
            iv18 = float(d[8] or 0)
            subtotal = float(d[9] or precio*cantidad)
            if ex > 0 and g15 == 0 and g18 == 0:
                id_isv = 3
            elif g15 > 0:
                id_isv = 1
            elif g18 > 0:
                id_isv = 2
            else:
                id_isv = 3
            items.append({
                "codigo": id_code,
                "descripcion": nombre,
                "precio": precio,
                "cantidad": cantidad,
                "id_isv": id_isv,
                "subtotal": subtotal
            })
        return jsonify({"header": header, "items": items})
    except Exception as e:
        try:
            connm.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500
@app.delete("/api/pedidos/<numero>")
def api_pedido_delete(numero):
    if conectar_mysql is None:
        return jsonify({"error":"MySQL no disponible"}), 503
    try:
        _asegurar_tablas_pedidos_mysql()
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("SELECT id_pedido FROM pedidos WHERE numero_pedido=%s", (numero,))
        r = cur.fetchone()
        if not r:
            connm.close()
            return jsonify({"error":"Pedido no encontrado"}), 404
        pid = int(r[0])
        # En lugar de borrar, marcar como generado para mantener histórico
        cur.execute("UPDATE pedidos SET estado=%s WHERE id_pedido=%s", ("generado", pid))
        connm.commit()
        connm.close()
        return jsonify({"ok": True})
    except Exception as e:
        try:
            connm.rollback()
        except Exception:
            pass
        try:
            connm.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

@app.post("/api/pedidos/<numero>/estado")
def api_pedido_cambiar_estado(numero):
    data = request.get_json(force=True) or {}
    nuevo = (data.get("estado") or "").strip().lower()
    if nuevo not in ("pendiente", "desactivado"):
        return jsonify({"error":"Estado inválido"}), 400
    if conectar_mysql is None:
        return jsonify({"error":"MySQL no disponible"}), 503
    try:
        _asegurar_tablas_pedidos_mysql()
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("SELECT id_pedido, estado FROM pedidos WHERE numero_pedido=%s", (numero,))
        r = cur.fetchone()
        if not r:
            connm.close()
            return jsonify({"error":"Pedido no encontrado"}), 404
        pid = int(r[0])
        estado_actual = str(r[1] or "").strip().lower()
        if estado_actual in ("generado", "cobrado"):
            connm.close()
            return jsonify({"error":"No se puede cambiar estado de pedido ya cobrado/generado"}), 400
        cur.execute("UPDATE pedidos SET estado=%s WHERE id_pedido=%s", (nuevo, pid))
        connm.commit()
        connm.close()
        return jsonify({"ok": True, "estado": nuevo})
    except Exception as e:
        try:
            connm.rollback()
        except Exception:
            pass
        try:
            connm.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

@app.post("/api/pedidos/<numero>/actualizar")
def api_pedido_actualizar(numero):
    data = request.get_json(force=True) or {}
    items = data.get("items", [])
    cliente_nombre = (data.get("cliente_nombre") or "")
    cliente_rtn = (data.get("cliente_rtn") or "")
    estado = (data.get("estado") or "").strip()
    if conectar_mysql is None:
        return jsonify({"error":"MySQL no disponible"}), 503
    try:
        _asegurar_tablas_pedidos_mysql()
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("SELECT id_pedido FROM pedidos WHERE numero_pedido=%s", (numero,))
        r = cur.fetchone()
        if not r:
            connm.close()
            return jsonify({"error":"Pedido no encontrado"}), 404
        pid = int(r[0])
        cur.execute("DELETE FROM pedidos_detalle WHERE id_pedido=%s", (pid,))
        detalle = []
        totales = calcular_totales_detalle(items)
        for it in items:
            precio = float(it.get("precio") or 0)
            cantidad = float(it.get("cantidad") or 0)
            subtotal = precio * cantidad
            id_isv = int(it.get("id_isv",3))
            grav15=grav18=ex=iv15=iv18=0.0
            if id_isv == 3:
                ex = subtotal
            elif id_isv == 1:
                base = subtotal/1.15; grav15 = base; iv15 = base*0.15
            elif id_isv == 2:
                base = subtotal/1.18; grav18 = base; iv18 = base*0.18
            detalle.append((
                pid, numero, it.get("codigo") or "", it.get("descripcion") or "", precio, cantidad,
                subtotal, grav15, grav18, ex, iv15, iv18, (grav15+grav18+ex+iv15+iv18)
            ))
        if detalle:
            cur.executemany("""
                INSERT INTO pedidos_detalle
                (id_pedido, numero_pedido, id, nombre_articulo, valor_articulo, cantidad, subtotal, gravado15, gravado18, totalexento, isv15, isv18, grantotal)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, detalle)
        cur.execute("UPDATE pedidos SET cliente=%s, rtn_cliente=%s, total=%s WHERE id_pedido=%s", (cliente_nombre, cliente_rtn, float(totales.get("total",0)), pid))
        if estado:
            cur.execute("UPDATE pedidos SET estado=%s WHERE id_pedido=%s", (estado, pid))
        connm.commit()
        connm.close()
        return jsonify({"ok": True})
    except Exception as e:
        try:
            connm.rollback()
        except Exception:
            pass
        try:
            connm.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500
@app.route("/agregar-producto-foto")
def agregar_producto_foto():
    return render_template("agregar_producto.html")

@app.route("/agregar-producto")
@app.route("/agregar_producto")
@app.route("/agregar-producto.html")
@app.route("/agregar_producto.html")
def agregar_producto_alias():
    return render_template("agregar_producto.html")

@app.route("/editar-producto")
def editar_producto_view():
    producto = None
    pid = request.args.get("id")
    codigo = request.args.get("codigo")
    # Preferir MySQL
    if conectar_mysql is not None:
        try:
            if asegurar_tablas_mysql is not None:
                try:
                    asegurar_tablas_mysql()
                except Exception:
                    pass
            connm = conectar_mysql()
            cur = connm.cursor()
            if pid:
                cur.execute("SELECT id, barra, nombre, precio, id_isv, stock, pesable, id_categoria FROM inventario WHERE id = %s", (pid,))
            else:
                cur.execute("SELECT id, barra, nombre, precio, id_isv, stock, pesable, id_categoria FROM inventario WHERE barra = %s", (codigo,))
            r = cur.fetchone()
            connm.close()
            if r:
                producto = {
                    "id": int(r[0]) if r[0] is not None else None,
                    "codigo": str(r[1] or ""),
                    "nombre": r[2] or "",
                    "precio": float(r[3]) if r[3] is not None else None,
                    "id_isv": int(r[4]) if r[4] is not None else 1,
                    "stock": int(r[5]) if r[5] is not None else 0,
                    "pesable": int(r[6]) if len(r) > 6 and r[6] is not None else 0,
                    "id_categoria": int(r[7]) if len(r) > 7 and r[7] is not None else None
                }
        except Exception:
            producto = None
    # Fallback SQLite
    if producto is None and (pid or codigo):
        row = None
        if pid:
            row = query_one("SELECT id, barra, nombre, precio, id_isv, stock, id_categoria FROM inventario WHERE id = ?", (pid,))
        else:
            row = query_one("SELECT id, barra, nombre, precio, id_isv, stock, id_categoria FROM inventario WHERE barra = ?", (codigo,))
        if row:
            producto = {
                "id": row.get("id"),
                "codigo": row.get("barra") or "",
                "nombre": row.get("nombre") or "",
                "precio": row.get("precio"),
                "id_isv": row.get("id_isv") or 1,
                "stock": row.get("stock") or 0,
                "pesable": 0,
                "id_categoria": row.get("id_categoria")
            }
    return render_template("agregar_producto.html", modo="editar", producto=producto)

@app.route("/agregar-producto-scanner")
def agregar_producto_scanner():
    return render_template("agregar_producto_scanner.html")

@app.route("/importar-productos")
def importar_productos_view():
    return render_template("importar_productos.html")

@app.route("/productos")
def productos_view():
    return render_template("productos.html")

@app.route("/clientes")
def clientes_view():
    try:
        execute("CREATE TABLE IF NOT EXISTS clientes (id_cliente INTEGER PRIMARY KEY AUTOINCREMENT, rtn TEXT, nombre TEXT NOT NULL)")
    except Exception:
        pass
    return render_template("clientes.html")

@app.route("/ventas")
def ventas_view():
    return render_template("index.html", usuario=session.get("usuario"))

@app.route("/pedidos")
def pedidos_view():
    return render_template("pedidos.html")

@app.route("/menu")
def menu_principal_view():
    if not session.get("usuario"):
        return redirect(url_for("login_view", next=request.path))
    return render_template("menu_principal.html", usuario=session.get("usuario"))

@app.route("/dashboard")
def dashboard_view():
    return render_template("dashboard.html")

TAREAS_FILE = os.path.join(PARENT_DIR, "datos", "tareas.json")

def _parse_autorizacion_pdf(path=None):
    if not path:
        path = os.path.join(PARENT_DIR, "autorizacion.pdf")
    if not os.path.exists(path):
        return None
    text = ""
    try:
        from PyPDF2 import PdfReader
        with open(path, "rb") as f:
            reader = PdfReader(f)
            for p in reader.pages:
                t = p.extract_text() or ""
                text += "\n" + t
    except Exception:
        return None
    cai = None
    m_cai = re.search(r"\b[A-Z0-9]{6}(?:-[A-Z0-9]{6}){4}-[A-Z0-9]{2}\b", text, re.IGNORECASE)
    if m_cai:
        cai = m_cai.group(0).upper()
    rangos = re.findall(r"\b\d{3}-\d{3}-\d{2}-\d{8}\b", text)
    rango_inicial = None
    rango_final = None
    if len(rangos) >= 2:
        rango_inicial = rangos[0]
        rango_final = rangos[1]
    elif len(rangos) == 1:
        rango_inicial = rangos[0]
    if not rango_final:
        try:
            txt = re.sub(r"\s+", " ", text)
            m = re.search(r"/\s*(\d{3}\s*-\s*\d{3}\s*-\s*\d{2}\s*-\s*(\d{8}))", txt)
            if m:
                rango_final = m.group(2)
        except Exception:
            pass
    fecha_limite = None
    m_fl = re.search(r"fecha.*l[íi]mite.*?(\d{1,2}/\d{1,2}/\d{4})", text, re.IGNORECASE | re.DOTALL)
    if not m_fl:
        m_fl = re.search(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", text)
    if m_fl:
        try:
            d = datetime.strptime(m_fl.group(1), "%d/%m/%Y")
            fecha_limite = d.strftime("%Y-%m-%d")
        except Exception:
            fecha_limite = None
    fecha_solicitud = None
    m_fs = re.search(r"fecha\s*de\s*solicitud.*?(\d{1,2}/\d{1,2}/\d{4})", text, re.IGNORECASE | re.DOTALL)
    if not m_fs:
        m_fs = re.search(r"solicitud.*?(\d{1,2}/\d{1,2}/\d{4})", text, re.IGNORECASE | re.DOTALL)
    if not m_fs:
        all_dates = re.findall(r"\b(\d{1,2}/\d{1,2}/\d{4})\b", text)
        if all_dates:
            try:
                fecha_solicitud = datetime.strptime(all_dates[-1], "%d/%m/%Y").strftime("%Y-%m-%d")
            except Exception:
                fecha_solicitud = None
    if m_fs and not fecha_solicitud:
        try:
            fecha_solicitud = datetime.strptime(m_fs.group(1), "%d/%m/%Y").strftime("%Y-%m-%d")
        except Exception:
            fecha_solicitud = None
    est = None
    pem = None
    tip = None
    numdoc = None
    if rango_inicial:
        mseg = re.match(r"^(\d{3})-(\d{3})-(\d{2})-(\d{8})$", rango_inicial)
        if mseg:
            est = int(mseg.group(1))
            pem = int(mseg.group(2))
            tip = int(mseg.group(3))
            numdoc = int(mseg.group(4))
    data = {}
    if cai:
        data["cai"] = cai
    if fecha_limite:
        data["fecha_limite"] = fecha_limite
    if fecha_solicitud:
        data["fecha_solicitud"] = fecha_solicitud
    if rango_inicial:
        data["rango_inicial"] = rango_inicial
    if rango_final:
        data["rango_final"] = rango_final
    if est is not None:
        data["establecimiento"] = est
    if pem is not None:
        data["punto_emision"] = pem
    if tip is not None:
        data["tipo_doc"] = tip
    if numdoc is not None:
        data["numero_documento"] = numdoc
    return data if data else None

def load_tareas():
    if os.path.exists(TAREAS_FILE):
        try:
            with open(TAREAS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_tareas(tareas):
    os.makedirs(os.path.dirname(TAREAS_FILE), exist_ok=True)
    with open(TAREAS_FILE, "w", encoding="utf-8") as f:
        json.dump(tareas, f, ensure_ascii=False, indent=2)

@app.get("/api/tareas")
def api_get_tareas():
    fecha = request.args.get("fecha")
    if not fecha:
        return jsonify([])
    data = load_tareas()
    return jsonify(data.get(fecha, []))

@app.post("/api/tareas")
def api_add_tarea():
    req = request.get_json(force=True)
    fecha = req.get("fecha")
    texto = req.get("texto", "").strip()
    if not fecha or not texto:
        return jsonify({"error": "Fecha y texto requeridos"}), 400
    
    data = load_tareas()
    if fecha not in data:
        data[fecha] = []
    
    # Check duplicate? Desktop doesn't seem to enforce strict unique IDs but removes by text.
    data[fecha].append({"texto": texto, "completada": False})
    save_tareas(data)
    return jsonify({"ok": True})

@app.post("/api/tareas/toggle")
def api_toggle_tarea():
    req = request.get_json(force=True)
    fecha = req.get("fecha")
    texto = req.get("texto", "").strip()
    
    data = load_tareas()
    if fecha in data:
        for t in data[fecha]:
            if t["texto"] == texto:
                t["completada"] = not t.get("completada", False)
                save_tareas(data)
                return jsonify({"ok": True, "completada": t["completada"]})
    return jsonify({"error": "Tarea no encontrada"}), 404

@app.post("/api/tareas/delete")
def api_delete_tarea():
    req = request.get_json(force=True)
    fecha = req.get("fecha")
    texto = req.get("texto", "").strip()
    
    data = load_tareas()
    if fecha in data:
        initial_len = len(data[fecha])
        data[fecha] = [t for t in data[fecha] if t["texto"] != texto]
        if len(data[fecha]) < initial_len:
            save_tareas(data)
            return jsonify({"ok": True})
    return jsonify({"error": "Tarea no encontrada"}), 404


@app.route("/iconos/<path:filename>")
def servir_icono(filename):
    return send_from_directory(ICONOS_DIR, filename)

# --------- API ---------

@app.get("/api/clientes")
def api_clientes():
    data = query_all("SELECT id_cliente, rtn, nombre FROM clientes ORDER BY nombre")
    return jsonify(data)

@app.post("/api/clientes")
def api_clientes_crear():
    data = request.get_json(force=True) or {}
    nombre = (data.get("nombre") or "").strip()
    rtn = (data.get("rtn") or "").strip()
    if not nombre:
        return jsonify({"error":"Nombre requerido"}), 400
    try:
        execute("CREATE TABLE IF NOT EXISTS clientes (id_cliente INTEGER PRIMARY KEY AUTOINCREMENT, rtn TEXT, nombre TEXT NOT NULL)")
    except Exception:
        pass
    try:
        new_id = execute("INSERT INTO clientes (rtn, nombre) VALUES (?, ?)", (rtn if rtn else None, nombre))
        return jsonify({"ok": True, "id_cliente": int(new_id)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/api/productos/<int:pid>/activar")
def api_activar_producto(pid):
    if conectar_mysql is not None:
        try:
            if asegurar_tablas_mysql is not None:
                try:
                    asegurar_tablas_mysql()
                except Exception:
                    pass
            connm = conectar_mysql()
            cur = connm.cursor()
            codigo = request.args.get("codigo") or request.args.get("barra")
            cur.execute("UPDATE inventario SET activo = 1 WHERE id = %s", (pid,))
            afectados = cur.rowcount
            if afectados == 0 and codigo:
                try:
                    cur.execute("UPDATE inventario SET activo = 1 WHERE barra = %s", (codigo,))
                    afectados = cur.rowcount
                except Exception:
                    pass
            connm.commit()
            connm.close()
            if afectados > 0:
                return jsonify({"ok": True})
            else:
                return jsonify({"error": "Producto no encontrado"}), 404
        except Exception as e:
            return jsonify({"error": f"Error activando en MySQL: {str(e)}"}), 500
    try:
        codigo = request.args.get("codigo") or request.args.get("barra")
        with get_db() as conn:
            cur = conn.cursor()
            try:
                cur.execute("PRAGMA table_info(inventario)")
                cols = [r[1].lower() for r in cur.fetchall()]
                if 'activo' not in cols:
                    cur.execute("ALTER TABLE inventario ADD COLUMN activo INTEGER NOT NULL DEFAULT 1")
            except Exception:
                pass
            cur.execute("UPDATE inventario SET activo = 1 WHERE id = ?", (pid,))
            afectados = cur.rowcount
            if afectados == 0 and codigo:
                try:
                    cur.execute("UPDATE inventario SET activo = 1 WHERE barra = ?", (codigo,))
                    afectados = cur.rowcount
                except Exception:
                    pass
            conn.commit()
            if afectados > 0:
                return jsonify({"ok": True})
            return jsonify({"error": "Producto no encontrado"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.put("/api/clientes/<int:cid>")
def api_clientes_actualizar(cid):
    data = request.get_json(force=True) or {}
    nombre = (data.get("nombre") or "").strip()
    rtn = (data.get("rtn") or "").strip()
    if not nombre:
        return jsonify({"error":"Nombre requerido"}), 400
    try:
        execute("UPDATE clientes SET nombre = ?, rtn = ? WHERE id_cliente = ?", (nombre, rtn if rtn else None, cid))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.delete("/api/clientes/<int:cid>")
def api_clientes_eliminar(cid):
    try:
        execute("DELETE FROM clientes WHERE id_cliente = ?", (cid,))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/api/categorias")
def api_categorias():
    if conectar_mysql is not None:
        try:
            connm = conectar_mysql()
            cur = connm.cursor()
            rows = None
            try:
                cur.execute("SELECT cod_categoria, nombre FROM categorias ORDER BY nombre")
                rows = cur.fetchall()
                data = [{"id": int(r[0]), "nombre": r[1]} for r in rows]
            except Exception:
                cur.execute("SELECT id, nombre FROM categorias ORDER BY nombre")
                rows = cur.fetchall()
                data = [{"id": int(r[0]), "nombre": r[1]} for r in rows]
            connm.close()
            return jsonify(data)
        except Exception:
            pass
    data = query_all("SELECT id, nombre FROM categorias ORDER BY nombre")
    return jsonify(data)
@app.post("/api/categorias")
def api_categorias_create():
    data = request.get_json(force=True) or {}
    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        return jsonify({"error": "Nombre requerido"}), 400
    if conectar_mysql is not None:
        try:
            connm = conectar_mysql()
            cur = connm.cursor()
            try:
                cur.execute("SHOW COLUMNS FROM categorias")
                cols = [r[0].lower() for r in cur.fetchall()]
            except Exception:
                cols = []
            if "cod_categoria" in cols and "id" not in cols:
                try:
                    cur.execute("SELECT MAX(cod_categoria) FROM categorias")
                    mx = cur.fetchone()[0]
                except Exception:
                    mx = None
                nuevo_id = int(mx) + 1 if mx is not None else 10001
                cur.execute("INSERT INTO categorias (cod_categoria, nombre) VALUES (%s, %s)", (nuevo_id, nombre))
            else:
                cur.execute("INSERT INTO categorias (nombre) VALUES (%s)", (nombre,))
            connm.commit()
            try:
                if "cod_categoria" in cols and "id" not in cols:
                    cur.execute("SELECT cod_categoria FROM categorias WHERE nombre=%s", (nombre,))
                    new_id = int(cur.fetchone()[0])
                else:
                    cur.execute("SELECT LAST_INSERT_ID()")
                    new_id = int(cur.fetchone()[0])
            except Exception:
                new_id = None
            connm.close()
            return jsonify({"ok": True, "id": new_id, "nombre": nombre})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    try:
        new_id = execute("INSERT INTO categorias (nombre) VALUES (?)", (nombre,))
        return jsonify({"ok": True, "id": new_id, "nombre": nombre})
    except sqlite3.IntegrityError:
        return jsonify({"error": "La categoría ya existe"}), 409
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.put("/api/categorias/<int:cid>")
def api_categorias_update(cid):
    data = request.get_json(force=True) or {}
    nombre = (data.get("nombre") or "").strip()
    if not nombre:
        return jsonify({"error": "Nombre requerido"}), 400
    if conectar_mysql is not None:
        try:
            connm = conectar_mysql()
            cur = connm.cursor()
            try:
                cur.execute("SHOW COLUMNS FROM categorias")
                cols = [r[0].lower() for r in cur.fetchall()]
            except Exception:
                cols = []
            if "cod_categoria" in cols and "id" not in cols:
                cur.execute("UPDATE categorias SET nombre=%s WHERE cod_categoria=%s", (nombre, cid))
            else:
                cur.execute("UPDATE categorias SET nombre=%s WHERE id=%s", (nombre, cid))
            connm.commit()
            connm.close()
            return jsonify({"ok": True, "id": cid, "nombre": nombre})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    try:
        execute("UPDATE categorias SET nombre=? WHERE id=?", (nombre, cid))
        return jsonify({"ok": True, "id": cid, "nombre": nombre})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/api/productos")
def api_productos():
    limit = 200
    offset = 0
    q = (request.args.get("q") or "").strip()
    estado = (request.args.get("estado") or "activos").strip().lower()
    cat_raw = (request.args.get("categoria_id") or request.args.get("categoria") or "").strip()
    cat_id = None
    try:
        cat_id = int(cat_raw) if cat_raw else None
    except Exception:
        cat_id = None
    try:
        l = int(request.args.get("limit", limit))
        if l > 0 and l <= 1000:
            limit = l
        o = int(request.args.get("offset", offset))
        if o >= 0:
            offset = o
    except Exception:
        pass
    where_sql = " WHERE 1=1 "
    if estado == "activos":
        where_sql += " AND (activo IS NULL OR activo = 1) "
    elif estado == "inactivos":
        where_sql += " AND activo = 0 "
    params = []
    if q:
        where_sql += " AND (nombre LIKE %s OR barra = %s) "
        params.extend([f"%{q}%", q])
    if cat_id is not None:
        where_sql += " AND id_categoria = %s "
        params.append(cat_id)
    if conectar_mysql is None:
        return jsonify({"error": "MySQL no disponible"}), 503
    # Preferir MySQL
    if conectar_mysql is not None:
        try:
            connm = conectar_mysql()
            cur = connm.cursor()

            # Count total
            count_sql = f"SELECT COUNT(*) FROM inventario{where_sql}"
            cur.execute(count_sql, tuple(params))
            total_count = cur.fetchone()[0]

            sql = f"SELECT id, barra, nombre, precio, id_isv, stock, pesable, id_categoria FROM inventario{where_sql} LIMIT %s OFFSET %s"
            cur.execute(sql, (*params, limit, offset) if params else (limit, offset))
            rows = cur.fetchall()

            if q and (not rows or len(rows) == 0):
                try:
                    cur.execute("SELECT producto_id FROM inventario_barras WHERE barra = %s", (q,))
                    pidrow = cur.fetchone()
                    if pidrow:
                        if estado == "activos":
                            if cat_id is not None:
                                cur.execute("SELECT id, barra, nombre, precio, id_isv, stock, pesable, id_categoria FROM inventario WHERE (activo IS NULL OR activo = 1) AND id = %s AND id_categoria = %s LIMIT %s OFFSET %s", (int(pidrow[0]), cat_id, limit, offset))
                            else:
                                cur.execute("SELECT id, barra, nombre, precio, id_isv, stock, pesable, id_categoria FROM inventario WHERE (activo IS NULL OR activo = 1) AND id = %s LIMIT %s OFFSET %s", (int(pidrow[0]), limit, offset))
                        elif estado == "inactivos":
                            if cat_id is not None:
                                cur.execute("SELECT id, barra, nombre, precio, id_isv, stock, pesable, id_categoria FROM inventario WHERE activo = 0 AND id = %s AND id_categoria = %s LIMIT %s OFFSET %s", (int(pidrow[0]), cat_id, limit, offset))
                            else:
                                cur.execute("SELECT id, barra, nombre, precio, id_isv, stock, pesable, id_categoria FROM inventario WHERE activo = 0 AND id = %s LIMIT %s OFFSET %s", (int(pidrow[0]), limit, offset))
                        else:
                            if cat_id is not None:
                                cur.execute("SELECT id, barra, nombre, precio, id_isv, stock, pesable, id_categoria FROM inventario WHERE id = %s AND id_categoria = %s LIMIT %s OFFSET %s", (int(pidrow[0]), cat_id, limit, offset))
                            else:
                                cur.execute("SELECT id, barra, nombre, precio, id_isv, stock, pesable, id_categoria FROM inventario WHERE id = %s LIMIT %s OFFSET %s", (int(pidrow[0]), limit, offset))
                        rows = cur.fetchall()
                        if rows:
                            total_count = 1
                except Exception:
                    pass
            connm.close()
            data = []
            for r in rows:
                data.append({
                    "id": int(r[0]) if r[0] is not None else None,
                    "codigo": str(r[1] or ""),
                    "nombre": r[2],
                    "precio": float(r[3]) if r[3] is not None else None,
                    "id_isv": int(r[4]) if r[4] is not None else None,
                    "stock": int(r[5]) if r[5] is not None else None,
                    "pesable": int(r[6]) if len(r) > 6 and r[6] is not None else None,
                    "id_categoria": int(r[7]) if len(r) > 7 and r[7] is not None else None
                })
            res = jsonify(data)
            res.headers["X-Total-Count"] = str(total_count)
            return res
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    return jsonify([])

@app.get("/api/producto/<codigo>")
def api_producto(codigo):
    # Busca por barra (código) preferentemente; 6 dígitos se consideran código único
    if conectar_mysql is not None:
        try:
            if asegurar_tablas_mysql is not None:
                try:
                    asegurar_tablas_mysql()
                except Exception:
                    pass
            connm = conectar_mysql()
            cur = connm.cursor()
            codigo_str = str(codigo).strip()
            # 1) Buscar por barra exacta
            cur.execute("SELECT id, barra, nombre, precio, id_isv, stock, pesable, id_categoria FROM inventario WHERE (activo IS NULL OR activo = 1) AND barra = %s", (codigo_str,))
            r = cur.fetchone()
            if not r:
                try:
                    cur.execute("SELECT producto_id FROM inventario_barras WHERE barra = %s", (codigo_str,))
                    rr = cur.fetchone()
                    if rr:
                        cur.execute("SELECT id, barra, nombre, precio, id_isv, stock, pesable, id_categoria FROM inventario WHERE (activo IS NULL OR activo = 1) AND id = %s", (int(rr[0]),))
                        r = cur.fetchone()
                except Exception:
                    pass
            # 2) Buscar por id si el código es de 6 dígitos (000123 -> id=123)
            if not r and codigo_str.isdigit() and len(codigo_str) == 6:
                try:
                    cur.execute("SELECT id, barra, nombre, precio, id_isv, stock, pesable, id_categoria FROM inventario WHERE (activo IS NULL OR activo = 1) AND id = %s", (int(codigo_str),))
                    r = cur.fetchone()
                except Exception:
                    r = None
            connm.close()
            if r:
                return jsonify({
                    "id": int(r[0]) if r[0] is not None else None,
                    "codigo": str(r[1] or ""),
                    "nombre": r[2],
                    "precio": float(r[3]) if r[3] is not None else None,
                    "id_isv": int(r[4]) if r[4] is not None else None,
                    "stock": int(r[5]) if r[5] is not None else None,
                    "pesable": int(r[6]) if len(r) > 6 and r[6] is not None else None,
                    "id_categoria": int(r[7]) if len(r) > 7 and r[7] is not None else None
                })
        except Exception:
            pass
    # Fallback SQLite
    codigo_str = str(codigo).strip()
    # 1) Buscar por barra exacta con detección de columnas opcionales
    try:
        with get_db() as conn:
            cur = conn.execute("PRAGMA table_info(inventario)")
            cols = [r[1].lower() for r in cur.fetchall()]
            has_activo = ("activo" in cols)
            has_id_categoria = ("id_categoria" in cols)
            select_cols = "id AS id, barra AS codigo, nombre, precio, id_isv" + (", id_categoria" if has_id_categoria else "")
            where_activo = " AND (activo IS NULL OR activo = 1)" if has_activo else ""
            # barra
            sql = f"SELECT {select_cols} FROM inventario WHERE 1=1{where_activo} AND barra=?"
            cur = conn.execute(sql, (codigo_str,))
            r = cur.fetchone()
            row = dict(r) if r else None
            # fallback inventario_barras
            if not row:
                try:
                    cur = conn.execute("SELECT producto_id FROM inventario_barras WHERE barra = ?", (codigo_str,))
                    rr = cur.fetchone()
                    if rr:
                        pid = int(rr[0])
                        sql2 = f"SELECT {select_cols} FROM inventario WHERE 1=1{where_activo} AND id=?"
                        cur = conn.execute(sql2, (pid,))
                        r2 = cur.fetchone()
                        row = dict(r2) if r2 else None
                except Exception:
                    row = None
            # Buscar por id si el código es de 6 dígitos (000123 -> id=123)
            if not row and codigo_str.isdigit() and len(codigo_str) == 6:
                try:
                    pid6 = int(codigo_str)
                    sql3 = f"SELECT {select_cols} FROM inventario WHERE 1=1{where_activo} AND id=?"
                    cur = conn.execute(sql3, (pid6,))
                    r3 = cur.fetchone()
                    row = dict(r3) if r3 else None
                except Exception:
                    row = None
    except Exception:
        row = None
    if row:
        return jsonify(row)
    return jsonify({"error":"Producto no encontrado"}), 404

@app.post("/api/productos")
def api_crear_producto():
    data = request.get_json(force=True) or {}
    nombre = (data.get("nombre") or "").strip()
    barra = (data.get("barra") or None)
    id_categoria = data.get("id_categoria")
    try:
        pesable = int(data.get("pesable", 0))
    except Exception:
        pesable = 0
    try:
        precio = float(data.get("precio", 0))
    except Exception:
        precio = 0.0
    try:
        id_isv = int(data.get("id_isv", 3))
    except Exception:
        id_isv = 3
    try:
        stock = int(data.get("stock", 100))
    except Exception:
        stock = 100

    if not nombre or precio <= 0:
        return jsonify({"error":"Nombre y precio son obligatorios"}), 400

    # Preferir MySQL
    if conectar_mysql is not None:
        try:
            if asegurar_tablas_mysql is not None:
                try:
                    asegurar_tablas_mysql()
                except Exception:
                    pass
            connm = conectar_mysql()
            cur = connm.cursor()
            # Validar código de barras único en MySQL
            if barra:
                try:
                    cur.execute("SELECT id FROM inventario WHERE barra = %s", (barra,))
                    dup = cur.fetchone()
                    if dup:
                        connm.close()
                        return jsonify({"error":"El código de barras ya existe"}), 409
                except Exception:
                    pass
            if barra:
                try:
                    cur.execute("SELECT producto_id FROM inventario_barras WHERE barra = %s", (barra,))
                    r = cur.fetchone()
                    if r:
                        connm.close()
                        return jsonify({"error":"El código de barras ya existe"}), 409
                except Exception:
                    pass
            # ID autogenerado: si tu tabla usa AUTO_INCREMENT, omitimos id
            if id_categoria is not None:
                cur.execute(
                    "INSERT INTO inventario (barra, nombre, precio, id_isv, stock, pesable, id_categoria) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (barra, nombre, precio, id_isv, stock, pesable, int(id_categoria))
                )
            else:
                cur.execute(
                    "INSERT INTO inventario (barra, nombre, precio, id_isv, stock, pesable) VALUES (%s, %s, %s, %s, %s, %s)",
                    (barra, nombre, precio, id_isv, stock, pesable)
                )
            connm.commit()
            try:
                cur.execute("SELECT LAST_INSERT_ID()")
                new_id = cur.fetchone()[0]
            except Exception:
                new_id = None
            try:
                auto_barra = None
                if not barra or str(barra).strip() == "":
                    try:
                        auto_barra = str(int(new_id or 0)).zfill(6)
                        cur.execute("UPDATE inventario SET barra = %s WHERE id = %s", (auto_barra, int(new_id or 0)))
                        connm.commit()
                    except Exception:
                        auto_barra = None
                final_barra = barra if (barra and str(barra).strip() != "") else auto_barra
                if final_barra:
                    try:
                        cur.execute("INSERT INTO inventario_barras (producto_id, barra) VALUES (%s, %s)", (int(new_id or 0), final_barra))
                        connm.commit()
                    except Exception:
                        pass
            except Exception:
                pass
            connm.close()
            return jsonify({"ok": True, "id": new_id})
        except Exception as e:
            # Si ya existe barra, reportar conflicto
            return jsonify({"error": f"No se pudo crear en MySQL: {str(e)}"}), 500

    # Fallback a SQLite
    try:
        # Validar código de barras único en SQLite
        if barra:
            r = query_one("SELECT id FROM inventario WHERE barra = ?", (barra,))
            if r:
                return jsonify({"error":"El código de barras ya existe"}), 409
            r2 = query_one("SELECT producto_id FROM inventario_barras WHERE barra = ?", (barra,))
            if r2:
                return jsonify({"error":"El código de barras ya existe"}), 409
        if id_categoria is not None:
            new_id = execute(
                "INSERT INTO inventario (barra, nombre, precio, id_isv, stock, pesable, id_categoria) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (barra, nombre, precio, id_isv, stock, pesable, int(id_categoria))
            )
        else:
            new_id = execute(
                "INSERT INTO inventario (barra, nombre, precio, id_isv, stock, pesable) VALUES (?, ?, ?, ?, ?, ?)",
                (barra, nombre, precio, id_isv, stock, pesable)
            )
        try:
            auto_barra = None
            if not barra or str(barra).strip() == "":
                try:
                    auto_barra = str(int(new_id or 0)).zfill(6)
                    execute("UPDATE inventario SET barra = ? WHERE id = ?", (auto_barra, int(new_id or 0)))
                except Exception:
                    auto_barra = None
            final_barra = barra if (barra and str(barra).strip() != "") else auto_barra
            if final_barra:
                try:
                    execute("INSERT INTO inventario_barras (producto_id, barra) VALUES (?, ?)", (int(new_id or 0), final_barra))
                except Exception:
                    pass
        except Exception:
            pass
        return jsonify({"ok": True, "id": new_id})
    except sqlite3.IntegrityError:
        return jsonify({"error":"El código de barras ya existe"}), 409
    except Exception as e:
        return jsonify({"error":"No se pudo crear el producto"}), 500

@app.post("/api/productos/<int:pid>")
def api_actualizar_producto(pid):
    data = request.get_json(force=True) or {}
    nombre = (data.get("nombre") or "").strip()
    barra = (data.get("barra") or None)
    id_categoria = data.get("id_categoria")
    try:
        pesable = int(data.get("pesable", 0))
    except Exception:
        pesable = 0
    try:
        precio = float(data.get("precio", 0))
    except Exception:
        precio = 0.0
    try:
        id_isv = int(data.get("id_isv", 3))
    except Exception:
        id_isv = 3
    try:
        stock = int(data.get("stock", 100))
    except Exception:
        stock = 100
    # Preferir MySQL
    if conectar_mysql is not None:
        try:
            if asegurar_tablas_mysql is not None:
                try:
                    asegurar_tablas_mysql()
                except Exception:
                    pass
            connm = conectar_mysql()
            cur = connm.cursor()
            # Validar duplicado de barra en otro producto (MySQL)
            if barra:
                try:
                    cur.execute("SELECT id FROM inventario WHERE barra = %s AND id <> %s", (barra, pid))
                    dup = cur.fetchone()
                    if dup:
                        connm.close()
                        return jsonify({"error":"El código de barras ya existe en otro producto"}), 409
                except Exception:
                    pass
                try:
                    cur.execute("SELECT producto_id FROM inventario_barras WHERE barra = %s AND producto_id <> %s", (barra, pid))
                    dup2 = cur.fetchone()
                    if dup2:
                        connm.close()
                        return jsonify({"error":"El código de barras ya existe en otro producto"}), 409
                except Exception:
                    pass
            if id_categoria is not None:
                cur.execute(
                    "UPDATE inventario SET barra=%s, nombre=%s, precio=%s, id_isv=%s, stock=%s, pesable=%s, id_categoria=%s WHERE id=%s",
                    (barra, nombre, precio, id_isv, stock, pesable, int(id_categoria), pid)
                )
            else:
                cur.execute(
                    "UPDATE inventario SET barra=%s, nombre=%s, precio=%s, id_isv=%s, stock=%s, pesable=%s WHERE id=%s",
                    (barra, nombre, precio, id_isv, stock, pesable, pid)
                )
            try:
                if barra:
                    cur.execute("INSERT INTO inventario_barras (producto_id, barra) VALUES (%s, %s)", (pid, barra))
            except Exception:
                pass
            try:
                if not barra or str(barra).strip() == "":
                    auto_barra = str(int(pid)).zfill(6)
                    try:
                        cur.execute("UPDATE inventario SET barra = %s WHERE id = %s", (auto_barra, pid))
                        connm.commit()
                    except Exception:
                        pass
                    try:
                        cur.execute("INSERT INTO inventario_barras (producto_id, barra) VALUES (%s, %s)", (pid, auto_barra))
                        connm.commit()
                    except Exception:
                        pass
            except Exception:
                pass
            connm.commit()
            connm.close()
            return jsonify({"ok": True, "id": pid})
        except Exception as e:
            return jsonify({"error": f"No se pudo actualizar en MySQL: {str(e)}"}), 500
    # Fallback SQLite
    try:
        # Validar duplicado de barra en otro producto (SQLite)
        if barra:
            r = query_one("SELECT id FROM inventario WHERE barra = ? AND id <> ?", (barra, pid))
            if r:
                return jsonify({"error":"El código de barras ya existe en otro producto"}), 409
            r2 = query_one("SELECT producto_id FROM inventario_barras WHERE barra = ? AND producto_id <> ?", (barra, pid))
            if r2:
                return jsonify({"error":"El código de barras ya existe en otro producto"}), 409
        if id_categoria is not None:
            execute(
                "UPDATE inventario SET barra=?, nombre=?, precio=?, id_isv=?, stock=?, pesable=?, id_categoria=? WHERE id=?",
                (barra, nombre, precio, id_isv, stock, pesable, int(id_categoria), pid)
            )
        else:
            execute(
                "UPDATE inventario SET barra=?, nombre=?, precio=?, id_isv=?, stock=?, pesable=? WHERE id=?",
                (barra, nombre, precio, id_isv, stock, pesable, pid)
            )
        try:
            if barra:
                execute("INSERT INTO inventario_barras (producto_id, barra) VALUES (?, ?)", (pid, barra))
        except Exception:
            pass
        try:
            if not barra or str(barra).strip() == "":
                auto_barra = str(int(pid)).zfill(6)
                try:
                    execute("UPDATE inventario SET barra = ? WHERE id = ?", (auto_barra, pid))
                except Exception:
                    pass
                try:
                    execute("INSERT INTO inventario_barras (producto_id, barra) VALUES (?, ?)", (pid, auto_barra))
                except Exception:
                    pass
        except Exception:
            pass
        return jsonify({"ok": True, "id": pid})
    except Exception:
        return jsonify({"error": "No se pudo actualizar producto"}), 500

@app.get("/api/productos/<int:pid>/barras")
def api_productos_barras_list(pid):
    barras = []
    # MySQL
    if conectar_mysql is not None:
        try:
            connm = conectar_mysql()
            cur = connm.cursor()
            try:
                cur.execute("SELECT barra FROM inventario WHERE id = %s", (pid,))
                r = cur.fetchone()
                if r and r[0]:
                    barras.append(str(r[0]))
            except Exception:
                pass
            try:
                cur.execute("SELECT barra FROM inventario_barras WHERE producto_id = %s", (pid,))
                for br in cur.fetchall():
                    if br and br[0]:
                        barras.append(str(br[0]))
            except Exception:
                pass
            connm.close()
            return jsonify(sorted(list(set(barras))))
        except Exception:
            pass
    # SQLite
    try:
        row = query_one("SELECT barra FROM inventario WHERE id = ?", (pid,))
        if row and row.get("barra"):
            barras.append(str(row.get("barra")))
        for r in query_all("SELECT barra FROM inventario_barras WHERE producto_id = ?", (pid,)):
            if r.get("barra"):
                barras.append(str(r.get("barra")))
    except Exception:
        pass
    return jsonify(sorted(list(set(barras))))

@app.post("/api/productos/<int:pid>/barras")
def api_productos_barras_add(pid):
    data = request.get_json(force=True) or {}
    barra = (data.get("barra") or "").strip()
    if not barra:
        return jsonify({"error": "Barra requerida"}), 400
    # MySQL
    if conectar_mysql is not None:
        try:
            connm = conectar_mysql()
            cur = connm.cursor()
            try:
                cur.execute("SELECT id FROM inventario WHERE barra = %s AND id <> %s", (barra, pid))
                dup = cur.fetchone()
                if dup:
                    connm.close()
                    return jsonify({"error":"El código de barras ya existe en otro producto"}), 409
            except Exception:
                pass
            try:
                cur.execute("SELECT producto_id FROM inventario_barras WHERE barra = %s AND producto_id <> %s", (barra, pid))
                dup2 = cur.fetchone()
                if dup2:
                    connm.close()
                    return jsonify({"error":"El código de barras ya existe en otro producto"}), 409
            except Exception:
                pass
            try:
                cur.execute("INSERT INTO inventario_barras (producto_id, barra) VALUES (%s, %s)", (pid, barra))
                connm.commit()
            except Exception as e:
                connm.close()
                return jsonify({"error": str(e)}), 500
            connm.close()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    # SQLite
    try:
        r = query_one("SELECT id FROM inventario WHERE barra = ? AND id <> ?", (barra, pid))
        if r:
            return jsonify({"error":"El código de barras ya existe en otro producto"}), 409
        r2 = query_one("SELECT producto_id FROM inventario_barras WHERE barra = ? AND producto_id <> ?", (barra, pid))
        if r2:
            return jsonify({"error":"El código de barras ya existe en otro producto"}), 409
        execute("INSERT INTO inventario_barras (producto_id, barra) VALUES (?, ?)", (pid, barra))
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.delete("/api/productos/<int:pid>/barras/<barra>")
def api_productos_barras_delete(pid, barra):
    # MySQL
    if conectar_mysql is not None:
        try:
            connm = conectar_mysql()
            cur = connm.cursor()
            cur.execute("DELETE FROM inventario_barras WHERE producto_id = %s AND barra = %s", (pid, barra))
            connm.commit()
            connm.close()
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    # SQLite
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM inventario_barras WHERE producto_id = ? AND barra = ?", (pid, barra))
            conn.commit()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
@app.delete("/api/productos/<int:pid>")
def api_eliminar_producto(pid):
    # Preferir MySQL
    if conectar_mysql is not None:
        try:
            if asegurar_tablas_mysql is not None:
                try:
                    asegurar_tablas_mysql()
                except Exception:
                    pass
            connm = conectar_mysql()
            cur = connm.cursor()
            codigo = request.args.get("codigo") or request.args.get("barra")
            cur.execute("UPDATE inventario SET activo = 0 WHERE id = %s", (pid,))
            eliminados = cur.rowcount
            if eliminados == 0 and codigo:
                try:
                    cur.execute("UPDATE inventario SET activo = 0 WHERE barra = %s", (codigo,))
                    eliminados = cur.rowcount
                except Exception:
                    pass
            # No borrar inventario_barras para preservar trazabilidad
            connm.commit()
            connm.close()
            if eliminados > 0:
                return jsonify({"ok": True})
            else:
                return jsonify({"error": "Producto no encontrado"}), 404
        except Exception as e:
            return jsonify({"error": f"Error eliminando en MySQL: {str(e)}"}), 500

    # Fallback SQLite
    try:
        codigo = request.args.get("codigo") or request.args.get("barra")
        with get_db() as conn:
            cur = conn.cursor()
            # Soft-delete: marcar inactivo
            # Asegurar columna activo existe en SQLite
            try:
                cur.execute("PRAGMA table_info(inventario)")
                cols = [r[1].lower() for r in cur.fetchall()]
                if 'activo' not in cols:
                    cur.execute("ALTER TABLE inventario ADD COLUMN activo INTEGER NOT NULL DEFAULT 1")
            except Exception:
                pass
            cur.execute("UPDATE inventario SET activo = 0 WHERE id = ?", (pid,))
            eliminados = cur.rowcount
            if eliminados == 0 and codigo:
                try:
                    cur.execute("UPDATE inventario SET activo = 0 WHERE barra = ?", (codigo,))
                    eliminados = cur.rowcount
                except Exception:
                    pass
            # No borrar inventario_barras
            conn.commit()
            if eliminados > 0:
                return jsonify({"ok": True})
            return jsonify({"error": "Producto no encontrado"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/api/registrar-venta")
def api_registrar_venta():
    """
    Recibe JSON:
    {
      "cliente_id": 1 | null,
      "cliente_nombre": "texto",
      "items": [ { "codigo": "123", "descripcion": "X", "precio": 10.0, "cantidad": 2, "id_isv": 1 }, ... ],
      "pago": { "efectivo": 100.0 }
    }
    """
    data = request.get_json(force=True)
    items = data.get("items", [])
    if not items:
        return jsonify({"error":"Sin items"}), 400

    totales = calcular_totales_detalle(items)
    tipo_req = 'G'
    try:
        t_ex = float(totales.get('exento', 0) or 0)
        t_g15 = float(totales.get('gravado15', 0) or 0)
        t_g18 = float(totales.get('gravado18', 0) or 0)
        if t_ex > 0 and t_g15 == 0 and t_g18 == 0:
            tipo_req = 'E'
    except Exception:
        pass

    # Preferir MySQL para registrar venta completa
    if conectar_mysql is not None:
        try:
            if asegurar_tablas_mysql is not None:
                try:
                    asegurar_tablas_mysql()
                except Exception:
                    pass
            connm = conectar_mysql()
            curm = connm.cursor()
            numero_factura = None
            cai = None
            try:
                _asegurar_tablas_cai_separadas_mysql()
                # Buscar CAI activo del tipo requerido
                tbl = _tabla_cai(tipo_req)
                curm.execute(f"""
                    SELECT cai, establecimiento, punto_emision, tipo_doc, numero_documento
                    FROM {tbl}
                    WHERE activo=1
                    ORDER BY id DESC
                    LIMIT 1
                """)
                r = curm.fetchone()
                
                # Si no encuentra del tipo especifico, buscar cualquiera activo (fallback)
                if not r:
                    curm.execute("""
                        SELECT cai, establecimiento, punto_emision, tipo_doc, numero_documento
                        FROM info_cai_general
                        WHERE activo=1
                        ORDER BY id DESC
                        LIMIT 1
                    """)
                    r = curm.fetchone()
                
                # Fallback final: cualquier CAI (incluso inactivo si no hay activos? No, mejor solo activos)
                if not r:
                    curm.execute("""
                        SELECT cai, establecimiento, punto_emision, tipo_doc, numero_documento
                        FROM info_cai_exenta
                        WHERE activo=1
                        ORDER BY id DESC
                        LIMIT 1
                    """)
                    r = curm.fetchone()
                rangoi = rangof = flim = None
                if r:
                    cai, est, pem, tip, num = r
                    try:
                        ndoc = int(num or 0) + 1
                    except Exception:
                        ndoc = 1
                    try:
                        numero_factura = f"{int(est):03d}-{int(pem):03d}-{int(tip):02d}-{int(ndoc):08d}"
                    except Exception:
                        numero_factura = f"000-000-00-{int(ndoc):08d}"
                    # Validaciones adicionales: fecha límite y rango
                    try:
                        curm.execute(f"SELECT rango_i, rango_f, f_limite FROM {tbl} WHERE cai=%s AND establecimiento=%s AND punto_emision=%s AND tipo_doc=%s", (cai, est, pem, tip))
                        r2 = curm.fetchone()
                        if r2:
                            rangoi, rangof, flim = r2
                    except Exception:
                        pass
                    # Fecha límite
                    try:
                        if flim:
                            expired = False
                            # Caso 1: Entero (Julian)
                            if isinstance(flim, int):
                                now_j = _to_julian(datetime.now().strftime("%Y-%m-%d"))
                                if now_j and now_j > flim:
                                    expired = True
                            else:
                                s_flim = str(flim).strip()
                                # Caso 2: String numérico (Julian en varchar)
                                if s_flim.isdigit() and len(s_flim) > 6:
                                    now_j = _to_julian(datetime.now().strftime("%Y-%m-%d"))
                                    if now_j and now_j > int(s_flim):
                                        expired = True
                                else:
                                    # Caso 3: String fecha
                                    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d"):
                                        try:
                                            limite = datetime.strptime(s_flim, fmt)
                                            if datetime.now() > limite:
                                                expired = True
                                            break
                                        except Exception:
                                            continue
                            
                            if expired:
                                try:
                                    connm.close()
                                except Exception:
                                    pass
                                return jsonify({"error": f"El CAI venció ({flim})"}), 400
                    except Exception:
                        pass
                    # Rango
                    try:
                        if rangoi is not None and rangof is not None:
                            if not (int(rangoi) <= int(ndoc) <= int(rangof)):
                                try:
                                    connm.close()
                                except Exception:
                                    pass
                                return jsonify({"error": f"Número fuera de rango ({rangoi} - {rangof})"}), 400
                    except Exception:
                        pass
                else:
                    numero_factura = None
            except Exception:
                pass
            # Validar stock y descontar en inventario (MySQL)
            for it in items:
                codigo = it["codigo"]
                cant = int(it["cantidad"])
                curm.execute("SELECT stock, id FROM inventario WHERE id = %s OR barra = %s", (codigo, codigo))
                r = curm.fetchone()
                if not r:
                    connm.close()
                    return jsonify({"error": f"Producto {codigo} no existe"}), 400
                stock, id_real = int(r[0] or 0), int(r[1])
                if stock < cant:
                    connm.close()
                    return jsonify({"error": f"Stock insuficiente para {codigo} (disp: {stock})"}), 400
                curm.execute("UPDATE inventario SET stock = stock - %s WHERE id = %s", (cant, id_real))
            connm.commit()

            totales = calcular_totales_detalle(items)
            efectivo = float(data.get("pago",{}).get("efectivo", totales["total"]))
            cambio = round(efectivo - totales["total"], 2)

            # Insertar encabezado de venta en MySQL
            if asegurar_tabla_ventas_mysql is not None:
                try:
                    asegurar_tabla_ventas_mysql()
                except Exception:
                    pass
            usuario = ""
            metodo_pago = "Efectivo"
            cliente_nombre = data.get("cliente_nombre") or "CONSUMIDOR FINAL"
            cliente_rtn = (data.get("cliente_rtn") or "")
            try:
                rid = insertar_venta_encabezado_mysql(
                    mesa=None, mesero=None,
                    cliente=cliente_nombre,
                    rtn_cliente=cliente_rtn,
                    total=totales["total"],
                    fecha=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    numero_factura=numero_factura,
                    cai=cai or "",
                    exento=totales["exento"],
                    gravado15=totales["gravado15"],
                    gravado18=totales["gravado18"],
                    isv15=totales["isv15"],
                    isv18=totales["isv18"],
                    metodo_pago=metodo_pago,
                    efectivo=efectivo,
                    cambio=cambio,
                    usuario=usuario,
                    estado="emitida"
                )
                rid_sar = None
                if tipo_req != 'E' and insertar_sar_venta_encabezado_mysql is not None:
                    try:
                        if asegurar_tabla_sar_ventas_mysql is not None:
                            asegurar_tabla_sar_ventas_mysql()
                    except Exception:
                        pass
                    try:
                        rid_sar = insertar_sar_venta_encabezado_mysql(
                            mesa=None, mesero=None,
                            cliente=cliente_nombre,
                            rtn_cliente=cliente_rtn,
                            total=totales["total"],
                            fecha=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            numero_factura=numero_factura,
                            cai=cai or "",
                            exento=totales["exento"],
                            gravado15=totales["gravado15"],
                            gravado18=totales["gravado18"],
                            isv15=totales["isv15"],
                            isv18=totales["isv18"],
                            metodo_pago=metodo_pago,
                            efectivo=efectivo,
                            cambio=cambio,
                            usuario=usuario,
                            estado="emitida"
                        )
                    except Exception:
                        rid_sar = None
            except Exception:
                # Si no están disponibles los helpers, insertar mínimo detalle sin encabezado
                rid = None

            # Insertar detalle de venta en MySQL
            try:
                pedido_numero = str((data.get("pedido_numero") or "")).strip()
            except Exception:
                pedido_numero = ""
            if pedido_numero:
                try:
                    curm.execute("SELECT estado FROM pedidos WHERE numero_pedido=%s", (pedido_numero,))
                    pr = curm.fetchone()
                    if pr:
                        pest = str(pr[0] or "").strip().lower()
                        if pest in ("desactivado", "generado", "cobrado"):
                            return jsonify({"error":"No se puede facturar: pedido desactivado o ya cobrado"}), 400
                except Exception:
                    pass
            for it in items:
                subtotal = float(it["precio"]) * float(it["cantidad"])
                id_isv = int(it.get("id_isv",3))
                grav15=grav18=ex=iv15=iv18=0.0
                if id_isv == 3:
                    ex = subtotal
                elif id_isv == 1:
                    base = subtotal/1.15; grav15 = base; iv15 = base*0.15
                elif id_isv == 2:
                    base = subtotal/1.18; grav18 = base; iv18 = base*0.18
                try:
                    insertar_venta_detalle_mysql(
                        id_venta=rid,
                        numero_factura=str(numero_factura or rid or ""),
                        codigo=it["codigo"],
                        nombre=it["descripcion"],
                        precio=float(it["precio"]),
                        cantidad=float(it["cantidad"]),
                        subtotal=subtotal,
                        gravado15=grav15,
                        gravado18=grav18,
                        totalexento=ex,
                        isv15=iv15,
                        isv18=iv18,
                        grantotal=(grav15+grav18+ex+iv15+iv18)
                    )
                    if tipo_req != 'E' and insertar_sar_venta_detalle_mysql is not None:
                        try:
                            insertar_sar_venta_detalle_mysql(
                                id_venta=rid_sar,
                                numero_factura=str(numero_factura or rid_sar or ""),
                                codigo=it["codigo"],
                                nombre=it["descripcion"],
                                precio=float(it["precio"]),
                                cantidad=float(it["cantidad"]),
                                subtotal=subtotal,
                                gravado15=grav15,
                                gravado18=grav18,
                                totalexento=ex,
                                isv15=iv15,
                                isv18=iv18,
                                grantotal=(grav15+grav18+ex+iv15+iv18)
                            )
                        except Exception:
                            pass
                except Exception:
                # Insertar en una tabla mínima si falla helper
                    curm.execute("""
                        INSERT INTO ventas_detalle (factura, id_producto, cantidad, precio_unitario, subtotal, id_venta, numero_factura)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """, (str(numero_factura or ""), str(it["codigo"]), float(it["cantidad"]), float(it["precio"]), subtotal, int(rid), str(numero_factura or "")))
                connm.commit()
            if pedido_numero:
                try:
                    curm.execute("UPDATE pedidos SET estado=%s WHERE numero_pedido=%s", ("generado", pedido_numero))
                    connm.commit()
                except Exception:
                    pass
            try:
                if cai and numero_factura:
                    try:
                        ndoc = int(str(numero_factura).split("-")[-1])
                    except Exception:
                        ndoc = None
                    if ndoc is not None:
                        try:
                            curm.execute("UPDATE info_cai_general SET numero_documento=%s WHERE cai=%s", (ndoc, cai))
                            connm.commit()
                        except Exception:
                            pass
                        try:
                            curm.execute("UPDATE info_cai_exenta SET numero_documento=%s WHERE cai=%s", (ndoc, cai))
                            connm.commit()
                        except Exception:
                            pass
                        connm.commit()
            except Exception:
                pass
            try:
                connm.close()
                factura_id = int(rid or 0)
                pdf_path = generar_pdf_factura(factura_id, cliente_nombre, items, totales, efectivo, cambio, numero_factura, cai, cliente_rtn)
                return jsonify({"ok": True, "factura_id": factura_id, "pdf_url": f"/api/factura/{factura_id}/pdf"})
            except Exception:
                pass
        except Exception as e:
            return jsonify({"error": f"MySQL error al registrar venta: {str(e)}"}), 500
        return jsonify({"error": "No se pudo registrar venta en MySQL"}), 500
    return jsonify({"error": "MySQL no disponible"}), 503

def validar_formato_cai(cai: str) -> bool:
    patron = r'^[A-Z0-9]{6}(-[A-Z0-9]{6}){4}-[A-Z0-9]{2}$'
    try:
        return bool(re.match(patron, (cai or "").upper()))
    except Exception:
        return False

@app.get("/api/ultima-factura")
def api_ultima_factura():
    # Preferir MySQL si está disponible
    if conectar_mysql is not None:
        try:
            connm = conectar_mysql()
            curm = connm.cursor()
            try:
                curm.execute("SELECT id_venta FROM ventas_detalle WHERE id_venta IS NOT NULL ORDER BY id_detalle DESC LIMIT 1")
                r = curm.fetchone()
                if r and r[0]:
                    connm.close()
                    return jsonify({"id": int(r[0])})
            finally:
                try:
                    connm.close()
                except Exception:
                    pass
        except Exception:
            pass
    return jsonify({"id": None})

@app.get("/factura/imprimir/<int:factura_id>")
def factura_imprimir(factura_id):
    # Regenerar siempre el PDF desde MySQL si es posible
    try:
        path = os.path.join(FACTURAS_DIR, f"factura_{factura_id}.pdf")
        items = []
        numero_factura = None
        cai_str = None
        cliente_nombre = "CONSUMIDOR FINAL"
        efectivo = None
        cambio = None
        cliente_rtn = ""
        if conectar_mysql is not None:
            connm = conectar_mysql()
            curm = connm.cursor()
            try:
                curm.execute("""
                    SELECT id, nombre_articulo, valor_articulo, cantidad, gravado15, gravado18, totalexento, numero_factura
                    FROM ventas_detalle
                    WHERE id_venta = %s
                """, (factura_id,))
                rows = curm.fetchall() or []
                for r in rows:
                    codigo, nombre, precio, cantidad, g15, g18, exento, numf = r
                    id_isv = 3
                    try:
                        if float(g15 or 0) > 0: id_isv = 1
                        elif float(g18 or 0) > 0: id_isv = 2
                        else: id_isv = 3
                    except Exception:
                        id_isv = 3
                    items.append({
                        "codigo": str(codigo or ""),
                        "descripcion": str(nombre or ""),
                        "precio": float(precio or 0),
                        "cantidad": float(cantidad or 0),
                        "id_isv": int(id_isv)
                    })
                    if not numero_factura and numf:
                        numero_factura = str(numf)
                try:
                    curm.execute("""
                        SELECT cliente, rtn_cliente, efectivo, cambio, numero_factura
                        FROM ventas
                        WHERE id_venta = %s
                    """, (factura_id,))
                    rh = curm.fetchone()
                    if rh:
                        cn, rc, ef, ca, nf = rh
                        if cn:
                            cliente_nombre = str(cn or "")
                        cliente_rtn = str(rc or "")
                        try:
                            efectivo = float(ef or 0)
                        except Exception:
                            pass
                        try:
                            cambio = float(ca or 0)
                        except Exception:
                            pass
                        if not numero_factura and nf:
                            numero_factura = str(nf)
                except Exception:
                    pass
                curm.execute("""
                    SELECT cai, establecimiento, punto_emision, tipo_doc, numero_documento
                    FROM info_cai
                    WHERE activo=1
                    ORDER BY id DESC
                    LIMIT 1
                """)
                rc = curm.fetchone()
                if rc:
                    cai_str = str(rc[0] or "")
                connm.close()
            except Exception:
                try:
                    connm.close()
                except Exception:
                    pass
        if items:
            totales = calcular_totales_detalle(items)
            if efectivo is None:
                efectivo = totales["total"]
            if cambio is None:
                try:
                    cambio = round(float(efectivo) - float(totales["total"]), 2)
                except Exception:
                    cambio = 0.0
            pdf_path = generar_pdf_factura(factura_id, cliente_nombre, items, totales, efectivo, cambio, numero_factura, cai_str, cliente_rtn)
            if os.path.exists(pdf_path):
                return send_file(pdf_path, mimetype="application/pdf", as_attachment=False)
        # Si no hay datos en MySQL, intentar servir el PDF existente
        if os.path.exists(path):
            return send_file(path, mimetype="application/pdf", as_attachment=False)
    except Exception:
        pass
    return f"Factura {factura_id} no encontrada", 404

@app.get("/api/factura/<int:factura_id>/pdf")
def api_pdf(factura_id):
    path = os.path.join(FACTURAS_DIR, f"factura_{factura_id}.pdf")
    if not os.path.exists(path):
        return jsonify({"error":"PDF no encontrado"}), 404
    return send_file(path, mimetype="application/pdf", as_attachment=False, download_name=os.path.basename(path))

# -------- NUEVOS ENDPOINTS --------
@app.get("/api/cai-info")
def api_cai_info():
    if conectar_mysql is None:
        return jsonify({"available": False}), 503
    try:
        _asegurar_tablas_cai_separadas_mysql()
        connm = conectar_mysql()
        cur = connm.cursor()
        tipo_req = (request.args.get("tipo") or "").strip().upper()
        r = None
        if tipo_req in ("G", "E"):
            tbl = _tabla_cai(tipo_req)
            cur.execute(f"""
                SELECT cai, establecimiento, punto_emision, tipo_doc, numero_documento, rango_i, rango_f, f_limite
                FROM {tbl}
                WHERE activo=1
                ORDER BY id DESC
                LIMIT 1
            """)
            r = cur.fetchone()
        if not r:
            cur.execute("""
                SELECT cai, establecimiento, punto_emision, tipo_doc, numero_documento, rango_i, rango_f, f_limite
                FROM info_cai_general
                WHERE activo=1
                ORDER BY id DESC
                LIMIT 1
            """)
            r = cur.fetchone()
        if not r:
            cur.execute("""
                SELECT cai, establecimiento, punto_emision, tipo_doc, numero_documento, rango_i, rango_f, f_limite
                FROM info_cai_exenta
                WHERE activo=1
                ORDER BY id DESC
                LIMIT 1
            """)
            r = cur.fetchone()
        connm.close()
        if not r:
            return jsonify({"cai": None, "numero_formato": None, "numero_doc": None, "rango_i": None, "rango_f": None, "f_limite": None})
        cai, est, pem, tip, num, rangoi, rangof, flim = r
        try:
            numero_doc = int(num or 0) + 1
        except Exception:
            numero_doc = 1
        try:
            numero_fmt = f"{int(est):03d}-{int(pem):03d}-{int(tip):02d}-{int(numero_doc):08d}"
        except Exception:
            numero_fmt = f"000-000-00-{int(numero_doc):08d}"
        return jsonify({
            "cai": cai,
            "numero_doc": numero_doc,
            "numero_formato": numero_fmt,
            "rango_i": rangoi,
            "rango_f": rangof,
            "f_limite": flim
        })
    except Exception as e:
        try:
            connm.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

@app.get("/api/producto-mysql/<codigo>")
def api_producto_mysql(codigo):
    if conectar_mysql is None:
        return jsonify({"error": "MySQL no disponible"}), 503
    try:
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("SELECT id, nombre, barra, precio, id_isv FROM inventario WHERE id = %s OR barra = %s", (codigo, codigo))
        row = cur.fetchone()
        connm.close()
        if not row:
            return jsonify({"encontrado": False}), 404
        return jsonify({
            "encontrado": True,
            "id": int(row[0]),
            "nombre": row[1],
            "codigo": str(row[2] or ""),
            "precio": float(row[3]),
            "id_isv": int(row[4] or 3)
        })
    except Exception as e:
        return jsonify({"error": f"{e}"}), 500

@app.get("/api/apertura/estado")
def api_apertura_estado():
    if conectar_mysql is None:
        return jsonify({"available": False}), 503
    try:
        _asegurar_tabla_cierres_caja_mysql()
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("SELECT COUNT(*) FROM cierres_caja WHERE DATE(fecha_inicio)=CURDATE() AND fecha_fin IS NULL")
        abierta = int(cur.fetchone()[0] or 0) > 0
        connm.close()
        return jsonify({"abierta": abierta})
    except Exception as e:
        try:
            connm.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

@app.post("/api/apertura/abrir")
def api_apertura_abrir():
    if conectar_mysql is None:
        return jsonify({"available": False}), 503
    data = request.get_json(force=True) or {}
    try:
        monto = float(data.get("monto") or 0)
    except Exception:
        monto = 0.0
    u = session.get("usuario") or {}
    usuario = (u.get("usuario") or u.get("nombre") or "").strip()
    try:
        _asegurar_tabla_cierres_caja_mysql()
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("SELECT COUNT(*) FROM cierres_caja WHERE DATE(fecha_inicio)=CURDATE() AND fecha_fin IS NULL")
        abierta = int(cur.fetchone()[0] or 0) > 0
        if abierta:
            connm.close()
            return jsonify({"ok": True, "ya_abierta": True})
        cur.execute("INSERT INTO cierres_caja (fecha_inicio, monto_apertura, usuario) VALUES (%s,%s,%s)", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), monto, usuario))
        connm.commit()
        connm.close()
        return jsonify({"ok": True})
    except Exception as e:
        try:
            connm.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

@app.get("/api/producto-csv/<codigo>")
def api_producto_csv(codigo):
    candidates = [
        os.path.join(PARENT_DIR, "productos.csv"),
        os.path.join(PARENT_DIR, "productos"),
    ]
    encodings = ("utf-8-sig", "cp1252", "latin1")
    for path in candidates:
        if not os.path.exists(path):
            continue
        for enc in encodings:
            try:
                with open(path, "r", encoding=enc, newline="") as f:
                    sample = f.read(2048)
                    f.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                    except Exception:
                        class _D: pass
                        dialect = _D()
                        setattr(dialect, "delimiter", "\t")
                    reader = csv.reader(f, dialect)
                    try:
                        headers = next(reader)
                    except Exception:
                        headers = []
                    col_codigo = 0
                    col_nombre = 1
                    col_precio = None
                    col_isv = None
                    col_stock = None
                    col_pesable = None
                    if headers:
                        hclean = [str(h).lower().strip().replace("\ufeff","") for h in headers]
                        for i, h in enumerate(hclean):
                            if "codigo" in h or "barra" in h or "ean" in h:
                                col_codigo = i
                            if "nombre" in h or "producto" in h:
                                col_nombre = i
                            if "precio" in h or "price" in h or "valor" in h:
                                col_precio = i
                            if "isv" in h or "iva" in h or "impuesto" in h:
                                col_isv = i
                            if "stock" in h or "existencia" in h:
                                col_stock = i
                            if "pesable" in h:
                                col_pesable = i
                    fila = 1
                    for row in reader:
                        fila += 1
                        if len(row) <= max(col_codigo, col_nombre):
                            continue
                        kc = str(row[col_codigo]).strip()
                        if kc == codigo:
                            nombre = str(row[col_nombre]).strip()
                            precio = None
                            id_isv = None
                            stock = None
                            pesable = None
                            try:
                                if col_precio is not None and col_precio < len(row):
                                    precio = float(str(row[col_precio]).replace(",", ".").strip())
                            except Exception:
                                precio = None
                            try:
                                if col_isv is not None and col_isv < len(row):
                                    isv_val = str(row[col_isv]).strip().lower()
                                    if isv_val in ("15","15%","1"):
                                        id_isv = 1
                                    elif isv_val in ("18","18%","2"):
                                        id_isv = 2
                                    elif isv_val in ("exento","ex","3","0"):
                                        id_isv = 3
                            except Exception:
                                id_isv = None
                            try:
                                if col_stock is not None and col_stock < len(row):
                                    stock = int(str(row[col_stock]).strip())
                            except Exception:
                                stock = None
                            try:
                                if col_pesable is not None and col_pesable < len(row):
                                    p = str(row[col_pesable]).strip().lower()
                                    pesable = 1 if p in ("si","sí","true","1","y","yes") else 0
                            except Exception:
                                pesable = None
                            return jsonify({
                                "encontrado": True,
                                "codigo": codigo,
                                "nombre": nombre,
                                "precio": precio,
                                "id_isv": id_isv,
                                "stock": stock,
                                "pesable": pesable,
                                "fila": fila
                            })
            except Exception:
                continue
    return jsonify({"encontrado": False, "mensaje": "Código no encontrado en CSV"}), 404

@app.get("/buscar-codigo/<codigo>")
def redir_buscar_codigo(codigo):
    # 1) Intentar en MySQL, priorizando barra exacta; 6 dígitos se consideran código único
    if conectar_mysql is not None:
        try:
            if asegurar_tablas_mysql is not None:
                try:
                    asegurar_tablas_mysql()
                except Exception:
                    pass
            connm = conectar_mysql()
            cur = connm.cursor()
            codigo_str = str(codigo).strip()
            # Buscar por barra exacta
            cur.execute("SELECT id, nombre, barra, precio, id_isv FROM inventario WHERE (activo IS NULL OR activo = 1) AND barra = %s", (codigo_str,))
            row = cur.fetchone()
            if not row:
                # Fallback: buscar en inventario_barras
                try:
                    cur.execute("SELECT producto_id FROM inventario_barras WHERE barra = %s", (codigo_str,))
                    pidrow = cur.fetchone()
                    if pidrow:
                        cur.execute("SELECT id, nombre, barra, precio, id_isv FROM inventario WHERE (activo IS NULL OR activo = 1) AND id = %s", (int(pidrow[0]),))
                        row = cur.fetchone()
                except Exception:
                    row = None
            # Buscar por id si el código es de 6 dígitos (000123 -> id=123)
            if not row and codigo_str.isdigit() and len(codigo_str) == 6:
                try:
                    cur.execute("SELECT id, nombre, barra, precio, id_isv FROM inventario WHERE (activo IS NULL OR activo = 1) AND id = %s", (int(codigo_str),))
                    row = cur.fetchone()
                except Exception:
                    row = None
            connm.close()
            if row:
                return redirect(f"/productos?codigo={row[2] or codigo_str}&msg=found_mysql")
        except Exception:
            pass
    # 2) Intentar en CSV
    candidates = [
        os.path.join(PARENT_DIR, "productos.csv"),
        os.path.join(PARENT_DIR, "productos"),
    ]
    encodings = ("utf-8-sig", "cp1252", "latin1")
    for path in candidates:
        if not os.path.exists(path):
            continue
        for enc in encodings:
            try:
                with open(path, "r", encoding=enc, newline="") as f:
                    sample = f.read(2048)
                    f.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                    except Exception:
                        class _D: pass
                        dialect = _D()
                        setattr(dialect, "delimiter", "\t")
                    reader = csv.reader(f, dialect)
                    try:
                        headers = next(reader)
                    except Exception:
                        headers = []
                    col_codigo = 0
                    col_nombre = 1
                    if headers:
                        hclean = [str(h).lower().strip().replace("\ufeff","") for h in headers]
                        for i, h in enumerate(hclean):
                            if "codigo" in h or "barra" in h or "ean" in h:
                                col_codigo = i
                            if "nombre" in h or "producto" in h:
                                col_nombre = i
                    for row in reader:
                        if len(row) <= max(col_codigo, col_nombre):
                            continue
                        if str(row[col_codigo]).strip() == str(codigo).strip():
                            nombre = str(row[col_nombre]).strip()
                            return redirect(f"/agregar-producto?codigo={codigo}&nombre={nombre}&msg=found_csv")
            except Exception:
                continue
    # 3) No encontrado: abrir agregar producto con el código para captura manual
    return redirect(f"/agregar-producto?codigo={codigo}&msg=not_found")

@app.get("/api/producto-mysql-por-nombre")
def api_producto_mysql_por_nombre():
    if conectar_mysql is None:
        return jsonify({"error": "MySQL no disponible"}), 503
    nombre = request.args.get("nombre", "").strip()
    if not nombre:
        return jsonify({"error": "Parámetro 'nombre' requerido"}), 400
    try:
        connm = conectar_mysql()
        cur = connm.cursor()
        # Búsqueda exacta (case-insensitive)
        cur.execute(
            "SELECT id, nombre, barra, precio, id_isv FROM inventario WHERE LOWER(nombre) = LOWER(%s) LIMIT 1",
            (nombre,)
        )
        row = cur.fetchone()
        # Si no hay exacta, buscar coincidencia parcial y tomar la mejor coincidencia
        if not row:
            cur.execute(
                "SELECT id, nombre, barra, precio, id_isv FROM inventario WHERE nombre LIKE %s ORDER BY LENGTH(nombre) ASC LIMIT 1",
                (f"%{nombre}%",)
            )
            row = cur.fetchone()
        connm.close()
        if not row:
            return jsonify({"encontrado": False}), 404
        return jsonify({
            "encontrado": True,
            "id": int(row[0]),
            "nombre": row[1],
            "codigo": str(row[2] or ""),
            "precio": float(row[3]),
            "id_isv": int(row[4] or 3)
        })
    except Exception as e:
        return jsonify({"error": f"{e}"}), 500

@app.get("/api/producto-csv-por-nombre")
def api_producto_csv_por_nombre():
    nombre = request.args.get("nombre", "").strip()
    if not nombre:
        return jsonify({"error": "Parámetro 'nombre' requerido"}), 400
    candidates = [
        os.path.join(PARENT_DIR, "productos.csv"),
        os.path.join(PARENT_DIR, "productos"),
    ]
    encodings = ("utf-8-sig", "cp1252", "latin1")
    objetivo = nombre.lower()
    for path in candidates:
        if not os.path.exists(path):
            continue
        for enc in encodings:
            try:
                with open(path, "r", encoding=enc, newline="") as f:
                    sample = f.read(2048)
                    f.seek(0)
                    try:
                        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
                    except Exception:
                        class _D: pass
                        dialect = _D()
                        setattr(dialect, "delimiter", "\t")
                    reader = csv.reader(f, dialect)
                    try:
                        headers = next(reader)
                    except Exception:
                        headers = []
                    col_codigo = None
                    col_nombre = 1
                    col_precio = None
                    col_isv = None
                    col_stock = None
                    col_pesable = None
                    if headers:
                        hclean = [str(h).lower().strip().replace("\ufeff","") for h in headers]
                        for i, h in enumerate(hclean):
                            if "codigo" in h or "barra" in h or "ean" in h:
                                col_codigo = i
                            if "nombre" in h or "producto" in h:
                                col_nombre = i
                            if "precio" in h or "price" in h or "valor" in h:
                                col_precio = i
                            if "isv" in h or "iva" in h or "impuesto" in h:
                                col_isv = i
                            if "stock" in h or "existencia" in h:
                                col_stock = i
                            if "pesable" in h:
                                col_pesable = i
                    fila = 1
                    mejor = None
                    for row in reader:
                        fila += 1
                        if len(row) <= col_nombre:
                            continue
                        nombre_csv = str(row[col_nombre]).strip()
                        nc = nombre_csv.lower()
                        if nc == objetivo:
                            precio = None
                            id_isv = None
                            stock = None
                            pesable = None
                            try:
                                if col_precio is not None and col_precio < len(row):
                                    precio = float(str(row[col_precio]).replace(",", ".").strip())
                            except Exception:
                                precio = None
                            try:
                                if col_isv is not None and col_isv < len(row):
                                    isv_val = str(row[col_isv]).strip().lower()
                                    if isv_val in ("15","15%","1"):
                                        id_isv = 1
                                    elif isv_val in ("18","18%","2"):
                                        id_isv = 2
                                    elif isv_val in ("exento","ex","3","0"):
                                        id_isv = 3
                            except Exception:
                                id_isv = None
                            try:
                                if col_stock is not None and col_stock < len(row):
                                    stock = int(str(row[col_stock]).strip())
                            except Exception:
                                stock = None
                            try:
                                if col_pesable is not None and col_pesable < len(row):
                                    p = str(row[col_pesable]).strip().lower()
                                    pesable = 1 if p in ("si","sí","true","1","y","yes") else 0
                            except Exception:
                                pesable = None
                            return jsonify({
                                "encontrado": True,
                                "codigo": (str(row[col_codigo]).strip() if col_codigo is not None and col_codigo < len(row) else ""),
                                "nombre": nombre_csv,
                                "precio": precio,
                                "id_isv": id_isv,
                                "stock": stock,
                                "pesable": pesable,
                                "fila": fila
                            })
                        # Guardar primera coincidencia parcial como mejor candidata
                        if mejor is None and (objetivo in nc):
                            mejor = (list(row), fila)
                    if mejor is not None:
                        row, fila = mejor
                        precio = None
                        id_isv = None
                        stock = None
                        pesable = None
                        try:
                            if col_precio is not None and col_precio < len(row):
                                precio = float(str(row[col_precio]).replace(",", ".").strip())
                        except Exception:
                            precio = None
                        try:
                            if col_isv is not None and col_isv < len(row):
                                isv_val = str(row[col_isv]).strip().lower()
                                if isv_val in ("15","15%","1"):
                                    id_isv = 1
                                elif isv_val in ("18","18%","2"):
                                    id_isv = 2
                                elif isv_val in ("exento","ex","3","0"):
                                    id_isv = 3
                        except Exception:
                            id_isv = None
                        try:
                            if col_stock is not None and col_stock < len(row):
                                stock = int(str(row[col_stock]).strip())
                        except Exception:
                            stock = None
                        try:
                            if col_pesable is not None and col_pesable < len(row):
                                p = str(row[col_pesable]).strip().lower()
                                pesable = 1 if p in ("si","sí","true","1","y","yes") else 0
                        except Exception:
                            pesable = None
                        return jsonify({
                            "encontrado": True,
                            "codigo": (str(row[col_codigo]).strip() if col_codigo is not None and col_codigo < len(row) else ""),
                            "nombre": str(row[col_nombre]).strip(),
                            "precio": precio,
                            "id_isv": id_isv,
                            "stock": stock,
                            "pesable": pesable,
                            "fila": fila
                        })
            except Exception:
                continue
    return jsonify({"encontrado": False, "mensaje": "Nombre no encontrado en CSV"}), 404

@app.get("/api/buscar-producto-api/<codigo>")
def api_buscar_producto_externo(codigo):
    """
    Busca un producto en APIs públicas por código de barras
    Retorna información del producto si se encuentra
    """
    resultado = product_lookup.buscar_producto(codigo)
    
    if not resultado:
        return jsonify({
            "encontrado": False,
            "mensaje": "Producto no encontrado en bases de datos públicas"
        }), 404
    
    return jsonify({
        "encontrado": True,
        "datos": resultado
    })

@app.post("/api/productos/quick-add")
def api_quick_add_producto():
    """
    Endpoint rápido para agregar producto:
    1. Intenta buscar en APIs públicas
    2. Pre-llena los datos encontrados
    3. Permite completar/editar antes de guardar
    """
    data = request.get_json(force=True) or {}
    codigo_barras = data.get("codigo_barras", "").strip()
    
    if not codigo_barras:
        return jsonify({"error": "Código de barras requerido"}), 400
    
    # Buscar en APIs públicas
    info_externa = product_lookup.buscar_producto(codigo_barras)
    
    # Preparar respuesta con datos encontrados o vacíos
    respuesta = {
        "codigo_barras": codigo_barras,
        "nombre": data.get("nombre", ""),
        "precio": data.get("precio", 0),
        "id_isv": data.get("id_isv", 1),  # 1=15% por defecto
        "stock": data.get("stock", 100),
        "info_externa": None
    }
    
    if info_externa:
        # Pre-llenar con datos externos si existen
        if not respuesta["nombre"] and info_externa.get("nombre"):
            respuesta["nombre"] = info_externa["nombre"]
            if info_externa.get("marca"):
                respuesta["nombre"] = f"{info_externa['marca']} {info_externa['nombre']}"
        
        respuesta["info_externa"] = info_externa
    
    # Si el request incluye "guardar": true, insertar en BD
    if data.get("guardar") == True:
        nombre = respuesta["nombre"].strip()
        precio = float(respuesta["precio"])
        
        if not nombre or precio <= 0:
            return jsonify({"error": "Nombre y precio válidos son requeridos"}), 400
        
        try:
            new_id = execute(
                "INSERT INTO inventario (barra, nombre, precio, id_isv, stock) VALUES (?, ?, ?, ?, ?)",
                (codigo_barras, nombre, precio, respuesta["id_isv"], respuesta["stock"])
            )
            respuesta["guardado"] = True
            respuesta["id"] = new_id
        except sqlite3.IntegrityError:
            return jsonify({"error": "El código de barras ya existe en inventario"}), 409
    else:
        respuesta["guardado"] = False
    
    return jsonify(respuesta)

@app.post("/api/facturas/anular")
def api_anular_factura():
    data = request.get_json(force=True) or {}
    numero = (data.get("numero_factura") or data.get("numero") or "").strip()
    usuario = (data.get("usuario") or "").strip()
    motivo = (data.get("motivo") or "").strip()
    fecha = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not numero:
        return jsonify({"error": "Número de factura requerido"}), 400
    # MySQL preferente
    if conectar_mysql is not None:
        try:
            if asegurar_tablas_mysql is not None:
                try:
                    asegurar_tablas_mysql()
                except Exception:
                    pass
            connm = conectar_mysql()
            cur = connm.cursor()
            # Asegurar tabla de logs
            try:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS logs_anulaciones (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        id_venta INT,
                        numero_factura VARCHAR(64),
                        usuario VARCHAR(255),
                        fecha_anulacion VARCHAR(19),
                        motivo TEXT,
                        datos_json TEXT
                    )
                """)
            except Exception:
                pass
            # Marcar anulada
            cur.execute("UPDATE ventas SET estado = 'anulada' WHERE numero_factura = %s", (numero,))
            afectadas = cur.rowcount
            try:
                import json as _json
                datos_json = _json.dumps({"motivo": motivo, "usuario": usuario}, ensure_ascii=False)
                cur.execute("INSERT INTO logs_anulaciones (id_venta, numero_factura, usuario, fecha_anulacion, motivo, datos_json) VALUES (%s, %s, %s, %s, %s, %s)",
                            (None, numero, usuario or "", fecha, motivo or "", datos_json))
            except Exception:
                pass
            connm.commit()
            connm.close()
            if afectadas > 0:
                return jsonify({"ok": True, "numero_factura": numero, "estado": "anulada"})
            return jsonify({"error": "Factura no encontrada"}), 404
        except Exception as e:
            return jsonify({"error": f"{e}"}), 500
    # SQLite fallback
    try:
        # Asegurar tabla ventas y logs en SQLite
        try:
            with get_db() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS ventas (id_venta INTEGER PRIMARY KEY AUTOINCREMENT, numero_factura TEXT, estado TEXT, fecha TEXT)")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS logs_anulaciones (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        id_venta INTEGER,
                        numero_factura TEXT,
                        usuario TEXT,
                        fecha_anulacion TEXT,
                        motivo TEXT,
                        datos_json TEXT
                    )
                """)
                conn.commit()
        except Exception:
            pass
        with get_db() as conn:
            cur = conn.cursor()
            cur.execute("UPDATE ventas SET estado = 'anulada' WHERE numero_factura = ?", (numero,))
            afectadas = cur.rowcount
            try:
                import json as _json
                datos_json = _json.dumps({"motivo": motivo, "usuario": usuario}, ensure_ascii=False)
                cur.execute("INSERT INTO logs_anulaciones (id_venta, numero_factura, usuario, fecha_anulacion, motivo, datos_json) VALUES (?, ?, ?, ?, ?, ?)",
                            (None, numero, usuario or "", fecha, motivo or "", datos_json))
            except Exception:
                pass
            conn.commit()
        if afectadas > 0:
            return jsonify({"ok": True, "numero_factura": numero, "estado": "anulada"})
        return jsonify({"error": "Factura no encontrada"}), 404
    except Exception as e:
        return jsonify({"error": f"{e}"}), 500

@app.post("/api/productos/escanear-y-crear")
def api_escanear_y_crear():
    """
    Flujo completo: escanea código -> busca en APIs -> crea producto
    Solo requiere: codigo_barras, precio (el resto se auto-completa)
    """
    data = request.get_json(force=True) or {}
    codigo_barras = data.get("codigo_barras", "").strip()
    precio = data.get("precio")
    
    if not codigo_barras:
        return jsonify({"error": "Código de barras requerido"}), 400
    
    if precio is None or float(precio) <= 0:
        return jsonify({"error": "Precio válido requerido"}), 400
    
    # Verificar si ya existe
    existe = query_one("SELECT id FROM inventario WHERE barra = ?", (codigo_barras,))
    if existe:
        return jsonify({"error": "Este código de barras ya está registrado"}), 409
    
    # Buscar info externa
    info_externa = product_lookup.buscar_producto(codigo_barras)
    
    # Determinar nombre
    if data.get("nombre"):
        nombre = data["nombre"]
    elif info_externa:
        nombre = info_externa.get("nombre", "")
        if info_externa.get("marca"):
            nombre = f"{info_externa['marca']} {nombre}"
    else:
        nombre = f"Producto {codigo_barras}"
    
    # Usar valores por defecto o proporcionados
    id_isv = int(data.get("id_isv", 1))  # 15% por defecto
    stock = int(data.get("stock", 100))
    
    try:
        new_id = execute(
            "INSERT INTO inventario (barra, nombre, precio, id_isv, stock) VALUES (?, ?, ?, ?, ?)",
            (codigo_barras, nombre.strip(), float(precio), id_isv, stock)
        )
        
        return jsonify({
            "ok": True,
            "id": new_id,
            "nombre": nombre,
            "info_externa": info_externa,
            "mensaje": "Producto creado exitosamente"
        })
    except Exception as e:
        return jsonify({"error": f"Error al crear producto: {str(e)}"}), 500

@app.errorhandler(404)
def handle_404(e):
    if session.get("usuario"):
        return redirect("/menu")
    return redirect(url_for("login_view"))

@app.get("/")
def root_index():
    if session.get("usuario"):
        return redirect(url_for("menu_principal_view"))
    return redirect(url_for("login_view"))

@app.get("/usuarios")
def usuarios_view():
    if not _is_admin():
        return redirect(url_for("menu_principal_view"))
    return render_template("usuarios.html")

@app.get("/api/usuarios")
def api_usuarios_list():
    if not _is_admin():
        return jsonify({"error":"No autorizado"}), 403
    try:
        _asegurar_tabla_usuarios_mysql()
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("SELECT nombre, usuario, rol, activo FROM usuarios ORDER BY nombre")
        rows = cur.fetchall()
        connm.close()
        data = [{"nombre": r[0], "usuario": r[1], "rol": r[2], "activo": int(r[3] or 0)} for r in rows]
        return jsonify(data)
    except Exception as e:
        try:
            connm.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

@app.post("/api/usuarios")
def api_usuarios_create():
    if not _is_admin():
        return jsonify({"error":"No autorizado"}), 403
    req = request.get_json(force=True) or {}
    usuario = (req.get("usuario") or "").strip()
    nombre = (req.get("nombre") or "").strip()
    contrasena = (req.get("contrasena") or "").strip()
    rol = (req.get("rol") or "cajero").strip()
    activo = int(req.get("activo", 1) or 1)
    if not usuario or not nombre or not contrasena:
        return jsonify({"error":"Datos requeridos"}), 400
    try:
        _asegurar_tabla_usuarios_mysql()
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("SELECT 1 FROM usuarios WHERE usuario = %s", (usuario,))
        if cur.fetchone():
            connm.close()
            return jsonify({"error":"Usuario ya existe"}), 409
        hashed = _hash_password(contrasena)
        cur.execute("INSERT INTO usuarios (usuario, nombre, contrasena, rol, activo) VALUES (%s, %s, %s, %s, %s)", (usuario, nombre, hashed, rol, activo))
        connm.commit()
        connm.close()
        return jsonify({"ok": True})
    except Exception as e:
        try:
            connm.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

@app.put("/api/usuarios/<usuario>")
def api_usuarios_update(usuario):
    if not _is_admin():
        return jsonify({"error":"No autorizado"}), 403
    req = request.get_json(force=True) or {}
    nombre = req.get("nombre")
    contrasena = req.get("contrasena")
    rol = req.get("rol")
    activo = req.get("activo")
    try:
        _asegurar_tabla_usuarios_mysql()
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("SELECT nombre, usuario, rol, activo FROM usuarios WHERE usuario = %s", (usuario,))
        if not cur.fetchone():
            connm.close()
            return jsonify({"error":"Usuario no encontrado"}), 404
        sets = []
        vals = []
        if nombre is not None:
            sets.append("nombre = %s")
            vals.append(nombre.strip())
        if contrasena:
            sets.append("contrasena = %s")
            vals.append(_hash_password(contrasena))
        if rol is not None:
            sets.append("rol = %s")
            vals.append(rol.strip())
        if activo is not None:
            sets.append("activo = %s")
            vals.append(int(activo))
        if not sets:
            connm.close()
            return jsonify({"error":"Sin cambios"}), 400
        vals.append(usuario)
        cur.execute(f"UPDATE usuarios SET {', '.join(sets)} WHERE usuario = %s", tuple(vals))
        connm.commit()
        connm.close()
        return jsonify({"ok": True})
    except Exception as e:
        try:
            connm.close()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500

@app.post("/api/usuarios/<usuario>/activar")
def api_usuarios_activar(usuario):
    if not _is_admin():
        return jsonify({"error":"No autorizado"}), 403
    try:
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("UPDATE usuarios SET activo = 1 WHERE usuario = %s", (usuario,))
        connm.commit()
        connm.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.post("/api/usuarios/<usuario>/inactivar")
def api_usuarios_inactivar(usuario):
    if not _is_admin():
        return jsonify({"error":"No autorizado"}), 403
    try:
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("UPDATE usuarios SET activo = 0 WHERE usuario = %s", (usuario,))
        connm.commit()
        connm.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.delete("/api/usuarios/<usuario>")
def api_usuarios_delete(usuario):
    if not _is_admin():
        return jsonify({"error":"No autorizado"}), 403
    try:
        connm = conectar_mysql()
        cur = connm.cursor()
        cur.execute("DELETE FROM usuarios WHERE usuario = %s", (usuario,))
        connm.commit()
        connm.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    print("Iniciando servidor...", flush=True)
    port = int(os.getenv("APP_PORT", "5000"))
    ssl_ctx = None
    http_only = (os.getenv("APP_HTTP_ONLY") == "1")
    cert_path = os.getenv("SSL_CERT_FILE")
    key_path = os.getenv("SSL_KEY_FILE")
    use_adhoc = False
    try:
        if cert_path and key_path and os.path.exists(cert_path) and os.path.exists(key_path):
            ssl_ctx = (cert_path, key_path)
        else:
            try:
                import cryptography  # type: ignore
                use_adhoc = True
            except Exception:
                use_adhoc = False
    except Exception:
        ssl_ctx = None
    if http_only:
        ssl_ctx = None
    elif not ssl_ctx and use_adhoc:
        try:
            import socket
            from datetime import datetime, timedelta
            import ipaddress
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            cert_dir = os.path.join(PARENT_DIR, "certs")
            os.makedirs(cert_dir, exist_ok=True)
            cert_file = os.path.join(cert_dir, "dev-cert.pem")
            key_file = os.path.join(cert_dir, "dev-key.pem")
            force_regen = (os.getenv("SSL_FORCE_REGEN") == "1")
            need_gen = force_regen or not (os.path.exists(cert_file) and os.path.exists(key_file))
            if need_gen:
                key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, u"Sistema Local")])
                alt_names = [x509.DNSName(u"localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    s.connect(("8.8.8.8", 80))
                    ip = s.getsockname()[0]
                    s.close()
                    alt_names.append(x509.IPAddress(ipaddress.ip_address(ip)))
                except Exception:
                    pass
                # Add additional SAN IPs from environment (comma separated)
                try:
                    extra_ips = (os.getenv("SSL_SAN_IPS") or "").strip()
                    if extra_ips:
                        for raw in extra_ips.split(","):
                            raw = raw.strip()
                            if raw:
                                try:
                                    alt_names.append(x509.IPAddress(ipaddress.ip_address(raw)))
                                except Exception:
                                    pass
                except Exception:
                    pass
                san = x509.SubjectAlternativeName(alt_names)
                cert = (
                    x509.CertificateBuilder()
                    .subject_name(name)
                    .issuer_name(name)
                    .public_key(key.public_key())
                    .serial_number(x509.random_serial_number())
                    .not_valid_before(datetime.utcnow() - timedelta(days=1))
                    .not_valid_after(datetime.utcnow() + timedelta(days=3650))
                    .add_extension(san, critical=False)
                    .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
                    .sign(key, hashes.SHA256())
                )
                with open(key_file, "wb") as f:
                    f.write(
                        key.private_bytes(
                            encoding=serialization.Encoding.PEM,
                            format=serialization.PrivateFormat.TraditionalOpenSSL,
                            encryption_algorithm=serialization.NoEncryption(),
                        )
                    )
                with open(cert_file, "wb") as f:
                    f.write(cert.public_bytes(serialization.Encoding.PEM))
            ssl_ctx = (cert_file, key_file)
        except Exception:
            ssl_ctx = "adhoc"
    app.run(debug=True, use_reloader=True, host="0.0.0.0", port=port, ssl_context=ssl_ctx)
