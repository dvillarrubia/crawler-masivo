"""Ajustes de la agencia (decision 196; revision de UX del 25-oct, 2.5).

Lo que vale para todos los clientes: hoy, si estan puestas las claves de los
modelos (la capa de decisiones, decision 84) y su tope mensual. **Nunca un
secreto**: solo si la variable existe y, del tope, la cifra (que no lo es).
Las llaves de Search Console y los tokens de Slack tienen su API en
`credenciales.py`; aqui no se repiten.
"""

from __future__ import annotations

import os

from fastapi import APIRouter

router = APIRouter(prefix="/api", tags=["ajustes"])


def _puesta(nombre: str) -> bool:
    return bool((os.getenv(nombre) or "").strip())


@router.get("/ajustes")
def ajustes():
    from api.routers.enriquecido import _token_configurado

    # Se leen las variables y no `shared.decisiones`: la capa de decisiones no
    # se importa fuera de su camino (decision 84, test_decisiones_no_se_disparan).
    # Mismas reglas que `decisiones_activadas` y `presupuesto_global_mensual`.
    bruto = (os.getenv("ENRICHMENT_MAX_USD_MES") or "").strip()
    try:
        tope = float(bruto) if bruto else None
    except ValueError:
        tope = None   # mal escrito en el .env: no se inventa una cifra
    return {
        "modelos": {
            # La clave del proveedor (OpenRouter) con la que se llama al modelo.
            "clave_modelo": _puesta("OPENROUTER_API_KEY"),
            # La clave de autorizacion que se pide al lanzar un analisis
            # enriquecido (ENRICHMENT_TOKEN), solo si es valida (>= 16).
            "clave_autorizacion": _token_configurado() is not None,
            "tope_mensual_usd": tope,
            # DECISIONS_ENABLED: sin esto la capa no llama aunque haya clave.
            "activada": os.getenv("DECISIONS_ENABLED", "0") == "1",
        },
    }
