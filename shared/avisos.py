"""Avisar fuera cuando un rastreo encuentra algo grave.

Una alerta que espera en la interfaz solo sirve si alguien entra. El fallo de
Lopesan —2.367 páginas canonicalizadas a un servidor de pruebas— lo pillamos
porque re-rastreamos ese día por otro motivo.

Es un **webhook genérico**, no una integración con Slack ni un servidor de
correo. Dos razones: el VPS ya ejecuta n8n, así que desde ahí se enruta a
correo, a Slack o a donde haga falta sin meter credenciales en el crawler; y el
cuerpo es JSON con la forma que Slack entiende (`text` + `blocks` no, solo
`text` y los datos aparte), de modo que apuntarlo directo a un *incoming
webhook* de Slack también funciona.

Se manda con `urllib`, no con `requests`: la imagen del worker lleva Scrapy,
pero una dependencia menos en el camino que corre después de CADA rastreo es
una cosa menos que pueda romperlo.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

WEBHOOK_URL = os.getenv("ALERTA_WEBHOOK_URL", "").strip()
# Un aviso que tarda en irse no puede retrasar el cierre de un rastreo.
TIMEOUT = float(os.getenv("ALERTA_TIMEOUT_SEGUNDOS", "10"))
# Para que el enlace del aviso lleve a alguna parte.
BASE_PUBLICA = os.getenv("API_PUBLIC_URL", "").rstrip("/")


def _texto_de_alertas(nombre_job: str, alertas: list[dict]) -> str:
    """El resumen en una frase por alerta, con qué decide Google.

    Lo que se manda NO es «hay 3 alertas»: es qué pasa y sobre cuántas
    páginas. Un aviso que obliga a abrir otra cosa para entenderlo es un aviso
    a medias, que es justo el problema que esto viene a resolver.
    """
    lineas = [f"*{nombre_job}*: algo ha cambiado respecto al rastreo anterior."]
    for a in alertas:
        lineas.append(
            f"• *{a.get('paginas')}* de {a.get('de')} páginas "
            f"({a.get('pct')}%) — {a.get('que_decide_google', '')}"
        )
    return "\n".join(lineas)


def avisar_de_alertas(job_id, nombre_job: str, alertas: list[dict]) -> bool:
    """Manda el aviso. Devuelve si se envió; nunca lanza.

    Best-effort de verdad y con el `try` cubriendo la función entera: esto
    corre al cerrar un rastreo, y que un webhook caído deje un censo en
    `failed` sería cambiar un problema por otro peor.
    """
    try:
        if not WEBHOOK_URL:
            return False
        if not alertas:
            return False
        cuerpo = {
            "text": _texto_de_alertas(nombre_job, alertas),
            "job_id": str(job_id),
            "job": nombre_job,
            "url": f"{BASE_PUBLICA}/#/jobs/{job_id}" if BASE_PUBLICA else None,
            "alertas": [
                {k: a.get(k) for k in
                 ("regla", "severidad", "paginas", "de", "pct",
                  "que_decide_google")}
                for a in alertas
            ],
        }
        datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        peticion = urllib.request.Request(
            WEBHOOK_URL, data=datos,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(peticion, timeout=TIMEOUT) as resp:
            if resp.status >= 300:
                logger.warning("El webhook de avisos respondio %s", resp.status)
                return False
        logger.info("Aviso enviado del job %s (%d alerta(s))",
                    job_id, len(alertas))
        return True
    except Exception:
        logger.exception("No se pudo enviar el aviso del job %s", job_id)
        return False
