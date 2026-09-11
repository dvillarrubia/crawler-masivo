"""Repone en `page_content` el titular (h1) que el extractor tiro al rastrear.

Hasta el fix del hero (CLAUDE.md 10 y 10b) habia plantillas cuyo `<h1>` vivia en
un bloque que trafilatura descartaba: la pagina quedaba en la base de datos con
el cuerpo entero y sin su titular, que es justo lo que mas pesa. Los censos ya
rastreados arrastran ese hueco.

El HTML crudo no se guarda, asi que desde la base de datos NO se puede
reconstruir el hero completo (categoria, subtitulo, claim): solo el titular, que
si esta a salvo en la tabla `headings`. Esta reparacion es por tanto parcial y a
proposito — para el hero entero hay que re-rastrear con el extractor corregido.

Criterio de "falta": el h1 no aparece como LINEA propia del contenido (comparado
sin mayusculas y con espacios colapsados). Como subcadena daria falsos
positivos: en un texto largo la marca o el nombre del producto reaparecen a
media frase y el titular se daria por presente sin estarlo.

Es idempotente: pasarlo dos veces no duplica nada.

Uso (dentro del contenedor del crawler):

    python scripts/fix_h1_en_contenido.py <job_id> [<job_id> ...] [--dry-run]
    python scripts/fix_h1_en_contenido.py --todos --dry-run
    python scripts/fix_h1_en_contenido.py --revertir fix_h1_<job>_<fecha>.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime

_WHITESPACE = re.compile(r"\s+")


def _preparar_rutas() -> None:
    """Permite ejecutarlo tanto desde /app como desde la raiz del repo."""
    aqui = os.path.dirname(os.path.abspath(__file__))
    raiz = os.path.dirname(aqui)
    if raiz not in sys.path:
        sys.path.insert(0, raiz)


_preparar_rutas()

from sqlalchemy import select, text as sql_text  # noqa: E402

from shared.database import SessionLocal  # noqa: E402
from shared.models import Heading, PageContent, Url  # noqa: E402


def _norm(linea: str) -> str:
    return _WHITESPACE.sub(" ", linea).strip().lower()


def _falta_titular(contenido: str | None, titular: str) -> bool:
    """True cuando *titular* no figura como linea propia de *contenido*."""
    if not contenido:
        return True
    objetivo = _norm(titular)
    return not any(_norm(ln) == objetivo for ln in contenido.splitlines())


def _anteponer(contenido: str | None, titular: str) -> str:
    return f"{titular}\n{contenido}" if contenido else titular


def _h1_por_url(sesion, job_id: str) -> dict[int, str]:
    """Primer h1 no vacio de cada URL 200/HTML del job."""
    filas = sesion.execute(
        select(Heading.url_id, Heading.text, Heading.position)
        .join(Url, Url.id == Heading.url_id)
        .where(
            Url.job_id == job_id,
            Url.status_code == 200,
            Url.is_html.is_(True),
            Heading.tag == "h1",
        )
        .order_by(Heading.url_id, Heading.position)
    ).all()
    primeros: dict[int, str] = {}
    for url_id, texto, _pos in filas:
        if url_id in primeros:
            continue
        limpio = _WHITESPACE.sub(" ", texto or "").strip()
        if len(limpio) > 3:
            primeros[url_id] = limpio
    return primeros


def _quitar_primera_linea(contenido: str | None, esperada: str) -> str | None:
    """Quita la primera linea SOLO si es exactamente *esperada*."""
    if not contenido:
        return contenido
    trozos = contenido.split("\n", 1)
    if _norm(trozos[0]) != _norm(esperada):
        return contenido
    return trozos[1].lstrip("\n") if len(trozos) > 1 else None


def revertir_diario(sesion, diario: dict, *, dry_run: bool) -> tuple[int, int]:
    """Deshace SOLO las paginas anotadas en el diario de una pasada anterior.

    No se puede revertir por heuristica: quitar la primera linea cuando
    coincide con el h1 borraria el titular de las paginas que YA lo tenian
    bien puesto — que son la mayoria. Solo se toca lo que consta escrito.
    """
    titulares = {int(k): v for k, v in diario["paginas"].items()}
    revertidas = 0
    ids = list(titulares)
    for i in range(0, len(ids), 500):
        contenidos = sesion.execute(
            select(PageContent).where(PageContent.url_id.in_(ids[i : i + 500]))
        ).scalars().all()
        for pc in contenidos:
            titular = titulares[pc.url_id]
            texto = _quitar_primera_linea(pc.content_text, titular)
            if texto == pc.content_text:
                continue  # esta pagina no la habiamos tocado
            pc.content_text = texto
            pc.content_length = len(texto) if texto else None
            pc.content_markdown = _quitar_primera_linea(
                pc.content_markdown, f"# {titular}"
            )
            pc.content_text_original = _quitar_primera_linea(
                pc.content_text_original, titular
            )
            pc.content_markdown_original = _quitar_primera_linea(
                pc.content_markdown_original, f"# {titular}"
            )
            revertidas += 1
        if dry_run:
            sesion.expunge_all()
        else:
            sesion.commit()

    return len(titulares), revertidas


def reparar_job(sesion, job_id: str, *, dry_run: bool) -> tuple[int, dict[int, str]]:
    """Devuelve (paginas con h1 examinadas, {url_id: titular} reparadas)."""
    titulares = _h1_por_url(sesion, job_id)
    if not titulares:
        return 0, 0

    tocadas: dict[int, str] = {}
    ids = list(titulares)
    LOTE = 500
    for i in range(0, len(ids), LOTE):
        trozo = ids[i : i + LOTE]
        contenidos = sesion.execute(
            select(PageContent).where(PageContent.url_id.in_(trozo))
        ).scalars().all()
        for pc in contenidos:
            titular = titulares[pc.url_id]
            # La decision se toma SIEMPRE sobre content_text, la columna
            # canonica. En markdown el titular puede venir como encabezado
            # (`# X`), subrayado (`X` + `===`) o dentro del `![alt]()` de la
            # imagen del hero, asi que comparar lineas alli marcaria como
            # rotas paginas que estan perfectamente bien.
            if not _falta_titular(pc.content_text, titular):
                continue
            pc.content_text = _anteponer(pc.content_text, titular)
            pc.content_length = len(pc.content_text)
            if pc.content_markdown:
                pc.content_markdown = f"# {titular}\n\n{pc.content_markdown}"
            else:
                pc.content_markdown = f"# {titular}"
            # Si la pagina paso por una limpieza post-crawl, parchear tambien
            # la copia original: si no, revertir la limpieza reintroduce el
            # hueco que acabamos de tapar.
            if pc.content_text_original is not None and _falta_titular(
                pc.content_text_original, titular
            ):
                pc.content_text_original = _anteponer(pc.content_text_original, titular)
            if pc.content_markdown_original:
                pc.content_markdown_original = (
                    f"# {titular}\n\n{pc.content_markdown_original}"
                )
            tocadas[pc.url_id] = titular
        if dry_run:
            sesion.expunge_all()
        else:
            sesion.commit()

    return len(titulares), tocadas


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("jobs", nargs="*", help="ids de job a reparar")
    parser.add_argument(
        "--todos", action="store_true", help="reparar todos los jobs con contenido"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="solo contar, sin escribir nada"
    )
    parser.add_argument(
        "--revertir",
        metavar="DIARIO",
        help="deshacer la pasada anotada en ese fichero de diario (.json)",
    )
    parser.add_argument(
        "--diario",
        metavar="RUTA",
        help="donde escribir el diario de lo reparado "
        "(por defecto ./fix_h1_<job>_<fecha>.json)",
    )
    args = parser.parse_args()

    if not args.jobs and not args.todos and not args.revertir:
        parser.error("indica al menos un job_id, --todos o --revertir <diario>")

    sesion = SessionLocal()
    try:
        if args.revertir:
            with open(args.revertir, encoding="utf-8") as fh:
                diario = json.load(fh)
            examinadas, revertidas = revertir_diario(
                sesion, diario, dry_run=args.dry_run
            )
            verbo = "se revertirian" if args.dry_run else "revertidas"
            print(f"{verbo} {revertidas} de las {examinadas} paginas del diario")
            return 0

        if args.todos:
            jobs = [
                str(j)
                for (j,) in sesion.execute(
                    sql_text(
                        "SELECT DISTINCT u.job_id FROM urls u "
                        "JOIN page_content pc ON pc.url_id = u.id"
                    )
                ).all()
            ]
        else:
            jobs = args.jobs

        total_examinadas = total_reparadas = 0
        for job_id in jobs:
            nombre = sesion.execute(
                sql_text("SELECT name FROM jobs WHERE id = :j"), {"j": job_id}
            ).scalar()
            if nombre is None:
                print(f"  ??  {job_id}  (no existe)")
                continue
            examinadas, tocadas = reparar_job(sesion, job_id, dry_run=args.dry_run)
            total_examinadas += examinadas
            total_reparadas += len(tocadas)
            marca = "-" if not tocadas else "*"
            print(
                f"  {marca}  {job_id[:8]}  {len(tocadas):>5} / {examinadas:<5} "
                f"{(nombre or '')[:55]}"
            )
            if tocadas and not args.dry_run:
                ruta = args.diario or (
                    f"fix_h1_{job_id[:8]}_"
                    f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
                )
                with open(ruta, "w", encoding="utf-8") as fh:
                    json.dump(
                        {"job_id": job_id, "paginas": tocadas}, fh, ensure_ascii=False
                    )
                print(f"      diario para deshacerlo: {ruta}")

        verbo = "se repararian" if args.dry_run else "reparadas"
        print(
            f"\n{verbo} {total_reparadas} paginas de {total_examinadas} con h1"
            + (" (dry-run: no se ha escrito nada)" if args.dry_run else "")
        )
        print(
            "Solo se repone el titular; el resto del hero (categoria, subtitulo)\n"
            "no esta en la base de datos y necesita re-rastreo."
        )
    finally:
        sesion.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
