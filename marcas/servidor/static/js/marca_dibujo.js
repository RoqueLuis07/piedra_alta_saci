// Deja dibujar una marca a mano (mouse, dedo o lápiz) y la agrega como si
// fuera un archivo más al <input type="file"> indicado -- así el resto del
// formulario (y del servidor) no necesita saber si la imagen vino de un
// archivo subido o de un trazo hecho en pantalla.
function inicializarDibujoMarca(idInputArchivo, idContenedorDibujo) {
  const inputArchivo = document.getElementById(idInputArchivo);
  const contenedor = document.getElementById(idContenedorDibujo);
  if (!inputArchivo || !contenedor) return;

  const canvas = contenedor.querySelector("canvas");
  const ctx = canvas.getContext("2d");
  let trazos = [];
  let trazoActual = null;

  function limpiarCanvas() {
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }

  function redibujar() {
    limpiarCanvas();
    for (const trazo of trazos) {
      ctx.strokeStyle = "#000000";
      ctx.lineWidth = trazo.grosor;
      ctx.lineCap = "round";
      ctx.lineJoin = "round";
      ctx.beginPath();
      trazo.puntos.forEach((p, i) => (i === 0 ? ctx.moveTo(p.x, p.y) : ctx.lineTo(p.x, p.y)));
      ctx.stroke();
    }
  }
  limpiarCanvas();

  function posicion(evento) {
    const rect = canvas.getBoundingClientRect();
    const punto = evento.touches ? evento.touches[0] : evento;
    return {
      x: (punto.clientX - rect.left) * (canvas.width / rect.width),
      y: (punto.clientY - rect.top) * (canvas.height / rect.height),
    };
  }

  function empezarTrazo(evento) {
    evento.preventDefault();
    const grosor = parseInt(contenedor.querySelector(".grosor-dibujo").value, 10);
    trazoActual = { grosor, puntos: [posicion(evento)] };
    trazos.push(trazoActual);
  }
  function seguirTrazo(evento) {
    if (!trazoActual) return;
    evento.preventDefault();
    trazoActual.puntos.push(posicion(evento));
    redibujar();
  }
  function terminarTrazo() {
    trazoActual = null;
  }

  canvas.addEventListener("mousedown", empezarTrazo);
  canvas.addEventListener("mousemove", seguirTrazo);
  window.addEventListener("mouseup", terminarTrazo);
  canvas.addEventListener("touchstart", empezarTrazo, { passive: false });
  canvas.addEventListener("touchmove", seguirTrazo, { passive: false });
  canvas.addEventListener("touchend", terminarTrazo);

  contenedor.querySelector(".dibujo-deshacer").addEventListener("click", () => {
    trazos.pop();
    redibujar();
  });
  contenedor.querySelector(".dibujo-borrar").addEventListener("click", () => {
    trazos = [];
    redibujar();
  });

  contenedor.querySelector(".dibujo-usar").addEventListener("click", () => {
    if (!trazos.length) return;
    canvas.toBlob((blob) => {
      if (!blob) return;
      const archivo = new File([blob], `dibujo_${Date.now()}.png`, { type: "image/png" });
      const datos = new DataTransfer();
      if (inputArchivo.multiple) {
        Array.from(inputArchivo.files).forEach((f) => datos.items.add(f));
      }
      datos.items.add(archivo);
      inputArchivo.files = datos.files;
      inputArchivo.dispatchEvent(new Event("change"));
      trazos = [];
      redibujar();
    }, "image/png");
  });
}

// Miniaturas de lo que hay cargado en el <input type="file"> ahora mismo --
// sea un archivo subido o un dibujo agregado por inicializarDibujoMarca.
function inicializarVistaPreviaImagenes(idInputArchivo, idContenedorPrevia) {
  const inputArchivo = document.getElementById(idInputArchivo);
  const contenedor = document.getElementById(idContenedorPrevia);
  if (!inputArchivo || !contenedor) return;
  inputArchivo.addEventListener("change", () => {
    contenedor.innerHTML = "";
    Array.from(inputArchivo.files).forEach((archivo) => {
      const img = document.createElement("img");
      img.width = 56;
      img.height = 56;
      img.alt = archivo.name;
      img.style.objectFit = "contain";
      img.style.border = "1px solid var(--borde-fina)";
      img.style.background = "#fff";
      if (archivo.type === "image/svg+xml") {
        img.src = URL.createObjectURL(archivo);
      } else {
        const lector = new FileReader();
        lector.onload = (e) => (img.src = e.target.result);
        lector.readAsDataURL(archivo);
      }
      contenedor.appendChild(img);
    });
  });
}
