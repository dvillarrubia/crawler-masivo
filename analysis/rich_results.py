"""Validación de resultados enriquecidos (rich results) de datos estructurados.

El crawler ya EXTRAE el JSON-LD/microdata (`structured_data.raw` + `schema_type`),
pero nadie comprobaba si cumple los requisitos de Google para salir como
resultado enriquecido. Esto lo hace: por cada tipo de schema conocido, valida
los campos OBLIGATORIOS (si faltan → error, el rich result no aparece) y los
RECOMENDADOS (si faltan → warning, aparece pero peor).

Función pura `validate_rich_result(schema_type, raw)` → (status, issues):
status ∈ {"ok","warning","error", None (tipo no validable)}; issues = lista de
{field, level, message} en castellano. Fuente: requisitos documentados por
Google (search.google.com/structured-data). Se mantiene deliberadamente
conservador: solo tipos con requisitos claros, presencia de campo (no valida
formatos internos), para no generar falsos positivos.
"""
from __future__ import annotations

from typing import Any

# Requisitos por tipo. `one_of_required` = al menos uno del grupo.
# Alias: varios @type mapean al mismo spec (subtipos de Article/LocalBusiness).
REQUIREMENTS: dict[str, dict] = {
    "Product": {
        "required": ["name"],
        "one_of_required": [["offers", "review", "aggregateRating"]],
        "recommended": ["image", "brand", "description"],
    },
    "Article": {
        "required": ["headline"],
        "recommended": ["image", "datePublished", "author", "dateModified"],
    },
    "Recipe": {
        "required": ["name", "image"],
        "recommended": ["recipeIngredient", "recipeInstructions",
                        "aggregateRating", "author", "datePublished"],
    },
    "FAQPage": {
        "required": ["mainEntity"],
        "recommended": [],
    },
    "QAPage": {
        "required": ["mainEntity"],
        "recommended": [],
    },
    "HowTo": {
        "required": ["name", "step"],
        "recommended": ["image", "totalTime", "tool", "supply"],
    },
    "BreadcrumbList": {
        "required": ["itemListElement"],
        "recommended": [],
    },
    "Organization": {
        "required": ["name"],
        "recommended": ["url", "logo", "sameAs", "contactPoint"],
    },
    "LocalBusiness": {
        "required": ["name", "address"],
        "recommended": ["telephone", "openingHours", "geo", "priceRange", "image"],
    },
    "Event": {
        "required": ["name", "startDate", "location"],
        "recommended": ["endDate", "offers", "image", "description", "performer"],
    },
    "JobPosting": {
        "required": ["title", "description", "datePosted",
                     "hiringOrganization", "jobLocation"],
        "recommended": ["baseSalary", "employmentType", "validThrough"],
    },
    "Review": {
        "required": ["itemReviewed", "reviewRating", "author"],
        "recommended": ["datePublished", "reviewBody"],
    },
    "VideoObject": {
        "required": ["name", "description", "thumbnailUrl", "uploadDate"],
        "recommended": ["duration", "contentUrl", "embedUrl"],
    },
    "Course": {
        "required": ["name", "description", "provider"],
        "recommended": ["offers", "hasCourseInstance"],
    },
    "SoftwareApplication": {
        "required": ["name", "offers", "aggregateRating"],
        "recommended": ["operatingSystem", "applicationCategory"],
    },
}

# Subtipos que comparten spec con un tipo base.
_ALIASES = {
    "NewsArticle": "Article", "BlogPosting": "Article",
    "TechArticle": "Article", "ScholarlyArticle": "Article",
    "Restaurant": "LocalBusiness", "Store": "LocalBusiness",
    "ProfessionalService": "LocalBusiness", "Dentist": "LocalBusiness",
    "MedicalBusiness": "LocalBusiness", "FoodEstablishment": "LocalBusiness",
    "OnlineStore": "Organization", "Corporation": "Organization",
    "NGO": "Organization", "EducationalOrganization": "Organization",
    "MusicEvent": "Event", "SportsEvent": "Event", "TheaterEvent": "Event",
    "BusinessEvent": "Event", "EducationEvent": "Event",
    "IndividualProduct": "Product", "ProductModel": "Product",
    "MobileApplication": "SoftwareApplication",
    "WebApplication": "SoftwareApplication",
}


def _norm_type(schema_type: Any) -> str | None:
    """Normaliza el @type a un nombre simple (última parte de una URL, sin @)."""
    if isinstance(schema_type, list):
        # varios tipos: elige el primero que sepamos validar
        for t in schema_type:
            n = _norm_type(t)
            if n and (n in REQUIREMENTS or n in _ALIASES):
                return n
        return _norm_type(schema_type[0]) if schema_type else None
    if not isinstance(schema_type, str):
        return None
    name = schema_type.strip().rstrip("/").split("/")[-1].split(":")[-1]
    return name or None


def _spec_for(schema_type: Any) -> tuple[str, dict] | None:
    name = _norm_type(schema_type)
    if name is None:
        return None
    base = _ALIASES.get(name, name)
    spec = REQUIREMENTS.get(base)
    return (name, spec) if spec else None


def _resolve_node(raw: Any, schema_type: Any) -> dict | None:
    """Extrae el nodo dict relevante del `raw` (que puede ser lista, tener
    @graph, o ser ya el objeto)."""
    name = _norm_type(schema_type)

    def _matches(node: dict) -> bool:
        t = node.get("@type") or node.get("type")
        return _norm_type(t) == name if t is not None else False

    if isinstance(raw, dict):
        if "@graph" in raw and isinstance(raw["@graph"], list):
            for node in raw["@graph"]:
                if isinstance(node, dict) and _matches(node):
                    return node
        return raw
    if isinstance(raw, list):
        for node in raw:
            if isinstance(node, dict) and _matches(node):
                return node
        for node in raw:
            if isinstance(node, dict):
                return node
    return None


def _has(node: dict, field: str) -> bool:
    """El campo está presente y no vacío. Acepta la variante con @ (JSON-LD)
    y algunas equivalencias schema.org habituales."""
    for key in (field, "@" + field):
        if key in node:
            v = node[key]
            if v is None:
                continue
            if isinstance(v, (list, dict, str)) and len(v) == 0:
                continue
            return True
    return False


def validate_rich_result(schema_type: Any, raw: Any) -> tuple[str | None, list | None]:
    """Valida un bloque de datos estructurados contra los requisitos de rich
    result de su tipo. Devuelve (status, issues).

    status: "error" (falta algo obligatorio), "warning" (falta recomendado),
    "ok" (cumple), o None si el tipo no es validable (no se toca)."""
    spec = _spec_for(schema_type)
    if spec is None:
        return None, None
    name, req = spec

    node = _resolve_node(raw, schema_type)
    if not isinstance(node, dict):
        return None, None

    issues: list[dict] = []
    for f in req.get("required", []):
        if not _has(node, f):
            issues.append({"field": f, "level": "error",
                           "message": f"Falta el campo obligatorio «{f}» para {name}"})
    for group in req.get("one_of_required", []):
        if not any(_has(node, f) for f in group):
            issues.append({"field": "/".join(group), "level": "error",
                           "message": f"{name} necesita al menos uno de: {', '.join(group)}"})
    for f in req.get("recommended", []):
        if not _has(node, f):
            issues.append({"field": f, "level": "warning",
                           "message": f"Falta el campo recomendado «{f}» para {name}"})

    if any(i["level"] == "error" for i in issues):
        return "error", issues
    if issues:
        return "warning", issues
    return "ok", None
