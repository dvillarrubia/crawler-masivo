"""Generate a self-contained HTML report of a semantic analysis.

Usage:
    python scripts/export_semantic_html.py <job_id> [output.html] [--label "Site"]

The result is a single HTML file with Plotly loaded from CDN and all data
inlined. Hand it to a client and it works offline once opened. The script
talks to the local API (default http://localhost:8000) so the API must be
running and the job's semantic analysis must be in "completed" state.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import sys
import urllib.request
from typing import Any

DEFAULT_API = os.environ.get("API_URL", "http://localhost:8000")
PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.35.0.min.js"

RING_COLORS = {
    "Core": "#34a853",
    "Focus": "#4285f4",
    "Expansion": "#fbbc04",
    "Peripheral": "#ea4335",
}
RING_DESCRIPTIONS = {
    "Core": "Páginas más alineadas con el tema central del sitio. Son el ancla semántica del negocio.",
    "Focus": "Páginas de soporte temático cercanas al núcleo. Refuerzan la autoridad temática.",
    "Expansion": "Páginas de expansión legítima del topic. Amplían el campo sin desviarlo.",
    "Peripheral": "Páginas que distorsionan el foco semántico del sitio. Candidatas a revisión.",
}


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def _get(path: str, api: str) -> Any:
    url = f"{api.rstrip('/')}{path}"
    with urllib.request.urlopen(url, timeout=60) as r:  # noqa: S310
        return json.loads(r.read().decode("utf-8"))


def fetch_all(job_id: str, api: str) -> dict[str, Any]:
    job = _get(f"/api/jobs/{job_id}", api)
    results = _get(f"/api/jobs/{job_id}/semantic/results", api)
    ring_data = _get(f"/api/jobs/{job_id}/semantic/ring-data", api)
    # Optional endpoints — tolerate failures.
    try:
        cannibal = _get(f"/api/jobs/{job_id}/semantic/cannibalization", api)
    except Exception:
        cannibal = {"pairs": []}
    try:
        drift = _get(f"/api/jobs/{job_id}/semantic/drift", api)
    except Exception:
        drift = {"drift": []}
    return {
        "job": job,
        "results": results,
        "ring_data": ring_data,
        "cannibal": cannibal,
        "drift": drift,
    }


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------
def fmt_int(n: Any) -> str:
    try:
        return f"{int(n):,}".replace(",", ".")
    except (TypeError, ValueError):
        return "—"


def fmt_pct(p: float) -> str:
    return f"{p*100:.1f}%"


def cluster_table(pages: list[dict]) -> str:
    """Build a small table listing detected clusters with sample URLs."""
    by_cluster: dict[int, list[dict]] = {}
    for p in pages:
        cid = p.get("cluster_id")
        if cid is None or cid < 0:
            continue
        by_cluster.setdefault(cid, []).append(p)

    if not by_cluster:
        return "<p class='dim'>HDBSCAN no identificó sub-clusters densos: el sitio es altamente homogéneo en topic.</p>"

    rows = []
    for cid in sorted(by_cluster, key=lambda c: -len(by_cluster[c])):
        pages_c = sorted(by_cluster[cid], key=lambda p: -p.get("weight", 0))[:5]
        sample_urls = "<br>".join(html.escape(p["url"]) for p in pages_c)
        rows.append(
            f"<tr><td>{cid}</td><td>{len(by_cluster[cid])}</td>"
            f"<td class='sample'>{sample_urls}</td></tr>"
        )
    return (
        "<table class='data'>"
        "<thead><tr><th>Cluster</th><th>Páginas</th><th>Ejemplos (top peso)</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def cannibal_table(pairs: list[dict], limit: int = 15) -> str:
    if not pairs:
        return "<p class='dim'>No se detectaron pares de canibalización al umbral configurado.</p>"
    rows = []
    for p in pairs[:limit]:
        sim = p.get("cosine_similarity", 0)
        rows.append(
            "<tr>"
            f"<td>{sim:.3f}</td>"
            f"<td class='sample'>{html.escape(p.get('url_dominant','') or '')}</td>"
            f"<td class='sample'>{html.escape(p.get('url_weak','') or '')}</td>"
            "</tr>"
        )
    extra = ""
    if len(pairs) > limit:
        extra = f"<p class='dim'>Mostrando {limit} de {len(pairs)} pares detectados.</p>"
    return (
        "<table class='data'>"
        "<thead><tr><th>Similitud coseno</th><th>URL dominante</th><th>URL débil</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>{extra}"
    )


def drift_table(items: list[dict], limit: int = 10) -> str:
    if not items:
        return ""
    rows = []
    for d in items[:limit]:
        rows.append(
            "<tr>"
            f"<td class='sample'>{html.escape(d.get('url','') or '')}</td>"
            f"<td>{d.get('distance',0):.3f}</td>"
            f"<td>{d.get('weight',0):.3f}</td>"
            f"<td>{d.get('drift_score',0):.3f}</td>"
            "</tr>"
        )
    return (
        "<table class='data'>"
        "<thead><tr><th>URL</th><th>Distancia</th><th>Peso</th><th>Drift score</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
    )


def ring_legend() -> str:
    parts = []
    for name in ["Core", "Focus", "Expansion", "Peripheral"]:
        parts.append(
            f"<li><span class='dot' style='background:{RING_COLORS[name]}'></span>"
            f"<b>{name}</b> — {RING_DESCRIPTIONS[name]}</li>"
        )
    return "<ul class='legend'>" + "".join(parts) + "</ul>"


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------
TEMPLATE = """<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Mapa semántico — {site_label}</title>
  <script src="{plotly_cdn}" charset="utf-8"></script>
  <style>
    :root {{
      --bg: #ffffff;
      --fg: #1f2933;
      --dim: #6b7785;
      --line: #e5e9ef;
      --card: #ffffff;
      --accent: #1a73e8;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      color: var(--fg);
      background: #f6f8fb;
      line-height: 1.5;
    }}
    .wrap {{ max-width: 1100px; margin: 0 auto; padding: 32px 24px 64px; }}
    header h1 {{ margin: 0 0 4px; font-size: 28px; letter-spacing: -.01em; }}
    header .meta {{ color: var(--dim); font-size: 14px; }}
    .kpis {{
      display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px; margin: 24px 0;
    }}
    .kpi {{
      background: var(--card); border: 1px solid var(--line); border-radius: 8px;
      padding: 16px;
    }}
    .kpi .label {{ font-size: 12px; color: var(--dim); text-transform: uppercase; letter-spacing: .04em; }}
    .kpi .value {{ font-size: 24px; font-weight: 600; margin-top: 4px; }}
    .kpi .sub {{ font-size: 12px; color: var(--dim); margin-top: 4px; }}
    section {{ margin: 32px 0; }}
    section h2 {{ font-size: 18px; margin: 0 0 12px; }}
    .card {{
      background: var(--card); border: 1px solid var(--line); border-radius: 12px;
      padding: 20px;
    }}
    .legend {{ list-style: none; padding: 0; margin: 0; }}
    .legend li {{ padding: 8px 0; border-bottom: 1px solid var(--line); }}
    .legend li:last-child {{ border-bottom: none; }}
    .legend .dot {{
      display: inline-block; width: 10px; height: 10px; border-radius: 50%;
      margin-right: 8px; vertical-align: middle;
    }}
    table.data {{
      width: 100%; border-collapse: collapse; font-size: 13px;
    }}
    table.data th, table.data td {{
      padding: 8px 10px; text-align: left; border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    table.data th {{ font-weight: 600; color: var(--dim); font-size: 11px;
      text-transform: uppercase; letter-spacing: .04em; }}
    table.data td.sample {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px; word-break: break-all; }}
    .dim {{ color: var(--dim); font-size: 13px; }}
    .plot-host {{ height: 640px; }}
    footer {{ margin-top: 48px; color: var(--dim); font-size: 12px; text-align: center; }}
  </style>
</head>
<body>
  <div class="wrap">
    <header>
      <h1>Mapa semántico — {site_label}</h1>
      <div class="meta">{n_pages} páginas analizadas · análisis del {analysis_date} · proveedor de embeddings: {provider}</div>
    </header>

    <div class="kpis">
      <div class="kpi"><div class="label">Focus Score</div><div class="value">{focus_score}</div><div class="sub">1 − mean(d)/p95(d). Más alto = sitio más concentrado.</div></div>
      <div class="kpi"><div class="label">Semantic Radius</div><div class="value">{semantic_radius}</div><div class="sub">p95 de distancias al centroide.</div></div>
      <div class="kpi"><div class="label">Outliers</div><div class="value">{n_outliers}</div><div class="sub">Páginas fuera del rango IQR.</div></div>
      <div class="kpi"><div class="label">Canibalización</div><div class="value">{n_cannibal}</div><div class="sub">Pares con sim ≥ {cannibal_threshold:.2f}.</div></div>
      <div class="kpi"><div class="label">Sub-clusters</div><div class="value">{n_clusters}</div><div class="sub">Detectados por HDBSCAN.</div></div>
    </div>

    <section class="card">
      <h2>Mapa de anillos concéntricos</h2>
      <div id="ringmap" class="plot-host"></div>
      {ring_legend}
    </section>

    <section class="card">
      <h2>Distribución por anillos</h2>
      <table class="data">
        <thead><tr><th>Anillo</th><th>Páginas</th><th>%</th><th>Significado</th></tr></thead>
        <tbody>{ring_rows}</tbody>
      </table>
    </section>

    <section class="card">
      <h2>Clusters temáticos detectados</h2>
      <p class="dim">Sub-temas que el motor identifica a partir del contenido (sin patrones URL pre-configurados).</p>
      {cluster_html}
    </section>

    <section class="card">
      <h2>Páginas que más desvían el centro semántico</h2>
      <p class="dim">Ordenadas por drift_score = peso × distancia al centroide. Candidatas a revisión, consolidación o despublicación.</p>
      {drift_html}
    </section>

    <section class="card">
      <h2>Pares de canibalización (top {cannibal_limit})</h2>
      <p class="dim">Páginas con similitud coseno ≥ {cannibal_threshold:.2f}. La <em>dominante</em> es la de mayor autoridad (PageRank + clicks).</p>
      {cannibal_html}
    </section>

    <footer>
      Generado el {generated_at} · Embeddings: {provider} {model} ({dim}d) · Job {job_id}
    </footer>
  </div>

  <script>
    const ringSpec = {ring_data_json};
    Plotly.newPlot('ringmap', ringSpec.data, ringSpec.layout, {{displayModeBar: false, responsive: true}});
  </script>
</body>
</html>
"""


def render(site_label: str, data: dict[str, Any]) -> str:
    job = data["job"]
    results = data["results"]
    ring_data = data["ring_data"]
    cannibal = data["cannibal"]
    drift = data["drift"]

    sm = results["site_metrics"] or {}
    cfg = results.get("config") or {}
    pages = results.get("pages") or []
    ring_counts = sm.get("ring_counts", {})
    total = sm.get("total_pages") or sum(ring_counts.values()) or 0

    ring_rows = []
    for name in ["Core", "Focus", "Expansion", "Peripheral"]:
        n = ring_counts.get(name, 0)
        pct = (n / total) if total else 0
        ring_rows.append(
            f"<tr><td><span style='display:inline-block;width:10px;height:10px;"
            f"border-radius:50%;background:{RING_COLORS[name]};margin-right:6px'></span>"
            f"{name}</td><td>{fmt_int(n)}</td><td>{fmt_pct(pct)}</td>"
            f"<td>{RING_DESCRIPTIONS[name]}</td></tr>"
        )

    completed_at = sm.get("completed_at") or results.get("completed_at") or ""
    if not completed_at and job.get("name"):
        completed_at = ""

    return TEMPLATE.format(
        site_label=html.escape(site_label),
        plotly_cdn=PLOTLY_CDN,
        n_pages=fmt_int(total),
        analysis_date=html.escape(
            (results.get("completed_at") or "").split("T")[0] or "—"
        ),
        provider=html.escape(cfg.get("embedding_provider") or "—"),
        focus_score=sm.get("focus_score", "—"),
        semantic_radius=sm.get("semantic_radius", "—"),
        n_outliers=fmt_int(sm.get("n_outliers", 0)),
        n_cannibal=fmt_int(sm.get("n_cannibal_pairs", 0)),
        cannibal_threshold=cfg.get("cannibal_threshold", 0.92),
        n_clusters=fmt_int(sm.get("n_clusters", 0)),
        ring_legend=ring_legend(),
        ring_rows="".join(ring_rows),
        cluster_html=cluster_table(pages),
        drift_html=drift_table(drift.get("drift") or []),
        cannibal_html=cannibal_table(cannibal.get("pairs") or []),
        cannibal_limit=15,
        ring_data_json=json.dumps(ring_data),
        generated_at=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        model=html.escape(cfg.get("embedding_model") or ""),
        dim=cfg.get("embedding_dim") or "",
        job_id=html.escape(str(job.get("id") or "")),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Export a semantic analysis as standalone HTML.")
    parser.add_argument("job_id", help="UUID of the crawl job")
    parser.add_argument("output", nargs="?", default=None, help="Output HTML path (default: ./semantic_<short>.html)")
    parser.add_argument("--label", default=None, help="Site label shown in the report header")
    parser.add_argument("--api", default=DEFAULT_API, help=f"API base URL (default: {DEFAULT_API})")
    args = parser.parse_args()

    try:
        data = fetch_all(args.job_id, args.api)
    except Exception as e:
        print(f"Error fetching from API ({args.api}): {e}", file=sys.stderr)
        return 1

    site_label = args.label or (data["job"].get("name") or args.job_id[:8])
    out_path = args.output or f"semantic_{args.job_id[:8]}.html"

    html_text = render(site_label, data)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_text)

    print(f"Wrote {out_path} ({len(html_text):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
