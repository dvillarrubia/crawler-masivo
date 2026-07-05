"""CLI orquestador del pipeline de entidades, por job_id.

Uso (dentro del contenedor `gliner` o un venv con requirements-gliner):

    python -m analysis.entities.run --job-id <uuid> \
        [--steps gold,pages,queries,catalog,resolve,report] \
        [--gemini-account <nombre>] [--schema-file schema.yaml] \
        [--output-dir /data/informes] [--max-urls N]

- El schema del cliente se lee de la tabla `client_extraction_schemas`
  (o de --schema-file, que además lo guarda en la tabla).
- La API key de Gemini sale de `gemini_accounts` (la del análisis
  semántico: cada cliente paga lo suyo).
- Mide throughput (URLs/min) — criterio de éxito 3 del POC.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import uuid as uuid_mod

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("entities.run")

ALL_STEPS = ["pages", "queries", "catalog", "resolve", "report"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Pipeline GLiNER2 por job")
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--steps", default=",".join(ALL_STEPS))
    ap.add_argument("--gemini-account", default=None,
                    help="nombre en gemini_accounts (default: la primera)")
    ap.add_argument("--schema-file", default=None)
    ap.add_argument("--output-dir", default="informes")
    ap.add_argument("--max-urls", type=int, default=None)
    ap.add_argument("--min-impressions", type=int, default=10)
    ap.add_argument("--model", default=None, help="checkpoint GLiNER2")
    ap.add_argument("--no-quantize", action="store_true")
    ap.add_argument("--gold-out", default=None,
                    help="genera el CSV del gold set y termina")
    ap.add_argument("--gold-eval", default=None,
                    help="evalúa F1 contra un CSV anotado y termina")
    args = ap.parse_args(argv)

    from shared.database import SessionLocal, init_db
    from shared.models import Job

    init_db()
    session = SessionLocal()
    job_id = uuid_mod.UUID(args.job_id)
    job = session.get(Job, job_id)
    if job is None:
        sys.exit(f"Job {job_id} no existe")
    client_id = job.client_id
    if not client_id:
        sys.exit("El job no tiene client_id: el schema de extracción es por cliente")

    # -- gold set: caminos cortos ----------------------------------------
    if args.gold_out:
        from analysis.entities.gold_set import sample_for_annotation

        print(sample_for_annotation(session, job_id, args.gold_out))
        return
    if args.gold_eval:
        from analysis.entities.gold_set import evaluate, gate_verdict
        from analysis.entities.schema_config import load_client_schema

        schema = load_client_schema(session, client_id)
        f1 = evaluate(session, job_id, args.gold_eval)
        print({"f1_por_tipo": f1})
        print(gate_verdict(f1, list(schema.resolubles)))
        return

    # -- schema ------------------------------------------------------------
    from analysis.entities.schema_config import load_client_schema, parse_schema

    if args.schema_file:
        yaml_text = open(args.schema_file, encoding="utf-8").read()
        schema = parse_schema(yaml_text)
        from shared.entity_models import ClientExtractionSchema

        row = session.get(ClientExtractionSchema, client_id)
        if row is None:
            session.add(ClientExtractionSchema(client_id=client_id, yaml_text=yaml_text))
        else:
            row.yaml_text = yaml_text
        session.commit()
        logger.info("Schema de %s guardado en la tabla desde %s",
                    client_id, args.schema_file)
    else:
        schema = load_client_schema(session, client_id)

    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    adapter = None
    if {"pages", "queries"} & set(steps):
        from analysis.entities.gliner_adapter import DEFAULT_MODEL, Gliner2Adapter

        adapter = Gliner2Adapter(schema, model_name=args.model or DEFAULT_MODEL,
                                 quantize=not args.no_quantize)

    def _gemini_key() -> str:
        from shared.semantic_models import GeminiAccount

        q = session.query(GeminiAccount)
        acc = (q.filter(GeminiAccount.name == args.gemini_account).first()
               if args.gemini_account else q.first())
        if acc is None:
            sys.exit("No hay cuenta Gemini (tabla gemini_accounts). "
                     "Créala en la consola → Cuentas.")
        return acc.api_key

    t0 = time.monotonic()
    if "pages" in steps:
        from analysis.entities.pipeline import extract_pages

        stats = extract_pages(session, job_id, schema, adapter,
                              max_urls=args.max_urls)
        session.commit()
        mins = (time.monotonic() - t0) / 60
        stats["urls_por_min"] = round(stats["urls"] / mins, 2) if mins else None
        print({"pages": stats})

    if "queries" in steps:
        from analysis.entities.pipeline import extract_queries

        print({"queries": extract_queries(
            session, job_id, schema, adapter,
            min_impressions=args.min_impressions)})
        session.commit()

    if "catalog" in steps:
        from analysis.entities.resolve import (
            GeminiEntityEmbedder, embed_catalog, seed_catalog_from_crawl,
        )

        if schema.catalogo_fuente == "generado":
            n = seed_catalog_from_crawl(session, client_id, job_id, schema)
            print({"catalog_seeded": n})
        embedder = GeminiEntityEmbedder(_gemini_key())
        print({"catalog_embedded": embed_catalog(session, client_id, embedder)})
        session.commit()

    if "resolve" in steps:
        from analysis.entities.resolve import (
            GeminiEntityEmbedder, GeminiFlashJudge, resolve_job,
        )

        key = _gemini_key()
        print({"resolve": resolve_job(
            session, job_id, client_id, schema,
            GeminiEntityEmbedder(key), GeminiFlashJudge(key))})
        session.commit()

    if "report" in steps:
        from analysis.entities.report import build_report, write_outputs

        report = build_report(session, job_id, client_id)
        print({"report": write_outputs(session, job_id, report,
                                       output_dir=args.output_dir)})
        session.commit()

    session.close()


if __name__ == "__main__":
    main()
