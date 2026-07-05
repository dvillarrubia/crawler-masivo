"""Propuesta de esquema de entidades con un LLM, a partir del cliente.

La sección de Entidades de Config exige rellenar a mano qué tipos de
entidad extraer (`resolubles`/`senal`) y los tipos de página. Esto lo
automatiza: reúne señal REAL del cliente (host, títulos + H1 de sus
páginas, patrones de path, top queries de GSC) y le pide a Gemini Flash que
proponga el esquema. El usuario luego revisa/edita y guarda con el PUT de
siempre (esto NO escribe nada: solo propone).

Partes puras (contexto y parseo) testeables sin Gemini; la llamada al
modelo es inyectable (`generate_fn`).
"""
from __future__ import annotations

import json
import re

_SLUG_RE = re.compile(r"[^a-z0-9_]+")


def _slug(text: str) -> str:
    s = (text or "").strip().lower().replace(" ", "_")
    s = _SLUG_RE.sub("_", s).strip("_")
    return s[:64] or "entidad"


def gather_client_context(session, client_id: str, *,
                          max_pages: int = 40, max_queries: int = 40) -> dict:
    """Reúne la señal del cliente para el prompt (solo lectura).

    host + muestra de (path, title, h1) de las páginas más importantes del
    último rastreo + primeros segmentos de path (pistas de tipo_pagina) +
    top queries de GSC por impresiones. Si no hay rastreo devuelve lo que
    haya (puede quedar casi vacío: el LLM tira del business_hint)."""
    from collections import Counter

    from shared.models import Heading, HtmlMeta, Job, Url

    job = (session.query(Job)
           .filter(Job.client_id == client_id, Job.status == "completed")
           .order_by(Job.completed_at.desc().nullslast()).first())
    ctx: dict = {"host": None, "n_pages": 0, "pages": [],
                 "path_segments": [], "queries": [], "n_queries": 0,
                 "job_id": str(job.id) if job else None}
    if job is None:
        return ctx

    rows = (session.query(Url.id, Url.url, Url.path, HtmlMeta.title)
            .outerjoin(HtmlMeta, HtmlMeta.url_id == Url.id)
            .filter(Url.job_id == job.id, Url.is_internal.is_(True),
                    Url.is_html.is_(True))
            .order_by(Url.pagerank.desc().nullslast())
            .limit(max_pages).all())
    if rows:
        ctx["host"] = _host_of(rows[0].url)
        ids = [r.id for r in rows]
        h1_by_url = {}
        for uid, txt in (session.query(Heading.url_id, Heading.text)
                         .filter(Heading.url_id.in_(ids), Heading.tag == "h1").all()):
            h1_by_url.setdefault(uid, txt)
        seg_counter: Counter = Counter()
        for r in rows:
            seg = _first_segment(r.path)
            if seg:
                seg_counter[seg] += 1
            ctx["pages"].append({
                "path": r.path or "/",
                "title": (r.title or "")[:160],
                "h1": (h1_by_url.get(r.id) or "")[:160],
            })
        ctx["n_pages"] = len(rows)
        ctx["path_segments"] = [s for s, _ in seg_counter.most_common(20)]

    # Top queries GSC del cliente (todos sus jobs, por impresiones)
    try:
        from sqlalchemy import func

        from shared.semantic_models import GscQueryData

        job_ids = [j.id for j in session.query(Job.id).filter(
            Job.client_id == client_id).all()]
        if job_ids:
            q = (session.query(GscQueryData.query,
                               func.sum(GscQueryData.impressions).label("imp"))
                 .filter(GscQueryData.job_id.in_(job_ids))
                 .group_by(GscQueryData.query)
                 .order_by(func.sum(GscQueryData.impressions).desc())
                 .limit(max_queries).all())
            ctx["queries"] = [row.query for row in q]
            ctx["n_queries"] = len(ctx["queries"])
    except Exception:
        pass  # GSC es opcional; sin ella el LLM tira de títulos + hint

    return ctx


def _host_of(url: str) -> str | None:
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc or None
    except Exception:
        return None


def _first_segment(path: str | None) -> str | None:
    if not path:
        return None
    parts = [p for p in path.strip("/").split("/") if p]
    return parts[0] if parts else None


PROMPT_HEADER = """\
Eres un consultor SEO experto en modelado de entidades para un motor de
extracción (GLiNER2). Tu tarea: proponer el ESQUEMA DE ENTIDADES de un
cliente para poder analizar su sitio.

Distingue dos grupos:
- "resolubles": tipos de entidad concretos y enumerables que forman el
  catálogo del negocio (p. ej. servicio, producto, curso, categoría,
  localización, marca). Son cosas que el cliente "tiene" y se pueden listar.
- "senal": tipos más difusos que dan contexto o intención, no catálogo
  (p. ej. tecnologia, sector_cliente, beneficio, problema, publico_objetivo).

Reglas:
- nombre en snake_case, minúsculas, sin espacios ni tildes (a-z, 0-9, _).
- descripcion: UNA frase clara en español (mínimo 10 palabras) que DEFINA el
  tipo para el extractor, con ejemplos concretos de este cliente.
- entre 3 y 8 resolubles y entre 2 y 6 senal; no te inventes tipos que el
  negocio no tenga.
- tipo_pagina: lista de tipos de página observados (home, servicio,
  categoria, producto, blog, contacto, caso_exito…), en snake_case.

Devuelve SOLO un JSON con esta forma exacta:
{
  "resolubles": [{"nombre": "...", "descripcion": "..."}],
  "senal": [{"nombre": "...", "descripcion": "..."}],
  "tipo_pagina": ["...", "..."],
  "razonamiento": "una o dos frases sobre qué tipo de negocio es y por qué"
}
"""


