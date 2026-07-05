"""Adaptador del modelo GLiNER2 local (CPU).

Aísla la API de la librería `gliner2` detrás de un contrato mínimo que
el resto del pipeline (y los tests, con un fake) consumen:

    adapter.process(text) -> {
        "entities": [ {text, type, start, end, confidence}, ... ],
        "labels":   { task_name: [(label, score), ...] },
    }

El modelo se carga UNA vez (pesado); `quantize=True` se prueba según el
brief y se mide throughput en el propio run (URLs/min en el log).
GLiNER2 permite schema combinado (entidades + clasificación multi-label)
en un solo forward pass; el adaptador lo construye desde el
ExtractionSchema del cliente.
"""
from __future__ import annotations

import logging
import time

from analysis.entities.schema_config import ExtractionSchema

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "fastino/gliner2-base"

FUNNEL_DESCRIPTIONS = {
    "TOFU": "contenido informativo para descubrir un tema, sin intención de compra",
    "MOFU": "contenido de comparación o evaluación de opciones",
    "BOFU": "contenido transaccional: comprar, contratar, pedir presupuesto",
}


class Gliner2Adapter:
    def __init__(self, schema: ExtractionSchema, *, model_name: str = DEFAULT_MODEL,
                 quantize: bool = True):
        self.schema = schema
        self.model_name = model_name
        self.quantize = quantize
        self._model = None
        self._n_processed = 0
        self._t_started = None

    # -- carga perezosa --------------------------------------------------
    def _load(self):
        if self._model is not None:
            return self._model
        try:
            from gliner2 import GLiNER2  # import pesado: solo en runtime real
        except ImportError as exc:
            raise RuntimeError(
                "El paquete 'gliner2' no está instalado. Usa el contenedor "
                "'gliner' (docker compose --profile gliner ...) o instala "
                "requirements-gliner.txt."
            ) from exc
        logger.info("Cargando GLiNER2 %s (quantize=%s)…", self.model_name, self.quantize)
        try:
            self._model = GLiNER2.from_pretrained(self.model_name, quantize=self.quantize)
        except TypeError:
            # versiones sin flag quantize
            self._model = GLiNER2.from_pretrained(self.model_name)
        self._t_started = time.monotonic()
        return self._model

    # -- schema combinado -------------------------------------------------
    def _build_schema(self, model):
        """Entidades con descripción + 2 tareas de clasificación en un pass."""
        s = model.create_schema()
        for etype, desc in self.schema.all_entity_types.items():
            s = s.entities({etype: desc})
        s = s.classification("funnel", {
            lbl: FUNNEL_DESCRIPTIONS.get(lbl, lbl) for lbl in self.schema.funnel
        }, multi_label=False)
        if self.schema.tipo_pagina:
            s = s.classification("tipo_pagina", {t: t for t in self.schema.tipo_pagina},
                                 multi_label=False)
        return s

    # -- contrato ----------------------------------------------------------
    def process(self, text: str) -> dict:
        model = self._load()
        raw = model.process(text, self._build_schema(model))
        self._n_processed += 1
        return self._normalize_output(raw)

    def throughput_per_min(self, unit_count: int) -> float | None:
        """URLs/min medidas desde la carga del modelo (criterio de éxito 3)."""
        if not self._t_started:
            return None
        elapsed = time.monotonic() - self._t_started
        return round(unit_count / (elapsed / 60.0), 2) if elapsed > 0 else None

    @staticmethod
    def _normalize_output(raw: dict) -> dict:
        """Tolerante con variaciones de la API: normaliza al contrato."""
        entities = []
        for e in (raw or {}).get("entities", []):
            if isinstance(e, dict):
                entities.append({
                    "text": e.get("text") or e.get("span") or "",
                    "type": e.get("type") or e.get("label") or "",
                    "start": e.get("start"),
                    "end": e.get("end"),
                    "confidence": float(e.get("confidence") or e.get("score") or 0.0),
                })
        labels: dict = {}
        for task, val in (raw or {}).items():
            if task == "entities":
                continue
            # formatos vistos: {"funnel": "TOFU"} | {"funnel": [("TOFU", 0.9)]}
            # | {"funnel": {"label": "TOFU", "score": 0.9}}
            if isinstance(val, str):
                labels[task] = [(val, 1.0)]
            elif isinstance(val, dict) and "label" in val:
                labels[task] = [(val["label"], float(val.get("score", 1.0)))]
            elif isinstance(val, list):
                pairs = []
                for item in val:
                    if isinstance(item, (list, tuple)) and len(item) >= 2:
                        pairs.append((str(item[0]), float(item[1])))
                    elif isinstance(item, dict) and "label" in item:
                        pairs.append((item["label"], float(item.get("score", 1.0))))
                    elif isinstance(item, str):
                        pairs.append((item, 1.0))
                if pairs:
                    labels[task] = pairs
        return {"entities": entities, "labels": labels}
