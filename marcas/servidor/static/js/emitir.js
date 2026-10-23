// Pantalla "Emitir PDF": subir la guía, dibujar/elegir marcas, ver la vista
// previa en vivo y generar el documento. Nada se guarda hasta "Generar PDF".
(function () {
  "use strict";
  var CFG = window.EMITIR;
  var $ = function (id) { return document.getElementById(id); };

  var estado = {
    pdf: null,          // File
    declaradas: null,   // marcas que declara la propia guía (si se pudieron leer)
    cupo: null,         // {complementarias_libres_rubro2, complementarias_libres_anexo, dominante_libre}
    dominante: null,    // ítem o null
    items: [],          // complementarias, en orden
    generado: false,
    secuencia: 0
  };
  var contadorDibujos = 0;

  // ---------- ítems ----------
  function itemCatalogo(m) {
    return { t: "cat", codigo: m.codigo, url: m.imagen_url, etiqueta: m.propietario || m.descripcion || "" };
  }
  function itemDibujo(blob, etiqueta) {
    return { t: "dib", blob: blob, url: URL.createObjectURL(blob), guardar: false, id: "dibujo_" + (contadorDibujos++), etiqueta: etiqueta || "Dibujada" };
  }
  (CFG.marcasPrevias || []).forEach(function (m) { if (m.imagen_url) estado.items.push(itemCatalogo(m)); });

  // ---------- lienzo ----------
  var lienzo = $("lienzo");
  var ctx = lienzo.getContext("2d");
  var trazos = [];
  var actual = null;

  function blanquear() { ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, lienzo.width, lienzo.height); }
  function redibujar() {
    blanquear();
    ctx.strokeStyle = "#000000"; ctx.lineCap = "round"; ctx.lineJoin = "round";
    trazos.forEach(function (t) {
      ctx.lineWidth = t.grosor;
      ctx.beginPath();
      t.puntos.forEach(function (p, i) { if (i === 0) ctx.moveTo(p.x, p.y); else ctx.lineTo(p.x, p.y); });
      if (t.puntos.length === 1) ctx.lineTo(t.puntos[0].x + 0.01, t.puntos[0].y);
      ctx.stroke();
    });
  }
  function punto(e) {
    var r = lienzo.getBoundingClientRect();
    return { x: (e.clientX - r.left) * (lienzo.width / r.width), y: (e.clientY - r.top) * (lienzo.height / r.height) };
  }
  lienzo.addEventListener("pointerdown", function (e) {
    e.preventDefault();
    lienzo.setPointerCapture(e.pointerId);
    actual = { grosor: parseInt($("grosor").value, 10), puntos: [punto(e)] };
    trazos.push(actual);
    redibujar();
  });
  lienzo.addEventListener("pointermove", function (e) {
    if (!actual) return;
    e.preventDefault();
    actual.puntos.push(punto(e));
    redibujar();
  });
  function soltar() { actual = null; }
  lienzo.addEventListener("pointerup", soltar);
  lienzo.addEventListener("pointercancel", soltar);
  blanquear();

  $("btn-deshacer").addEventListener("click", function () { trazos.pop(); redibujar(); });
  $("btn-borrar").addEventListener("click", function () { trazos = []; redibujar(); });

  function exportarDibujo(callback) {
    if (!trazos.length) { aviso("Dibujá la marca en el cuadro antes de agregarla."); return; }
    lienzo.toBlob(function (blob) {
      if (!blob) return;
      callback(blob);
      trazos = []; redibujar();
    }, "image/png");
  }
  $("btn-agregar").addEventListener("click", function () {
    exportarDibujo(function (blob) { estado.items.push(itemDibujo(blob)); cambio(); });
  });
  $("btn-dominante").addEventListener("click", function () {
    exportarDibujo(function (blob) { estado.dominante = itemDibujo(blob, "Dominante"); cambio(); });
  });
  $("campo-imagen").addEventListener("change", function (e) {
    Array.prototype.forEach.call(e.target.files, function (f) {
      if (f.type === "image/png") estado.items.push(itemDibujo(f, f.name));
    });
    e.target.value = "";
    cambio();
  });

  // ---------- bandeja ----------
  function ficha(item, numero, alQuitar) {
    var div = document.createElement("div");
    div.className = "ficha";
    if (numero) { var n = document.createElement("span"); n.className = "n"; n.textContent = numero; div.appendChild(n); }
    var q = document.createElement("button");
    q.type = "button"; q.className = "quitar"; q.title = "Quitar"; q.textContent = "×";
    q.addEventListener("click", alQuitar);
    div.appendChild(q);
    var img = document.createElement("img");
    img.src = item.url; img.alt = item.codigo || "marca dibujada";
    div.appendChild(img);
    var pie = document.createElement("div");
    pie.className = "pie";
    pie.textContent = item.t === "cat" ? item.codigo : (item.etiqueta || "Dibujada");
    div.appendChild(pie);
    if (item.t === "dib") {
      var l = document.createElement("label");
      var c = document.createElement("input");
      c.type = "checkbox"; c.checked = item.guardar;
      c.addEventListener("change", function () { item.guardar = c.checked; });
      l.appendChild(c);
      l.appendChild(document.createTextNode("Guardar en el catálogo"));
      div.appendChild(l);
    }
    return div;
  }
  function pintarBandeja() {
    var b = $("bandeja");
    b.textContent = "";
    if (!estado.items.length) {
      var v = document.createElement("div");
      v.className = "vacio";
      v.textContent = "Todavía no agregaste marcas complementarias.";
      b.appendChild(v);
    }
    estado.items.forEach(function (item, i) {
      b.appendChild(ficha(item, String(i + 1), function () { estado.items.splice(i, 1); cambio(); }));
    });
    var d = $("dominante-elegida");
    d.textContent = "";
    if (estado.dominante) {
      var grilla = document.createElement("div");
      grilla.className = "bandeja";
      grilla.style.maxWidth = "140px";
      grilla.appendChild(ficha(estado.dominante, "D", function () { estado.dominante = null; cambio(); }));
      d.appendChild(grilla);
    }
  }

  // ---------- avisos / cupo ----------
  function aviso(texto) {
    var e = $("estado-generar");
    e.textContent = texto;
  }
  function pintarCupo() {
    var c = $("cupo");
    c.textContent = "";
    if (!estado.cupo) { c.style.display = "none"; return; }
    c.style.display = "flex";
    var r2 = estado.cupo.complementarias_libres_rubro2;
    var anexo = estado.cupo.complementarias_libres_anexo;
    var usadas = estado.items.length;
    var enR2 = Math.min(usadas, r2);
    var enAnexo = Math.max(0, usadas - r2);
    function chip(texto, lleno) {
      var s = document.createElement("span");
      s.textContent = texto;
      if (lleno) s.className = "lleno";
      c.appendChild(s);
    }
    chip("Rubro 2: " + enR2 + " / " + r2 + " casillas", enR2 >= r2 && usadas > 0);
    chip("Anexo: " + enAnexo + " / " + anexo + " casillas", enAnexo > anexo);
    chip(estado.cupo.dominante_libre ? "Dominante: libre" : "Dominante: ya viene en el PDF", false);
    if (estado.declaradas) {
      // La guía declara el total de marcas (dominante + complementarias).
      var presentes = usadas + (estado.cupo.dominante_libre ? (estado.dominante ? 1 : 0) : 1);
      chip("Marcas: " + presentes + " de " + estado.declaradas + " que declara la guía", presentes !== estado.declaradas);
    }
    var nota = $("nota-dominante");
    var sinLugar = !estado.cupo.dominante_libre;
    $("btn-dominante").disabled = sinLugar;
    $("btn-catalogo-dom").disabled = sinLugar;
    nota.textContent = sinLugar
      ? "El PDF ya trae la dominante dibujada, así que no se toca."
      : "Si el PDF no la trae, dibujala en el cuadro de abajo y tocá «Usar como dominante».";
  }
  function actualizarBoton() {
    var sobran = geom ? Math.max(0, estado.items.length - geom.estaciones.length) : 0;
    var listo = !!estado.pdf && (estado.items.length > 0 || estado.dominante) && !estado.generado && !sobran;
    $("btn-generar").disabled = !listo;
    if (estado.generado) return;
    if (!estado.pdf) aviso("Subí el PDF para empezar.");
    else if (sobran) aviso("Sobra" + (sobran === 1 ? "" : "n") + " " + sobran + " marca" + (sobran === 1 ? "" : "s") + ": quitá " + (sobran === 1 ? "una" : sobran) + " para poder generar el PDF.");
    else if (!listo) aviso("Agregá al menos una marca.");
    else {
      var n = estado.items.length;
      var partes = [];
      if (n) partes.push(n + (n === 1 ? " marca complementaria" : " marcas complementarias"));
      if (estado.dominante) partes.push("la dominante");
      aviso("Listo para estampar: " + partes.join(" y ") + ".");
    }
  }
  function cambio() {
    pintarBandeja(); pintarCupo(); actualizarBoton(); pintarVista();
    if ((ultimoRespaldo || []).length) pintarRespaldo(ultimoRespaldo);   // las marcas ya usadas se ven marcadas
  }

  // ---------- pedido al servidor ----------
  function armarPedido() {
    var f = new FormData();
    f.append("csrf_token", CFG.csrf);
    f.append("pdf_guia", estado.pdf);
    var orden = estado.items.map(function (it) {
      if (it.t === "cat") return { t: "cat", codigo: it.codigo };
      f.append(it.id, it.blob, it.id + ".png");
      return { t: "dib", archivo: it.id, guardar: it.guardar };
    });
    f.append("orden", JSON.stringify(orden));
    if (estado.dominante) {
      var d = estado.dominante;
      if (d.t === "cat") f.append("dominante", JSON.stringify({ t: "cat", codigo: d.codigo }));
      else { f.append(d.id, d.blob, d.id + ".png"); f.append("dominante", JSON.stringify({ t: "dib", archivo: d.id, guardar: d.guardar })); }
    }
    return f;
  }

  // ---------- vista previa ----------
  // La guía se lee UNA vez en el servidor (/emitir/analizar): páginas, cupo y dónde cae cada marca.
  // Después la vista previa se arma acá, en el navegador: agregar o quitar una marca es instantáneo.
  var geom = null, capas = [], ultimoN = -1, reintento = false;
  function pct(v, t) { return (v / t * 100) + "%"; }
  function ubicar(el, r, margen) {
    var P = geom.paginas[r.pagina];
    el.style.left = pct(r.x + margen, P.ancho);
    el.style.top = pct(P.alto - (r.y + r.alto) + margen, P.alto);
    el.style.width = pct(r.ancho - 2 * margen, P.ancho);
    el.style.height = pct(r.alto - 2 * margen, P.alto);
  }
  function analizarPdf() {
    var mia = ++estado.secuencia;
    geom = null; capas = []; ultimoN = -1;
    $("vp-estado").textContent = "Leyendo la guía…";
    $("vista").style.opacity = "0.55";
    var f = new FormData();
    f.append("csrf_token", CFG.csrf);
    f.append("pdf_guia", estado.pdf);
    fetch(CFG.urls.analizar, { method: "POST", body: f })
      .then(function (r) { return r.json(); })
      .then(function (res) {
        if (mia !== estado.secuencia) return;
        var aviso_pdf = $("aviso-pdf");
        if (res.error) {
          aviso_pdf.textContent = res.error; aviso_pdf.style.display = "block";
          pintarRespaldo([]);
          $("vp-estado").textContent = "";
          $("vista").textContent = "";
          return;
        }
        aviso_pdf.style.display = "none";
        geom = res;
        estado.cupo = res.cupo; pintarCupo();
        if (res.encabezado) { estado.declaradas = res.encabezado.marcas_declaradas || null; autocompletar(res.encabezado); pintarCupo(); }
        pintarRespaldo(res.respaldo_validado || []);
        construirVista();
        desbloquear();
      })
      .catch(function () { $("vp-estado").textContent = "No se pudo leer la guía. Probá de nuevo."; })
      .finally(function () { $("vista").style.opacity = "1"; });
  }
  function construirVista() {
    var v = $("vista");
    v.textContent = "";
    capas = geom.paginas.map(function (P, i) {
      var pg = document.createElement("div");
      pg.className = "pg";
      pg.style.aspectRatio = P.ancho + " / " + P.alto;
      var img = document.createElement("img");
      img.alt = "Página " + (i + 1) + " de la guía";
      img.loading = i < 2 ? "eager" : "lazy";
      img.src = geom.url_pagina.replace(/\/0\.jpg$/, "/" + i + ".jpg");
      img.addEventListener("error", function () {
        // la memoria del servidor se reinició: se vuelve a leer la guía una sola vez
        if (!reintento) { reintento = true; analizarPdf(); }
      });
      var capa = document.createElement("div");
      capa.className = "capa";
      pg.appendChild(img); pg.appendChild(capa);
      v.appendChild(pg);
      return capa;
    });
    pintarVista();
  }
  function sello(item, rects) {
    rects.forEach(function (r) {
      var im = document.createElement("img");
      im.className = "est"; im.src = item.url; im.alt = "";
      ubicar(im, r, Math.min(r.ancho, r.alto) * 0.10);
      capas[r.pagina].appendChild(im);
    });
  }
  function resaltar(rects, texto, clase) {
    var primero = null;
    rects.forEach(function (r, i) {
      var d = document.createElement("div");
      d.className = "sig " + (i === 0 ? "" : "suave ") + (clase || "");
      ubicar(d, r, 0);
      if (i === 0) { var t = document.createElement("span"); t.textContent = texto; d.appendChild(t); primero = d; }
      capas[r.pagina].appendChild(d);
    });
    return primero;
  }
  function pintarVista() {
    actualizarBoton();
    if (!geom || !capas.length) return;
    capas.forEach(function (c) { c.textContent = ""; });
    var E = geom.estaciones, n = estado.items.length;
    if (estado.dominante) sello(estado.dominante, geom.dominante);
    estado.items.forEach(function (item, k) { if (E[k]) sello(item, E[k]); });
    var destino = null;
    if (n < E.length) destino = resaltar(E[n], "Acá va la marca " + (n + 1), "");
    if (!estado.dominante && geom.dominante.length) {
      var d = resaltar(geom.dominante, "Dominante", "dom");
      if (!destino) destino = d;
    }
    var r2 = geom.cupo.complementarias_libres_rubro2;
    var enR2 = Math.min(n, r2), enAnexo = Math.max(0, Math.min(n, E.length) - r2);
    var txt = "En el Rubro 2: " + enR2 + " · En el Anexo: " + enAnexo + (estado.dominante ? " · Dominante estampada" : "");
    if (n > E.length) txt = "Sobra" + (n - E.length === 1 ? "" : "n") + " " + (n - E.length) + " marca" + (n - E.length === 1 ? "" : "s") + ": no " + (n - E.length === 1 ? "entra" : "entran") + " en esta guía. " + txt;
    else if (n === E.length) txt += " · No quedan casillas libres";
    $("vp-estado").textContent = txt;
    if (destino && n !== ultimoN) {
      ultimoN = n;
      var v = $("vista");
      var y = destino.getBoundingClientRect().top - v.getBoundingClientRect().top + v.scrollTop;
      v.scrollTo({ top: Math.max(0, y - 80), behavior: "smooth" });
    }
  }

  // ---------- respaldo del Rubro 4 ----------
  function marcaYaEstaUsada(codigo) {
    return (estado.dominante && estado.dominante.codigo === codigo) || estado.items.some(function (i) { return i.codigo === codigo; });
  }
  function usarMarcaDeRespaldo(m) {
    if (marcaYaEstaUsada(m.codigo)) return;
    var item = itemCatalogo(m);
    if (m.tipo === "dominante" && estado.cupo && estado.cupo.dominante_libre && !estado.dominante) estado.dominante = item;
    else estado.items.push(item);
    cambio();
  }
  var ultimoRespaldo = [];
  function pintarRespaldo(lista) {
    ultimoRespaldo = lista;
    var paso = $("paso-respaldo"), resumen = $("respaldo-resumen"), cont = $("respaldo-lista");
    cont.textContent = ""; resumen.textContent = "";
    if (!lista.length) { paso.style.display = "none"; return; }
    paso.style.display = "block";
    var guias = lista.filter(function (f) { return f.cod === "G"; });
    var boletas = lista.filter(function (f) { return f.cod === "B"; });
    var halladas = guias.filter(function (f) { return f.encontrada; });
    var conMarcas = halladas.filter(function (f) { return f.marcas.length; });
    resumen.textContent = guias.length + " guías de respaldo leídas: " + halladas.length + " ya están cargadas en el sistema (" +
      conMarcas.length + " con marcas para elegir) y " + (guias.length - halladas.length) + " todavía no." +
      (boletas.length ? " Boleta de marca: " + boletas.map(function (b) { return b.numero; }).join(", ") + "." : "");
    var faltan = guias.filter(function (f) { return !f.encontrada; });
    if (faltan.length) {
      var det = document.createElement("details"); det.className = "guia-resp";
      var sum = document.createElement("summary"); sum.className = "no"; sum.style.cursor = "pointer";
      sum.textContent = "✗ " + faltan.length + " guía" + (faltan.length === 1 ? "" : "s") + " de respaldo todavía no " + (faltan.length === 1 ? "está cargada" : "están cargadas") + " (subilas en Guías escaneadas)";
      det.appendChild(sum);
      var lis = document.createElement("div"); lis.className = "sub mono"; lis.style.marginTop = "6px";
      lis.textContent = faltan.map(function (f) { return f.numero + (f.fecha ? " (" + f.fecha + ")" : ""); }).join(" · ");
      det.appendChild(lis);
      cont.appendChild(det);
    }
    guias.filter(function (f) { return f.encontrada; }).forEach(function (f) {
      var caja = document.createElement("div"); caja.className = "guia-resp";
      var cab = document.createElement("div"); cab.className = "cab";
      var num = document.createElement("span"); num.className = "num"; num.textContent = "G " + f.numero; cab.appendChild(num);
      if (f.fecha) { var fe = document.createElement("span"); fe.className = "sub"; fe.textContent = f.fecha; cab.appendChild(fe); }
      var est = document.createElement("span");
      if (f.encontrada) {
        est.className = "ok";
        est.textContent = "✓ está en el sistema" + (f.marcas.length ? " · " + f.marcas.length + " marca" + (f.marcas.length === 1 ? "" : "s") : " · sin marcas sacadas todavía");
      } else { est.className = "no"; est.textContent = "✗ no está cargada (subila en Guías escaneadas)"; }
      cab.appendChild(est);
      if (f.url_guia) { var a = document.createElement("a"); a.href = f.url_guia; a.target = "_blank"; a.rel = "noopener"; a.textContent = "ver la guía ↗"; cab.appendChild(a); }
      caja.appendChild(cab);
      if (f.marcas.length) {
        var fila = document.createElement("div"); fila.className = "marcas-resp";
        f.marcas.forEach(function (m) {
          var b = document.createElement("button"); b.type = "button"; b.className = "marca-resp";
          var usada = marcaYaEstaUsada(m.codigo);
          if (usada) { b.classList.add("usada"); b.disabled = true; }
          b.title = usada ? "Ya está en esta guía" : "Usar " + m.codigo + " en esta guía";
          var im = document.createElement("img"); im.src = m.imagen_url; im.alt = "Marca " + m.codigo; im.loading = "lazy"; b.appendChild(im);
          var cod = document.createElement("span"); cod.textContent = m.codigo; b.appendChild(cod);
          var t = document.createElement("span"); t.className = "tipo"; t.textContent = usada ? "✓ en la guía" : (m.tipo === "dominante" ? "Dominante" : "Compl."); b.appendChild(t);
          b.addEventListener("click", function () { usarMarcaDeRespaldo(m); });
          fila.appendChild(b);
        });
        caja.appendChild(fila);
      }
      cont.appendChild(caja);
    });
  }

  function desbloquear() {
    ["paso-datos", "paso-dominante", "paso-marcas"].forEach(function (id) { $(id).classList.remove("bloqueado"); });
  }

  // ---------- datos leídos del PDF ----------
  function autocompletar(enc) {
    if (!$("f-numero")) return;
    function poner(id, valor) {
      var el = $(id);
      if (el && valor && !el.dataset.tocado) el.value = valor;
    }
    poner("f-numero", enc.numero_orden);
    poner("f-vendedor", enc.vendedor_nombre);
    poner("f-comprador", enc.comprador_nombre);
    poner("f-cantidad", enc.cantidad_animales);
  }
  ["f-numero", "f-vendedor", "f-comprador", "f-cantidad"].forEach(function (id) {
    var el = $(id);
    if (el) el.addEventListener("input", function () { el.dataset.tocado = "1"; });
  });

  // ---------- PDF ----------
  function elegirPdf(archivo) {
    if (!archivo) return;
    if (archivo.type !== "application/pdf" && !/\.pdf$/i.test(archivo.name)) { aviso("Ese archivo no es un PDF."); return; }
    estado.pdf = archivo;
    estado.generado = false;
    $("zona-pdf-titulo").textContent = archivo.name;
    $("zona-pdf-sub").textContent = "Tocá para cambiar el PDF.";
    reintento = false;
    cambio();
    analizarPdf();
  }
  $("campo-pdf").addEventListener("change", function (e) { elegirPdf(e.target.files[0]); });
  var zona = $("zona-pdf");
  ["dragenter", "dragover"].forEach(function (ev) { zona.addEventListener(ev, function (e) { e.preventDefault(); zona.classList.add("arrastrando"); }); });
  ["dragleave", "drop"].forEach(function (ev) { zona.addEventListener(ev, function (e) { e.preventDefault(); zona.classList.remove("arrastrando"); }); });
  zona.addEventListener("drop", function (e) { elegirPdf(e.dataTransfer.files[0]); });

  // ---------- catálogo ----------
  var modal = $("modal-catalogo"), buscador = $("buscador"), resultados = $("resultados"), espera = null;
  var comoDominante = false;
  function abrirCatalogo(dominante) {
    comoDominante = dominante;
    modal.style.display = "flex"; buscador.value = ""; resultados.textContent = ""; buscador.focus();
  }
  $("btn-catalogo").addEventListener("click", function () { abrirCatalogo(false); });
  $("btn-catalogo-dom").addEventListener("click", function () { abrirCatalogo(true); });
  $("cerrar-catalogo").addEventListener("click", function () { modal.style.display = "none"; });
  modal.addEventListener("click", function (e) { if (e.target === modal) modal.style.display = "none"; });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") modal.style.display = "none"; });
  buscador.addEventListener("input", function () {
    clearTimeout(espera);
    var q = buscador.value.trim();
    if (!q) { resultados.textContent = ""; return; }
    espera = setTimeout(function () {
      fetch(CFG.urls.catalogo + "?q=" + encodeURIComponent(q))
        .then(function (r) { return r.json(); })
        .then(function (filas) {
          resultados.textContent = "";
          if (!filas.length) { var s = document.createElement("div"); s.className = "vacio"; s.textContent = "Sin resultados."; resultados.appendChild(s); }
          filas.forEach(function (m) {
            var b = document.createElement("button");
            b.type = "button";
            if (m.imagen_url) {
              var img = document.createElement("img"); img.src = m.imagen_url; img.alt = m.codigo; b.appendChild(img);
            }
            var cod = document.createElement("div"); cod.className = "cod"; cod.textContent = m.codigo; b.appendChild(cod);
            var sub = document.createElement("div"); sub.className = "sub";
            sub.textContent = !m.imagen_url ? "Sin imagen" : (m.estado === "revisar" ? "En revisión" : (m.propietario || ""));
            b.appendChild(sub);
            if (!m.imagen_url) b.disabled = true;
            b.addEventListener("click", function () {
              if (comoDominante) estado.dominante = itemCatalogo(m);
              else if (!estado.items.some(function (i) { return i.codigo === m.codigo; })) estado.items.push(itemCatalogo(m));
              modal.style.display = "none";
              cambio();
            });
            resultados.appendChild(b);
          });
        });
    }, 250);
  });

  // ---------- generar ----------
  $("btn-generar").addEventListener("click", function () {
    var boton = $("btn-generar");
    boton.disabled = true;
    aviso("Generando el PDF y guardándolo en el Archivo…");
    var f = armarPedido();
    if (CFG.operacionId) f.append("operacion_id", CFG.operacionId);
    else {
      f.append("tipo_operacion", "venta");
      f.append("numero_guia", $("f-numero").value);
      f.append("vendedor_nombre", $("f-vendedor").value);
      f.append("comprador_nombre", $("f-comprador").value);
      f.append("cantidad_animales", $("f-cantidad").value);
    }
    fetch(CFG.urls.generar, { method: "POST", body: f })
      .then(function (r) {
        var tipo = r.headers.get("Content-Type") || "";
        if (r.ok && tipo.indexOf("application/pdf") === 0) {
          var disp = r.headers.get("Content-Disposition") || "";
          var m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disp);
          var nombre = m ? decodeURIComponent(m[1]) : "guia.pdf";
          return r.blob().then(function (blob) {
            var a = document.createElement("a");
            a.href = URL.createObjectURL(blob); a.download = nombre;
            document.body.appendChild(a); a.click(); a.remove();
            estado.generado = true;
            var e = $("estado-generar");
            e.textContent = "";
            e.appendChild(document.createTextNode("Listo: se descargó " + nombre + " y quedó guardado en el Archivo. "));
            var ver = document.createElement("a"); ver.href = CFG.urls.archivo; ver.textContent = "Ver Archivo";
            e.appendChild(ver);
            e.appendChild(document.createTextNode(" · "));
            var otro = document.createElement("a"); otro.href = window.location.pathname; otro.textContent = "Emitir otro";
            e.appendChild(otro);
          });
        }
        return r.json().catch(function () { return { error: "No se pudo generar el documento." }; })
          .then(function (res) { aviso(res.error || "No se pudo generar el documento."); boton.disabled = false; });
      })
      .catch(function () { aviso("Se cortó la conexión. No se guardó nada; probá de nuevo."); boton.disabled = false; });
  });

  cambio();
})();
