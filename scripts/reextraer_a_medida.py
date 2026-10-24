"""Re-extrae las reglas a medida de un censo YA rastreado, sobre su `raw_html`
(#34, decision 166).

Sin volver a rastrear: lee `page_content.raw_html` (solo existe si el censo se
lanzo con `extraction.store_raw_html`) y aplica las reglas con la MISMA
`shared.extraccion_a_medida.aplicar` que el spider. Sirve para anadir o
corregir una regla sobre un censo entregado, o para repetir la extraccion tras
un arreglo del codigo.

Lo que hace y lo que no afirma:

- Toma el **candado del job** (el del analisis, `analysis.analyzer.candado_de_job`)
  y se niega con el job en marcha (`pending`, `running`, `analyzing`...): el
  spider escribe en `custom_extraction` mientras rastrea y no toma candado.
- Todo va en **una transaccion**: las filas de todos los lotes y las reglas de
  `jobs.config` se confirman juntas al final. Si el proceso muere a medias no
  queda nada cambiado (ni filas con la regla nueva bajo la config vieja, que la
  comparacion entre censos tomaria por cambios del sitio).
- Las reglas nuevas (`--reglas fichero.json`, con `custom_extraction` y/o
  `custom_search`) se FUSIONAN con las del censo como en la herencia (decision
  104): por nombre se sustituye la regla entera y `null` la quita. Se guardan
  en `jobs.config`, para que el censo diga con que reglas se extrajo y la
  comparacion entre censos (127) vea la definicion de verdad.
- Una pagina HTML 2xx interna CON `raw_html`: se reemplazan TODAS sus filas.
  Un muro (`urls.response_class`, decision 103): `pagina_bloqueada`, como en el
  rastreo. Una pagina SIN `raw_html` no se puede evaluar: se borran sus filas
  de las reglas que cambian o se quitan (ya no dicen nada de la regla nueva) y
  se conservan las de las reglas que no cambian. Sin fila = no evaluada (104):
  el resumen dice cuantas se quedan asi.
- `fuente=contenido` usa `page_content.content_text`, como el rastreo.
- Con render, `raw_html` es el DOM renderizado y sin banners: lo mismo que vio
  el spider. La fila conserva el `render` que tenia la pagina.

No llama a la API (va directo a la base de datos, como los demas scripts que
corren dentro de la imagen del crawler), asi que no necesita `API_TOKEN`.

Uso (dentro del contenedor del crawler):

    python scripts/reextraer_a_medida.py <job_id>                    # mismas reglas
    python scripts/reextraer_a_medida.py <job_id> --reglas r.json    # reglas nuevas
    python scripts/reextraer_a_medida.py <job_id> --reglas r.json --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Any

_RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _RAIZ not in sys.path:
    sys.path.insert(0, _RAIZ)

# Estados de un job en los que algo puede estar escribiendo sus filas.
EN_MARCHA = ("pending", "queued", "running", "analyzing", "paused", "deleting")
LOTE = 200


class NoSePuede(RuntimeError):
    """El censo no se puede re-extraer (en marcha, candado, reglas)."""


def fusionar_reglas(config: dict, nuevas: dict | None) -> tuple[dict, dict]:
    """(custom_extraction, custom_search) del censo con *nuevas* encima:
    por nombre sustituye la regla entera; `null` la quita."""
    ce = dict((config or {}).get("custom_extraction") or {})
    cs = dict((config or {}).get("custom_search") or {})
    nuevas = nuevas or {}
    desconocidas = set(nuevas) - {"custom_extraction", "custom_search"}
    if desconocidas:
        raise NoSePuede(f"claves desconocidas en las reglas: {', '.join(sorted(desconocidas))}")
    for destino, clave in ((ce, "custom_extraction"), (cs, "custom_search")):
        for nombre, regla in (nuevas.get(clave) or {}).items():
            if regla is None:
                destino.pop(nombre, None)
            else:
                destino[nombre] = regla
    return ce, cs


def _firmas(ce: dict, cs: dict) -> dict[str, str]:
    from shared.extraccion_a_medida import firma

    salida = {n: firma(r, "extractor") for n, r in ce.items() if isinstance(r, dict)}
    salida.update({n: firma(r, "busqueda") for n, r in cs.items() if isinstance(r, dict)})
    return salida


def reextraer(sesion, job_id: str, nuevas: dict | None = None, *, dry_run: bool = False,
              candado=None) -> dict[str, Any]:
    """Hace el trabajo y devuelve el resumen. `candado`: context manager que
    cede si se tomo (por defecto, el del analisis)."""
    import uuid

    from shared import extraccion_a_medida as xm
    from shared.models import CustomExtraction, Job

    try:
        job_id = uuid.UUID(str(job_id))
    except ValueError:
        raise NoSePuede(f"{job_id!r} no es un id de job") from None
    job = sesion.get(Job, job_id)
    if job is None:
        raise NoSePuede(f"el job {job_id} no existe")
    if job.status in EN_MARCHA:
        raise NoSePuede(f"el job esta {job.status}: re-extraer mientras rastrea o analiza "
                        f"pisaria sus filas")
    config = dict(job.config or {})
    ce, cs = fusionar_reglas(config, nuevas)
    extract_page_content = ((config.get("extraction") or {}).get("extract_page_content", True)
                            is not False)
    errores = xm.validar(ce, cs, extract_page_content=extract_page_content,
                         max_urls=config.get("max_urls"))
    if errores:
        raise NoSePuede("reglas no validas: " + "; ".join(errores))
    comp = xm.compilar(ce, cs)
    firmas_antes = _firmas(config.get("custom_extraction") or {},
                           config.get("custom_search") or {})
    firmas_ahora = _firmas(ce, cs)
    # Reglas cuyas filas viejas ya no dicen nada: quitadas o con otra definicion.
    con_filas = {r[0] for r in sesion.query(CustomExtraction.nombre)
                 .filter(CustomExtraction.job_id == job.id).distinct()}
    caducas = sorted(n for n in firmas_antes.keys() | con_filas
                     if firmas_ahora.get(n) != firmas_antes.get(n))
    render_job = bool(config.get("render_js"))

    if candado is None:
        from analysis.analyzer import candado_de_job as candado
    resumen: dict[str, Any] = {"job_id": str(job.id), "reglas": sorted(firmas_ahora),
                               "caducas": caducas, "evaluadas": 0, "bloqueadas": 0,
                               "sin_html": 0, "filas": 0, "estados": Counter(),
                               "dry_run": dry_run}
    with candado(str(job.id)) as tomado:
        if not tomado:
            raise NoSePuede("otro proceso tiene el candado del job (un analisis, un "
                            "archivado o una re-extraccion)")
        try:
            _reextraer_en_una_transaccion(sesion, job, config, ce, cs, comp, caducas,
                                          render_job, resumen, dry_run)
        except BaseException:
            # Nada a medias: ni filas con la regla nueva y la config vieja, ni
            # al reves (revision de #233).
            sesion.rollback()
            raise
    resumen["estados"] = dict(resumen["estados"])
    return resumen


def _reextraer_en_una_transaccion(sesion, job, config, ce, cs, comp, caducas, render_job,
                                  resumen, dry_run) -> None:
    """Todas las filas y las reglas de `jobs.config` en UNA transaccion, con un
    solo commit al final. Con un commit por lote, un proceso que muriera a
    medias (OOM, kill, despliegue) dejaba filas calculadas con la regla NUEVA
    y `jobs.config` diciendo la VIEJA, y la comparacion entre censos (127) y
    la columna «cambios» daban por cambio del sitio lo que era de la regla.
    Mientras corre, quien lea ve el censo como estaba (MVCC)."""
    from shared import extraccion_a_medida as xm
    from shared.models import CustomExtraction, PageContent, Url
    from shared.respuestas import NO_VALIDAS

    # Por `urls.job_id` (decision 37); los ids, sin el HTML: el HTML se lee
    # por lotes.
    candidatas = (
        sesion.query(Url.id, Url.path, Url.response_class, Url.status_code, Url.is_html)
        .filter(Url.job_id == job.id, Url.is_internal.is_(True))
        .order_by(Url.id).all()
    )
    render_previo = {
        uid: r for uid, r in sesion.query(CustomExtraction.url_id, CustomExtraction.render)
        .filter(CustomExtraction.job_id == job.id).distinct()
    }
    presupuesto = xm.Presupuesto()
    for i in range(0, len(candidatas), LOTE):
        lote = candidatas[i:i + LOTE]
        ids = [u.id for u in lote]
        contenidos = {
            pc.url_id: pc for pc in sesion.query(PageContent)
            .filter(PageContent.url_id.in_(ids))
        }
        reemplazar: dict[int, list[dict]] = {}
        sin_html: list[int] = []
        for u in lote:
            render = render_previo.get(u.id, render_job)
            ruta = u.path or "/"
            if u.response_class in NO_VALIDAS:
                reemplazar[u.id] = xm.aplicar(None, "", comp, ruta=ruta, render=render,
                                              presupuesto=presupuesto, bloqueada=True)
                resumen["bloqueadas"] += 1
                continue
            if not (u.is_html and u.status_code and 200 <= u.status_code < 300):
                continue
            pc = contenidos.get(u.id)
            if pc is None or not pc.raw_html:
                sin_html.append(u.id)
                continue
            raiz, texto = xm.preparar(pc.raw_html)
            reemplazar[u.id] = xm.aplicar(
                raiz, pc.raw_html, comp, texto=texto, contenido=pc.content_text,
                ruta=ruta, titulo=xm.titulo_de(raiz), render=render,
                presupuesto=presupuesto)
            resumen["evaluadas"] += 1
        resumen["sin_html"] += len(sin_html)
        filas = [
            {"job_id": job.id, "url_id": uid, "nombre": f["nombre"], "clase": f["clase"],
             "estado": f["estado"], "n": f.get("n"), "valor": f.get("valor"),
             "valor_clave": f.get("valor_clave"), "valor_num": f.get("valor_num"),
             "valores": f.get("valores"), "truncado": bool(f.get("truncado")),
             "render": f.get("render"), "detalle": (f.get("detalle") or None)}
            for uid, fs in reemplazar.items() for f in fs
        ]
        for f in filas:
            resumen["estados"][f["estado"]] += 1
        resumen["filas"] += len(filas)
        if dry_run:
            continue
        CE = CustomExtraction
        if reemplazar:
            sesion.query(CE).filter(CE.job_id == job.id, CE.url_id.in_(list(reemplazar))
                                    ).delete(synchronize_session=False)
        if sin_html and caducas:
            sesion.query(CE).filter(CE.job_id == job.id, CE.url_id.in_(sin_html),
                                    CE.nombre.in_(caducas)).delete(synchronize_session=False)
        if filas:
            sesion.bulk_insert_mappings(CE, filas)
    resumen["desactivadas"] = sorted(presupuesto.desactivadas)
    if not dry_run:
        config["custom_extraction"] = ce
        config["custom_search"] = cs
        job.config = config
        sesion.commit()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("job_id")
    ap.add_argument("--reglas", help="JSON con custom_extraction y/o custom_search "
                                     "(se fusiona con las del censo; null quita una regla)")
    ap.add_argument("--dry-run", action="store_true", help="cuenta sin escribir")
    args = ap.parse_args(argv)
    nuevas = None
    if args.reglas:
        with open(args.reglas, encoding="utf-8") as fh:
            nuevas = json.load(fh)
    from shared.database import SessionLocal

    sesion = SessionLocal()
    try:
        r = reextraer(sesion, args.job_id, nuevas, dry_run=args.dry_run)
    except NoSePuede as exc:
        print(f"No se re-extrae: {exc}", file=sys.stderr)
        return 2
    finally:
        sesion.close()
    print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    if r["sin_html"]:
        print(f"Aviso: {r['sin_html']} paginas HTML 2xx no tienen raw_html guardado y se quedan "
              f"SIN fila de las reglas que cambian (no evaluadas, que no es «vacio»).",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