def build_prompt(context: dict, business_hint: str | None = None) -> str:
    """Construye el prompt con la señal del cliente. Puro (sin red)."""
    lines = [PROMPT_HEADER, "\n--- DATOS DEL CLIENTE ---"]
    if context.get("host"):
        lines.append(f"Dominio: {context['host']}")
    if business_hint:
        lines.append(f"Descripción del negocio (dada por el usuario): {business_hint}")
    if context.get("path_segments"):
        lines.append("Secciones del sitio (primer nivel de URL): "
                     + ", ".join(context["path_segments"]))
    pages = context.get("pages") or []
    if pages:
        lines.append(f"\nMuestra de {len(pages)} páginas (path — título — H1):")
        for p in pages[:40]:
            bits = [p.get("path", "")]
            if p.get("title"):
                bits.append(p["title"])
            if p.get("h1"):
                bits.append(f"H1: {p['h1']}")
            lines.append("  · " + " — ".join(bits))
    queries = context.get("queries") or []
    if queries:
        lines.append(f"\nTop {len(queries)} búsquedas por las que aparece en Google:")
        lines.append("  " + "; ".join(queries[:40]))
    if not pages and not queries and not business_hint:
        lines.append("(Sin rastreo ni GSC ni descripción: propón un esquema "
                     "genérico razonable y dilo en el razonamiento.)")
    lines.append("\n--- FIN DATOS ---\nDevuelve solo el JSON.")
    return "\n".join(lines)


def parse_llm_schema(text: str) -> dict:
    """Convierte la respuesta del LLM en la forma del formulario, de forma
    defensiva (quita ``` fences, coacciona nombres a slug, descarta lo
    incompleto). No valida a fondo: eso lo hace el PUT al guardar."""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw).strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # último intento: extraer el primer objeto {...}
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            raise ValueError("La respuesta del modelo no era JSON válido.")
        data = json.loads(m.group(0))
    if not isinstance(data, dict):
        raise ValueError("La respuesta del modelo no era un objeto JSON.")

    def _clean_types(items) -> list[dict]:
        out, seen = [], set()
        for it in (items or []):
            if not isinstance(it, dict):
                continue
            nombre = _slug(str(it.get("nombre", "")))
            desc = str(it.get("descripcion", "")).strip()
            if not nombre or nombre in seen or len(desc) < 10:
                continue
            seen.add(nombre)
            out.append({"nombre": nombre, "descripcion": desc[:500]})
        return out

    resolubles = _clean_types(data.get("resolubles"))
    senal = _clean_types(data.get("senal"))
    # un tipo no puede estar en los dos grupos: gana resoluble
    res_names = {e["nombre"] for e in resolubles}
    senal = [e for e in senal if e["nombre"] not in res_names]

    tipos = []
    seen_tp = set()
    for t in (data.get("tipo_pagina") or []):
        s = _slug(str(t))
        if s and s not in seen_tp:
            seen_tp.add(s)
            tipos.append(s)

    return {
        "resolubles": resolubles,
        "senal": senal,
        "tipo_pagina": tipos,
        "razonamiento": str(data.get("razonamiento", ""))[:600],
    }


def suggest_schema(session, client_id: str, api_key: str, *,
                   business_hint: str | None = None,
                   generate_fn=None, attempts: int = 3) -> dict:
    """Orquesta: contexto → prompt → LLM → esquema propuesto.

    `generate_fn(prompt) -> str` es inyectable (tests). Por defecto usa
    Gemini Flash con salida JSON. El modelo es no determinista y de vez en
    cuando devuelve JSON malo o sin resolubles: se REINTENTA hasta
    `attempts` veces antes de rendirse (evita el 422 intermitente).
    Devuelve el esquema + un bloque `context` con en qué se basó (para que
    la UI sea honesta)."""
    context = gather_client_context(session, client_id)
    prompt = build_prompt(context, business_hint)

    if generate_fn is None:
        generate_fn = _gemini_generate(api_key)

    proposal = None
    last_err = "sin resolubles"
    for _ in range(max(1, attempts)):
        try:
            candidate = parse_llm_schema(generate_fn(prompt))
        except ValueError as exc:
            last_err = str(exc)
            continue
        if candidate["resolubles"]:
            proposal = candidate
            break
        last_err = "el modelo no propuso ningún tipo resoluble"

    if proposal is None:
        raise ValueError(
            f"No se pudo generar un esquema válido tras {attempts} intentos "
            f"({last_err}). Prueba a añadir una descripción del negocio, o "
            "revisa que el rastreo tenga contenido.")

    proposal["context"] = {
        "host": context.get("host"),
        "n_pages": context.get("n_pages", 0),
        "n_queries": context.get("n_queries", 0),
        "used_business_hint": bool(business_hint),
    }
    return proposal


def _gemini_generate(api_key: str):
    """Cierre que llama a Gemini Flash pidiendo JSON. Import perezoso."""
    def _run(prompt: str) -> str:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model="gemini-flash-latest", contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.4),
        )
        return resp.text or ""
    return _run
