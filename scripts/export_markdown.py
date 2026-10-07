"""One-shot export of page_content.content_markdown to .md files for a job.

Run inside the api container:
    docker exec -i crawler-masivo-api-1 python /app/scripts/export_markdown.py \
        --job-id <uuid> --out-dir /tmp/export_md
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from urllib.parse import urlparse, unquote

from shared.database import SessionLocal
from shared.models import HtmlMeta, PageContent, Url


def slugify(text: str, max_len: int = 180) -> str:
    text = unquote(text)
    text = re.sub(r"[^\w\-./]", "_", text)
    text = re.sub(r"[/\\]+", "__", text)
    text = re.sub(r"_+", "_", text).strip("_.")
    return text[:max_len] or "index"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument(
        "--por-host",
        action="store_true",
        help="una subcarpeta por host (un job puede rastrear varios)",
    )
    parser.add_argument(
        "--sin-cabecera",
        action="store_true",
        help="no anteponer el front-matter con la URL y el titulo",
    )
    parser.add_argument(
        "--todo",
        action="store_true",
        help="incluir tambien 404, noindex y canonicalizadas (por defecto no)",
    )
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    db = SessionLocal()
    written = 0
    try:
        rows = (
            db.query(Url.url, PageContent.content_markdown, HtmlMeta.title)
            .join(PageContent, PageContent.url_id == Url.id)
            .outerjoin(HtmlMeta, HtmlMeta.url_id == Url.id)
            .filter(Url.job_id == args.job_id)
            .filter(PageContent.content_markdown.isnot(None))
            .filter(PageContent.content_markdown != "")
            # Lo que se exporta es el contenido que el sitio PUBLICA: una 404 con
            # cuerpo, una pagina noindex o una canonicalizada no lo son, y
            # colarlas en el export las mete en el analisis semantico y en
            # cualquier base de conocimiento que se monte encima.
            # `--todo` las incluye para cuando lo que se quiere es auditar.
            .filter(*([] if args.todo else [
                Url.status_code == 200,
                Url.is_html.is_(True),
                Url.indexable.isnot(False),
            ]))
            .all()
        )

        # Pre-compute which paths have children, so they need ``index.md``
        # instead of clashing with a folder of the same name.
        # O(n), no O(n^2): antes se comparaba cada ruta con todas las demas,
        # que con 50.000 URLs son 2.500 millones de comparaciones. Basta
        # anotar la ruta del padre de cada una.
        path_set = {urlparse(u).path.rstrip("/") for u, _, _ in rows}
        con_hijos: set[str] = set()
        for ruta in path_set:
            padre = ruta.rsplit("/", 1)[0] if "/" in ruta else ""
            while padre:
                if padre in con_hijos:
                    break
                con_hijos.add(padre)
                padre = padre.rsplit("/", 1)[0] if "/" in padre else ""

        def has_children(p: str) -> bool:
            return p in con_hijos

        used_paths: set[str] = set()
        for url, md, title in rows:
            partes = urlparse(url)
            raw_path = partes.path.rstrip("/")
            segments = [slugify(s) for s in raw_path.split("/") if s]
            if args.por_host:
                segments = [slugify(partes.netloc)] + segments

            if not segments:
                rel = "index.md"
            elif has_children(raw_path):
                rel = os.path.join(*segments, "index.md")
            else:
                *folders, last = segments
                rel = os.path.join(*folders, f"{last}.md") if folders else f"{last}.md"

            # Tie-break duplicates (different URLs that slugify to the same path)
            base, ext = os.path.splitext(rel)
            unique = rel
            counter = 1
            while unique in used_paths:
                counter += 1
                unique = f"{base}__{counter}{ext}"
            used_paths.add(unique)

            fpath = os.path.join(args.out_dir, unique)
            os.makedirs(os.path.dirname(fpath) or args.out_dir, exist_ok=True)
            if not args.sin_cabecera:
                cabecera = ["---", f"url: {url}"]
                if title:
                    limpio = title.replace('"', "'").strip()
                    cabecera.append(f'title: "{limpio}"')
                cabecera += ["---", "", ""]
                md = "\n".join(cabecera) + md
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(md)
            written += 1
    finally:
        db.close()

    print(f"Wrote {written} markdown files to {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
