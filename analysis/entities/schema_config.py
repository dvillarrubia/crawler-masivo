"""Parseo y validación del `schema.yaml` por cliente.

Único fichero/registro específico de cliente. Tres bloques (ver brief):
entidades (resolubles + senal, descripciones en lenguaje natural para
GLiNER2), catalogo (fuente) y clasificacion (funnel universal +
tipo_pagina por vertical). Los umbrales de resolución son opcionales y
PROVISIONALES hasta calibrar contra el gold set.
"""
from __future__ import annotations

from dataclasses import dataclass, field

FUNNEL_UNIVERSAL = ["TOFU", "MOFU", "BOFU"]

# PROVISIONALES: se recalibran por barrido contra el gold set (fase 00).
# El 0,92 histórico de canibalización NO se hereda (regla del brief).
DEFAULT_HIGH_THRESHOLD = 0.85
DEFAULT_LOW_THRESHOLD = 0.60


class SchemaError(ValueError):
    """schema.yaml inválido — mensaje pensado para mostrarse en la consola."""


@dataclass
class ExtractionSchema:
    resolubles: dict[str, str] = field(default_factory=dict)   # tipo -> descripción
    senal: dict[str, str] = field(default_factory=dict)
    catalogo_fuente: str = "generado"          # feed | crawl | generado
    catalogo_ruta: str | None = None
    funnel: list[str] = field(default_factory=lambda: list(FUNNEL_UNIVERSAL))
    tipo_pagina: list[str] = field(default_factory=list)
    high_threshold: float = DEFAULT_HIGH_THRESHOLD
    low_threshold: float = DEFAULT_LOW_THRESHOLD

    @property
    def all_entity_types(self) -> dict[str, str]:
        return {**self.resolubles, **self.senal}

    def kind_of(self, entity_type: str) -> str:
        return "resoluble" if entity_type in self.resolubles else "senal"


def parse_schema(yaml_text: str) -> ExtractionSchema:
    """Valida y convierte el YAML del cliente. Errores en castellano."""
    import yaml

    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        raise SchemaError(f"YAML mal formado: {exc}") from exc
    if not isinstance(data, dict):
        raise SchemaError("El schema debe ser un mapa YAML con bloques "
                          "'entidades', 'catalogo' y 'clasificacion'.")

    ent = data.get("entidades") or {}
    if not isinstance(ent, dict):
        raise SchemaError("'entidades' debe ser un mapa con 'resolubles' y "
                          "'senal', no una lista ni un valor suelto.")
    resolubles = ent.get("resolubles") or {}
    senal = ent.get("senal") or {}
    for bloque, nombre in ((resolubles, "resolubles"), (senal, "senal")):
        if not isinstance(bloque, dict):
            raise SchemaError(f"'entidades.{nombre}' debe ser un mapa tipo→descripción.")
        for k, v in bloque.items():
            if not isinstance(v, str) or len(v.strip()) < 10:
                raise SchemaError(
                    f"La descripción de '{nombre}.{k}' debe ser una frase en "
                    "lenguaje natural (GLiNER2 la usa como definición del tipo).")
    if not resolubles:
        raise SchemaError("Hace falta al menos un tipo en 'entidades.resolubles'.")
    solapados = set(resolubles) & set(senal)
    if solapados:
        raise SchemaError(f"Tipos duplicados en resolubles y senal: {sorted(solapados)}")

    cat = data.get("catalogo") or {}
    if not isinstance(cat, dict):
        raise SchemaError("'catalogo' debe ser un mapa (fuente, ruta_o_tabla).")
    fuente = cat.get("fuente", "generado")
    if fuente not in ("feed", "crawl", "generado"):
        raise SchemaError("catalogo.fuente debe ser feed | crawl | generado.")

    cls = data.get("clasificacion") or {}
    if not isinstance(cls, dict):
        raise SchemaError("'clasificacion' debe ser un mapa (funnel, tipo_pagina).")
    funnel = cls.get("funnel") or list(FUNNEL_UNIVERSAL)
    if funnel != FUNNEL_UNIVERSAL:
        raise SchemaError("clasificacion.funnel es universal y fijo: [TOFU, MOFU, BOFU].")
    tipo_pagina = cls.get("tipo_pagina") or []
    if not isinstance(tipo_pagina, list) or not all(isinstance(t, str) for t in tipo_pagina):
        raise SchemaError("clasificacion.tipo_pagina debe ser una lista de etiquetas.")

    umb = data.get("umbrales") or {}
    if not isinstance(umb, dict):
        raise SchemaError("'umbrales' debe ser un mapa (resolucion_alta, resolucion_baja).")
    try:
        high = float(umb.get("resolucion_alta", DEFAULT_HIGH_THRESHOLD))
        low = float(umb.get("resolucion_baja", DEFAULT_LOW_THRESHOLD))
    except (TypeError, ValueError):
        raise SchemaError("umbrales: resolucion_alta y resolucion_baja deben ser números.")
    if not (0.0 < low < high <= 1.0):
        raise SchemaError("umbrales: se exige 0 < resolucion_baja < resolucion_alta ≤ 1.")

    return ExtractionSchema(
        resolubles=dict(resolubles), senal=dict(senal),
        catalogo_fuente=fuente, catalogo_ruta=cat.get("ruta_o_tabla"),
        funnel=list(funnel), tipo_pagina=list(tipo_pagina),
        high_threshold=high, low_threshold=low,
    )


def load_client_schema(session, client_id: str) -> ExtractionSchema:
    """Lee el schema del cliente desde la tabla (convención de la casa)."""
    from shared.entity_models import ClientExtractionSchema

    row = session.get(ClientExtractionSchema, client_id)
    if row is None:
        raise SchemaError(
            f"El cliente '{client_id}' no tiene schema de extracción. "
            "Créalo en Configuración → Extracción de entidades.")
    return parse_schema(row.yaml_text)
