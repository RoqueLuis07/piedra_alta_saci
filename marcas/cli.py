"""Interfaz de línea de comandos: python -m marcas <comando>."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from marcas import config, db
from marcas.pdf import (
    ItemPlanilla,
    generar_hoja_control,
    generar_planilla,
    generar_plantilla_captura,
)
from marcas.pdf.guia import completar_guia, hojas_de_anexo
from marcas.vectorizacion.pipeline import procesar_lote, resumen
from marcas.vectorizacion.trazar import potrace_disponible


# --------------------------------------------------------------------------
# comandos
# --------------------------------------------------------------------------
def cmd_captura(args) -> int:
    ruta = generar_plantilla_captura(
        args.salida, hojas=args.hojas, prefijo=args.prefijo,
        columnas=args.columnas, filas=args.filas,
        numero_inicial=args.desde,
    )
    casillas = args.hojas * (args.columnas or config.PLANILLA_COLUMNAS) * (
        args.filas or config.PLANILLA_FILAS
    )
    print(f"Plantilla de captura: {ruta} ({args.hojas} hojas, {casillas} casillas)")
    print("Imprimir al 100% (sin 'ajustar a página') para no deformar la grilla.")
    return 0


def cmd_procesar(args) -> int:
    if not potrace_disponible() and not args.sin_svg:
        print("Aviso: no se encontró 'potrace'; se generarán sólo los PNG.\n"
              "       Instalar con: sudo apt install potrace", file=sys.stderr)

    def avance(i, total, res):
        estado = f"ERROR: {res.error}" if res.error else (
            f"{len(res.marcas)} marcas"
            + (f", {res.a_revisar} a revisar" if res.a_revisar else "")
        )
        print(f"  [{i}/{total}] {res.hoja}: {estado}")

    print(f"Procesando {args.entrada} (modo {args.modo})...")
    resultados, manifiesto = procesar_lote(
        args.entrada,
        args.png,
        None if args.sin_svg else args.svg,
        modo=args.modo,
        ruta_codigos=args.codigos,
        manifiesto=args.manifiesto,
        lienzo=args.lienzo,
        dpi=args.dpi,
        al_avanzar=avance,
    )
    r = resumen(resultados)
    print(
        f"\nHojas: {r['hojas']} ({r['hojas_con_error']} con error) | "
        f"marcas: {r['marcas_generadas']}/{r['marcas_detectadas']} | "
        f"a revisar: {r['a_revisar']}"
    )
    print(f"Manifiesto: {manifiesto}")
    if r["a_revisar"]:
        print("Revisar las filas con revisar=True antes de importar al catálogo.")
    return 1 if r["hojas_con_error"] else 0


def cmd_importar(args) -> int:
    stats = db.importar_manifiesto(args.manifiesto, args.db)
    print(
        f"Catálogo actualizado: {stats['nuevas']} nuevas, "
        f"{stats['actualizadas']} actualizadas, "
        f"{stats['omitidas']} omitidas (casillas vacías)."
    )
    return 0


def _seleccionar(args) -> list:
    if args.codigos:
        codigos = [c.strip() for c in args.codigos.split(",") if c.strip()]
        return db.obtener_marcas(codigos, args.db)
    if args.lista:
        codigos = [
            linea.strip()
            for linea in Path(args.lista).read_text(encoding="utf-8").splitlines()
            if linea.strip() and not linea.startswith("#")
        ]
        return db.obtener_marcas(codigos, args.db)
    return db.listar_marcas(
        busqueda=args.buscar, estado=args.estado, limite=args.limite, ruta_db=args.db
    )


def cmd_listar(args) -> int:
    filas = _seleccionar(args)
    print(f"{'CÓDIGO':<24} {'ESTADO':<8} {'PROPIETARIO':<24} TINTA%")
    for f in filas:
        print(
            f"{f['codigo']:<24} {f['estado']:<8} "
            f"{(f['propietario'] or '-'):<24} {f['tinta_pct'] or 0:>6}"
        )
    print(f"\n{len(filas)} marcas.")
    return 0


def cmd_planilla(args) -> int:
    filas = _seleccionar(args)
    if not filas:
        print("La selección no devolvió marcas.", file=sys.stderr)
        return 1
    items = [ItemPlanilla.desde_fila(f) for f in filas]
    ruta = generar_planilla(
        items, args.salida, titulo=args.titulo, subtitulo=args.subtitulo,
        columnas=args.columnas, filas=args.filas, apaisado=args.apaisado,
        casillas_vacias=args.vacias,
    )
    if args.guardar:
        pid = db.guardar_planilla(
            args.titulo, [f["codigo"] for f in filas], args.subtitulo, args.db
        )
        print(f"Selección guardada como planilla #{pid}.")
    print(f"Planilla: {ruta} ({len(items)} marcas)")
    return 0


def cmd_guia(args) -> int:
    """Estampa las marcas elegidas sobre la guía oficial descargada."""
    if args.inspeccionar:
        hojas = hojas_de_anexo(args.pdf)
        if not hojas:
            print("No se reconocieron hojas de anexo en ese PDF.", file=sys.stderr)
            return 1
        print(f"{'PÁG':<5} {'COPIA':<15} {'HOJA':<5} {'CASILLAS':<9} LIBRES")
        for h in hojas:
            print(f"{h.pagina + 1:<5} {h.copia:<15} {h.orden + 1:<5} "
                  f"{len(h.casillas):<9} {len(h.libres)}")
        por_copia = {}
        for h in hojas:
            por_copia[h.copia] = por_copia.get(h.copia, 0) + len(h.libres)
        print(f"\nCapacidad: {min(por_copia.values())} marcas por copia.")
        return 0

    filas = _seleccionar(args)
    if not filas:
        print("La selección no devolvió marcas.", file=sys.stderr)
        return 1
    imagenes = []
    for f in filas:
        ruta = Path(f["archivo_png"])
        imagenes.append(ruta if ruta.is_absolute() else config.RAIZ / ruta)
    try:
        r = completar_guia(args.pdf, imagenes, args.salida, dpi=args.dpi)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(
        f"Guía completada: {r['salida']} ({r['peso_kb']} kB)\n"
        f"  {r['marcas']} marcas en {len(r['paginas_modificadas'])} páginas de anexo\n"
        f"  copias: {', '.join(r['copias'])}"
    )
    return 0


def cmd_control(args) -> int:
    filas = _seleccionar(args)
    items = [ItemPlanilla.desde_fila(f) for f in filas]
    ruta = generar_hoja_control(
        items, args.salida, subtitulo=args.subtitulo or f"{len(items)} marcas"
    )
    print(f"Hoja de control: {ruta} ({len(items)} marcas)")
    return 0


def cmd_estado(args) -> int:
    e = db.estadisticas(args.db)
    print(f"Marcas registradas .... {e['marcas']}")
    for estado, cantidad in sorted(e["por_estado"].items()):
        print(f"  {estado:<18} {cantidad}")
    print(f"Propietarios .......... {e['propietarios']}")
    print(f"Sin propietario ....... {e['sin_propietario']}")
    print(f"Imágenes duplicadas ... {e['imagenes_duplicadas']}")
    return 0


def cmd_web(args) -> int:
    from marcas.web.app import crear_app

    app = crear_app(args.db)
    print(f"Selector de marcas en http://{args.host}:{args.puerto}  (Ctrl+C para salir)")
    app.run(host=args.host, port=args.puerto, debug=args.debug)
    return 0


def cmd_demo(args) -> int:
    """Recorrido completo con hojas sintéticas: captura -> PNG -> catálogo -> PDF."""
    print("1) Generando hojas de prueba...")
    subprocess.run(
        [sys.executable, "scripts/generar_hoja_demo.py",
         "--hojas", str(args.hojas), "--salida", str(config.DIR_ESCANEOS)],
        check=True, cwd=config.RAIZ,
    )
    print("2) Digitalizando...")
    resultados, manifiesto = procesar_lote(config.DIR_ESCANEOS)
    print("  ", resumen(resultados))
    print("3) Cargando el catálogo...")
    print("  ", db.importar_manifiesto(manifiesto, args.db))
    print("4) Armando los PDF...")
    filas = db.listar_marcas(ruta_db=args.db)
    items = [ItemPlanilla.desde_fila(f) for f in filas]
    print("  ", generar_planilla(items, config.DIR_SALIDA / "planilla.pdf",
                                 subtitulo="Piedra Alta S.A.C.I."))
    print("  ", generar_hoja_control(items, config.DIR_SALIDA / "hoja_control.pdf"))
    print("  ", generar_plantilla_captura(config.DIR_SALIDA / "plantilla_captura.pdf",
                                          hojas=2))
    print("\nListo. Para elegir marcas a mano: python -m marcas web")
    return 0


# --------------------------------------------------------------------------
def construir_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="marcas",
        description="Gestión de marcas de ganado: digitalización, catálogo y planillas.",
    )
    ap.add_argument("--db", type=Path, default=None, help="ruta de la base SQLite")
    sub = ap.add_subparsers(dest="comando", required=True)

    p = sub.add_parser("captura", help="PDF en blanco para dibujar las marcas")
    p.add_argument("--salida", type=Path, default=config.DIR_SALIDA / "plantilla_captura.pdf")
    p.add_argument("--hojas", type=int, default=5)
    p.add_argument("--desde", type=int, default=1, help="número de la primera hoja")
    p.add_argument("--prefijo", default="H")
    p.add_argument("--columnas", type=int, default=None)
    p.add_argument("--filas", type=int, default=None)
    p.set_defaults(func=cmd_captura)

    p = sub.add_parser("procesar", help="escaneos -> PNG + SVG + manifiesto")
    p.add_argument("entrada", type=Path, nargs="?", default=config.DIR_ESCANEOS,
                   help="carpeta o archivo; acepta imágenes y PDF escaneados")
    p.add_argument("--png", type=Path, default=config.DIR_PNG)
    p.add_argument("--svg", type=Path, default=config.DIR_SVG)
    p.add_argument("--sin-svg", action="store_true", help="no vectorizar (más rápido)")
    p.add_argument("--modo", choices=["grilla", "libre"], default="grilla")
    p.add_argument("--codigos", type=Path, default=None,
                   help="CSV hoja,fila,columna,codigo para nombrar las marcas")
    p.add_argument("--manifiesto", type=Path, default=None)
    p.add_argument("--lienzo", type=int, default=None, help=f"px (def. {config.LIENZO_PX})")
    p.add_argument("--dpi", type=int, default=400,
                   help="resolución al rasterizar un PDF sin imagen escaneada")
    p.set_defaults(func=cmd_procesar)

    p = sub.add_parser("importar", help="manifiesto -> catálogo")
    p.add_argument("manifiesto", type=Path, nargs="?",
                   default=config.DIR_MARCAS / "manifiesto.csv")
    p.set_defaults(func=cmd_importar)

    def opciones_seleccion(p):
        p.add_argument("--codigos", default=None, help="lista separada por comas")
        p.add_argument("--lista", type=Path, default=None, help="archivo con un código por línea")
        p.add_argument("--buscar", default=None)
        p.add_argument("--estado", default=None, choices=["activa", "revisar", "baja"])
        p.add_argument("--limite", type=int, default=None)
        p.add_argument("--todas", action="store_true",
                       help="todo el catálogo (es lo que pasa si no se filtra)")

    p = sub.add_parser("listar", help="ver el catálogo")
    opciones_seleccion(p)
    p.set_defaults(func=cmd_listar)

    p = sub.add_parser("planilla", help="PDF general con las marcas en casillas")
    opciones_seleccion(p)
    p.add_argument("--salida", type=Path, default=config.DIR_SALIDA / "planilla.pdf")
    p.add_argument("--titulo", default="Registro de marcas")
    p.add_argument("--subtitulo", default="Piedra Alta S.A.C.I.")
    p.add_argument("--columnas", type=int, default=None)
    p.add_argument("--filas", type=int, default=None)
    p.add_argument("--apaisado", action="store_true")
    p.add_argument("--vacias", type=int, default=0, help="casillas en blanco al final")
    p.add_argument("--guardar", action="store_true", help="registrar la selección")
    p.set_defaults(func=cmd_planilla)

    p = sub.add_parser("control", help="hoja de contactos para revisar un lote")
    opciones_seleccion(p)
    p.add_argument("--salida", type=Path, default=config.DIR_SALIDA / "hoja_control.pdf")
    p.add_argument("--subtitulo", default=None)
    p.set_defaults(func=cmd_control)

    p = sub.add_parser("guia", help="estampar marcas sobre la guía oficial descargada")
    opciones_seleccion(p)
    p.add_argument("--pdf", type=Path, required=True,
                   help="guía de traslado descargada del sitio oficial")
    p.add_argument("--salida", type=Path, default=config.DIR_SALIDA / "guia_completada.pdf")
    p.add_argument("--dpi", type=int, default=300)
    p.add_argument("--inspeccionar", action="store_true",
                   help="sólo mostrar las hojas de anexo y las casillas libres")
    p.set_defaults(func=cmd_guia)

    p = sub.add_parser("estado", help="resumen del catálogo")
    p.set_defaults(func=cmd_estado)

    p = sub.add_parser("web", help="selector visual de marcas en el navegador")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--puerto", type=int, default=5000)
    p.add_argument("--debug", action="store_true")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("demo", help="recorrido completo con hojas sintéticas")
    p.add_argument("--hojas", type=int, default=3)
    p.set_defaults(func=cmd_demo)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
