
DROP TABLE IF EXISTS clientes;
DROP TABLE IF EXISTS inventario;
DROP TABLE IF EXISTS ventas;
DROP TABLE IF EXISTS facturas_resumen;
DROP TABLE IF EXISTS compania;

CREATE TABLE clientes (
  id_cliente INTEGER PRIMARY KEY AUTOINCREMENT,
  rtn TEXT,
  nombre TEXT NOT NULL
);

CREATE TABLE inventario (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  barra TEXT UNIQUE,
  nombre TEXT NOT NULL,
  precio REAL NOT NULL,
  id_isv INTEGER NOT NULL DEFAULT 3, -- 1=15%, 2=18%, 3=exento
  stock INTEGER NOT NULL DEFAULT 100
);

CREATE TABLE ventas (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  factura INTEGER NOT NULL,
  producto_id TEXT NOT NULL,
  nombre_producto TEXT NOT NULL,
  precio REAL NOT NULL,
  cantidad INTEGER NOT NULL,
  subtotal REAL NOT NULL,
  gravado15 REAL NOT NULL,
  gravado18 REAL NOT NULL,
  exento REAL NOT NULL,
  isv15 REAL NOT NULL,
  isv18 REAL NOT NULL,
  total_linea REAL NOT NULL
);

CREATE TABLE facturas_resumen (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cliente TEXT NOT NULL,
  gravado15 REAL NOT NULL,
  gravado18 REAL NOT NULL,
  exento REAL NOT NULL,
  isv15 REAL NOT NULL,
  isv18 REAL NOT NULL,
  total REAL NOT NULL,
  efectivo REAL NOT NULL,
  cambio REAL NOT NULL,
  fecha TEXT NOT NULL
);

CREATE TABLE compania (
  id_cia INTEGER PRIMARY KEY AUTOINCREMENT,
  nombre_cia TEXT NOT NULL,
  direccion1 TEXT,
  direccion2 TEXT,
  rtn_cia TEXT,
  correo TEXT,
  telefono TEXT
);

INSERT INTO compania (nombre_cia, direccion1, direccion2, rtn_cia, correo, telefono)
VALUES ('MI EMPRESA S.A.', 'Col. Centro, Calle Principal', 'Ciudad, País', '08011999123456', 'ventas@miempresa.com', '9999-9999');

INSERT INTO clientes (rtn, nombre) VALUES
('08011999123456', 'CONSUMIDOR FINAL'),
('08011999000011', 'Cliente Demo 1'),
('08011999000022', 'Cliente Demo 2');

INSERT INTO inventario (barra, nombre, precio, id_isv, stock) VALUES
('1001', 'Café Americano 8oz', 35.00, 3, 200),
('1002', 'Refresco Lata', 25.00, 1, 150),
('1003', 'Pollo Asado 1/4', 120.00, 2, 80),
('A001', 'Pan de la casa', 15.00, 3, 300);
