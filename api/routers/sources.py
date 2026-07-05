"""Descubrimiento de propiedades a partir de una service account.

Una misma service account de Google suele tener acceso a la vez a
propiedades de Search Console y de Analytics 4. Este router recibe el JSON
pegado y devuelve QUÉ propiedades ve de cada fuente, para que el usuario las
elija de un desplegable en vez de teclear URLs / property_id a mano.

Cada fuente se intenta por separado: una credencial puede tener GSC pero no
GA4 (o al revés), así que cada bloque trae su propio ok/error.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/sources", tags=["sources"])


class DiscoverRequest(BaseModel):
    credentials_json: dict[str, Any]


@router.post("/discover")
def discover_properties(body: DiscoverRequest) -> dict:
    """Devuelve las propiedades GSC y GA4 accesibles por la credencial.

    Formato:
      {
        "gsc": {"ok": bool, "properties": [url, ...], "error": str|None},
        "ga4": {"ok": bool, "properties": [{property_id, display_name}], "error": str|None}
      }
    """
    creds = body.credentials_json
    email = creds.get("client_email") if isinstance(creds, dict) else None

    gsc: dict = {"ok": False, "properties": [], "error": None}
    try:
        from POC_centro_semantico.src.gsc import get_gsc_properties
        gsc["properties"] = get_gsc_properties(creds)
        gsc["ok"] = True
    except ImportError as e:  # pragma: no cover
        gsc["error"] = f"Falta la librería de GSC: {e}"
    except Exception as e:  # noqa: BLE001
        gsc["error"] = str(e)

    ga4: dict = {"ok": False, "properties": [], "error": None}
    try:
        from POC_centro_semantico.src.ga4 import get_ga4_properties
        ga4["properties"] = get_ga4_properties(creds)
        ga4["ok"] = True
    except ImportError:
        ga4["error"] = ("Faltan google-analytics-data/admin en el contenedor "
                        "api (no hace falta si no usas GA4).")
    except Exception as e:  # noqa: BLE001
        ga4["error"] = str(e)

    return {"service_account": email, "gsc": gsc, "ga4": ga4}
