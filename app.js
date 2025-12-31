const $ = (sel) => document.querySelector(sel);
const fmt = (n)=> "L " + (Number(n||0).toFixed(2));

const state = {
  items: []
};

async function fetchJSON(url, opts={}){
  const r = await fetch(url, Object.assign({headers:{'Content-Type':'application/json'}}, opts));
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}

function recalc(){
  let exento=0, gravado15=0, gravado18=0, isv15=0, isv18=0;
  state.items.forEach(it=>{
    const subtotal = it.cantidad*it.precio;
    if(it.id_isv==3){ exento += subtotal; }
    else if(it.id_isv==1){ const base=subtotal/1.15; gravado15+=base; isv15+=base*0.15; }
    else if(it.id_isv==2){ const base=subtotal/1.18; gravado18+=base; isv18+=base*0.18; }
  });
  const total = exento+gravado15+gravado18+isv15+isv18;
  $("#exento").textContent = fmt(exento);
  $("#gravado15").textContent = fmt(gravado15);
  $("#gravado18").textContent = fmt(gravado18);
  $("#isv15").textContent = fmt(isv15);
  $("#isv18").textContent = fmt(isv18);
  $("#total").textContent = fmt(total);
}

function renderTable(){
  const tbody = $("#tabla tbody");
  tbody.innerHTML = "";
  state.items.forEach((it, i)=>{
    const tr = document.createElement("tr");
    const subtotal = it.cantidad * it.precio;
    tr.innerHTML = `
      <td>${i+1}</td>
      <td>${it.codigo}</td>
      <td>${it.descripcion}</td>
      <td style="text-align:right">${it.precio.toFixed(2)}</td>
      <td style="text-align:center">${it.cantidad}</td>
      <td style="text-align:right">${subtotal.toFixed(2)}</td>
      <td style="text-align:center">${it.id_isv==3?'E':''}</td>
      <td style="text-align:center"><button data-i="${i}" class="btn-del">Eliminar</button></td>`;
    tbody.appendChild(tr);
  });
  tbody.querySelectorAll(".btn-del").forEach(btn=>{
    btn.addEventListener("click", e=>{
      const i = Number(e.target.getAttribute("data-i"));
      state.items.splice(i,1);
      renderTable(); recalc();
    });
  });
}

async function cargarClientes(){
  const data = await fetchJSON("/api/clientes");
  const dl = $("#clientes-list");
  dl.innerHTML = "";
  data.forEach(c=>{
    const opt = document.createElement("option");
    opt.value = c.nombre;
    opt.label = c.rtn || "";
    dl.appendChild(opt);
  });
}

async function buscarProducto(codigo){
  try{
    const p = await fetchJSON(`/api/producto/${encodeURIComponent(codigo)}`);
    $("#descripcion").value = p.nombre;
    $("#precio").value = Number(p.precio).toFixed(2);
    $("#id_isv").value = p.id_isv;
  } catch(e){
    $("#descripcion").value = "Producto no disponible";
    $("#precio").value = "";
  }
}

function agregar(){
  const codigo = $("#codigo").value.trim();
  const descripcion = $("#descripcion").value.trim();
  const precio = Number($("#precio").value);
  const cantidad = Number($("#cantidad").value || 1);
  const id_isv = Number($("#id_isv").value);

  if(!codigo || !descripcion || !precio || cantidad<=0){
    alert("Complete los datos del producto");
    return;
  }

  // Merge si ya existe
  const idx = state.items.findIndex(it=> String(it.codigo)===codigo);
  if(idx>-1){
    state.items[idx].cantidad += cantidad;
  } else {
    state.items.push({codigo, descripcion, precio, cantidad, id_isv});
  }
  $("#codigo").value = "";
  $("#descripcion").value = "";
  $("#precio").value = "";
  $("#cantidad").value = 1;
  $("#codigo").focus();
  renderTable(); recalc();
}

let lastFacturaId = null;

async function checkUltimaFactura() {
    try {
        const res = await fetchJSON("/api/ultima-factura");
        if (res && res.id) {
            lastFacturaId = res.id;
            const btn = document.getElementById("btn-reimprimir"); // Usar getElementById para asegurar
            if(btn) btn.style.display = "inline-block";
        }
    } catch (e) {
        console.log("No se pudo obtener ultima factura", e);
    }
}

// Inicializar al cargar
document.addEventListener("DOMContentLoaded", () => {
    renderTable();
    checkUltimaFactura();
});

function reimprimirUltima() {
    if (lastFacturaId) {
        window.open(`/factura/imprimir/${lastFacturaId}`, "_blank", "width=400,height=600");
    }
}

async function pagar(){
  if(state.items.length===0){ alert("No hay artículos por pagar"); return; }
  const cliente_nombre = $("#cliente-input").value.trim();
  const efectivo = Number($("#monto-pagado").value || 0);

  try{
    const res = await fetchJSON("/api/registrar-venta", {
      method:"POST",
      body: JSON.stringify({ cliente_nombre, items: state.items, pago: {efectivo} })
    });
    
    lastFacturaId = res.factura_id;
    $("#btn-reimprimir").style.display = "inline-block";
    
    // Intentar abrir factura automáticamente
    const printWindow = window.open(`/factura/imprimir/${res.factura_id}`, "_blank", "width=400,height=600");
    
    // Si se bloqueó (printWindow es null), avisar al usuario
    if (!printWindow) {
        alert("Venta registrada. Por favor presione el botón 'Reimprimir Última' si la factura no aparece.");
    } else {
        // Si se abrió, poner foco
        printWindow.focus();
    }

    // reset
    state.items = [];
    renderTable(); recalc();
    $("#cliente-input").value = "";
    $("#monto-pagado").value = "";
  }catch(e){
    console.error(e);
    try{ const j = JSON.parse(e.message); alert(j.error || "Error en pago"); }catch(_){ alert("Error en pago"); }
  }
}

async function cargarProductos(){
  const data = await fetchJSON("/api/productos");
  const sel = $("#producto-select");
  sel.innerHTML = "";
  data.forEach(p=>{
    const opt = document.createElement("option");
    opt.value = p.id; // id real
    opt.textContent = `${p.nombre} (L ${p.precio})`;
    opt.dataset.codigo = p.codigo;
    opt.dataset.precio = p.precio;
    opt.dataset.nombre = p.nombre;
    opt.dataset.isv = p.id_isv;
    sel.appendChild(opt);
  });

  // Iniciar TomSelect en el select
  new TomSelect("#producto-select", {
    placeholder: "Buscar producto...",
    maxOptions: 2000,
    create: false,
    sortField: {field: "text", direction: "asc"},
    onChange: (value)=>{
      const opt = sel.querySelector(`option[value="${value}"]`);
      if(opt){
        $("#codigo").value = opt.dataset.codigo;
        $("#descripcion").value = opt.dataset.nombre;
        $("#precio").value = Number(opt.dataset.precio).toFixed(2);
        $("#id_isv").value = opt.dataset.isv;
      }
    }
  });
}
document.addEventListener("DOMContentLoaded", ()=>{
  // Cargar clientes y productos al iniciar
  cargarClientes();
  cargarProductos();

  // Eventos principales
  $("#btn-agregar").addEventListener("click", agregar);
  $("#btn-pagar").addEventListener("click", pagar);
});

