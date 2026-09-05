# Muestras

Carpeta para dejar documentos reales de ejemplo, para ajustar el sistema a
cómo son las marcas y los formularios de verdad.

## Por qué acá y no por chat

El asistente corre en un contenedor remoto y temporal: una carpeta suya no es
un lugar donde se puedan soltar archivos, y se borra al terminar la sesión.
**El repositorio es el canal compartido**: lo que se sube acá queda disponible
para revisar en cualquier sesión.

## Cómo subir archivos desde el navegador

1. Entrar a
   <https://github.com/RoqueLuis07/piedra_alta_saci/upload/claude/livestock-brand-vectorization-y95k3c/muestras/marcas_escaneadas>
   (ese enlace ya apunta a la rama y la carpeta correctas).
2. Arrastrar los PDF o imágenes a la zona de carga.
3. Abajo, en *Commit changes*, dejar seleccionada la rama
   `claude/livestock-brand-vectorization-y95k3c` y confirmar.
4. Avisar por chat: los archivos se leen desde el repositorio.

También se puede ir a mano: repositorio → carpeta `muestras/marcas_escaneadas`
→ botón **Add file** → **Upload files**.

Desde la computadora, con git:

```bash
git checkout claude/livestock-brand-vectorization-y95k3c
cp ~/Descargas/marcas_*.pdf muestras/marcas_escaneadas/
git add muestras/ && git commit -m "Muestras de marcas escaneadas" && git push
```

## Qué carpeta usar

| Carpeta | Qué va |
|---|---|
| `marcas_escaneadas/` | Los documentos escaneados donde están las marcas dibujadas a mano |
| `guias_oficiales/` | Alguna Guía de Traslado descargada del sitio de SENACSA |

## Qué muestras sirven más

Con dos o tres documentos alcanza para ajustar el pipeline. Conviene que sean
**representativos, no los mejores**:

- uno típico, del montón;
- **el peor que haya**: hoja manchada, trazo flojo, escaneo torcido, marcas
  que se salen del recuadro. Es el que define los umbrales;
- si hay varios formatos de planilla (libretas viejas, fichas, planillas
  nuevas), uno de cada uno.

Subirlos **como salieron del escáner**, sin recortar, sin "mejorar" el
contraste y sin comprimir. Si el archivo original es un TIFF o un PDF de
600 dpi, ese es el que sirve: una captura de pantalla o un PDF reducido
esconde justamente lo que hay que medir.

## Antes de subir: datos personales

Lo que se sube a un repositorio **queda en su historial**, y borrar el archivo
después no lo saca del historial. Los documentos de SENACSA traen nombres,
CI/RUC, códigos de establecimiento y números de boleta.

Para las muestras de marcas, con las **páginas que tienen los dibujos** alcanza:
no hace falta el documento completo. Si una página mezcla marcas y datos del
titular, tapar los datos antes de subir, o avisar y buscamos otra vía.

## Límites

- Por el navegador: hasta **25 MB por archivo** y 100 archivos por carga.
- Con git: hasta 100 MB por archivo.
- Un escaneo de una hoja a 600 dpi en PDF pesa entre 2 y 10 MB. Si un
  documento entero no entra, subir sólo las primeras páginas.

## Después

Estas muestras son para desarrollo. Cuando el sistema esté en producción, los
escaneos van a `datos/escaneos/` (fuera del repositorio) y esta carpeta se
puede vaciar.
