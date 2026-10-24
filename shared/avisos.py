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

Y ademas, desde la decision 149, al canal de Slack de cada cliente por la API
de Slack (`shared/slack.py`): el webhook sigue para quien enrute por n8n.

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
# La ficha de un rastreo en el frontend (`_irARuta` en `frontend/app.js`).
# Era `#/jobs/{id}`, una ruta que el frontend no conoce y manda a la lista de
# rastreos: el aviso llevaba a todas partes menos al rastreo que avisaba.
RUTA_FICHA_RASTREO = "#/rastreos/{job_id}"


def enlace_al_rastreo(job_id) -> str | None:
    """El enlace del aviso a la ficha del rastreo, o None sin URL publica."""
    if not BASE_PUBLICA:
        return None
    return f"{BASE_PUBLICA}/" + RUTA_FICHA_RASTREO.format(job_id=job_id)


def _texto_de_alertas(nombre_job: str, alertas: list[dict]) -> str:
    """El resumen en una frase por alerta, con qué decide Google.

    Lo que se manda NO es «hay 3 alertas»: es qué pasa y sobre cuántas
    páginas. Un aviso que obliga a abrir otra cosa para entenderlo es un aviso
    a medias, que es justo el problema que esto viene a resolver.
    """
    lineas = [f"*{nombre_job}*: algo ha cambiado respecto al rastreo anterior."]
    for a in alertas:
        if a.get("texto"):
            # Regla propia del cliente (decision 135): ya trae su frase.
            lineas.append(f"• {a['texto']}")
            continue
        lineas.append(
            f"• *{a.get('paginas')}* de {a.get('de')} páginas "
            f"({a.get('pct')}%) — {a.get('que_decide_google', '')}"
        )
    return "\n".join(lineas)


def avisar_de_alertas(job_id, nombre_job: str, alertas: list[dict], cliente=None) -> bool:
    """Manda el aviso al webhook y al Slack del cliente. Devuelve si salio por
    alguno; nunca lanza.

    Best-effort de verdad y con el `try` cubriendo la función entera: esto
    corre al cerrar un rastreo, y que un webhook caído deje un censo en
    `failed` sería cambiar un problema por otro peor.
    """
    if not alertas:
        return False
    por_slack = False
    if cliente is not None:
        from shared.slack import enviar_a_cliente

        por_slack = enviar_a_cliente(cliente, "alertas", _texto_de_alertas(nombre_job, alertas),
                                     enlace_al_rastreo(job_id), "Ver el rastreo") > 0
    return _webhook_de_alertas(job_id, nombre_job, alertas) or por_slack


def _webhook_de_alertas(job_id, nombre_job: str, alertas: list[dict]) -> bool:
    try:
        if not WEBHOOK_URL:
            return False
        cuerpo = {
            "text": _texto_de_alertas(nombre_job, alertas),
            "job_id": str(job_id),
            "job": nombre_job,
            "url": enlace_al_rastreo(job_id),
            "alertas": [
                {k: a.get(k) for k in
                 ("regla", "severidad", "paginas", "de", "pct",
                  "que_decide_google", "texto")}
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


def avisar_de_resumen(nombre_cliente: str, texto: str, contenido: dict, cliente=None) -> bool:
    """El resumen semanal por el mismo webhook (decision 135) y por el Slack
    del cliente (decision 149). Nunca lanza.

    Va con `tipo: resumen_semanal` para que quien lo recibe (n8n) lo pueda
    enrutar aparte de las alertas: un resumen se lee el lunes, una alerta en el
    momento.
    """
    por_slack = False
    if cliente is not None:
        from shared.slack import enviar_a_cliente

        enlace = (f"{BASE_PUBLICA}/#/clientes/{contenido['cliente']['id']}"
                  if BASE_PUBLICA and contenido.get("cliente") else None)
        por_slack = enviar_a_cliente(cliente, "resumen", texto, enlace, "Abrir el cliente") > 0
    return _webhook_de_resumen(nombre_cliente, texto, contenido) or por_slack


def _webhook_de_resumen(nombre_cliente: str, texto: str, contenido: dict) -> bool:
    try:
        if not WEBHOOK_URL:
            return False
        cuerpo = {"tipo": "resumen_semanal", "text": texto, "cliente": nombre_cliente,
                  "url": (f"{BASE_PUBLICA}/#/clientes/{contenido['cliente']['id']}"
                          if BASE_PUBLICA else None),
                  "resumen": contenido}
        datos = json.dumps(cuerpo, ensure_ascii=False, default=str).encode("utf-8")
        peticion = urllib.request.Request(
            WEBHOOK_URL, data=datos,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(peticion, timeout=TIMEOUT) as resp:
            return resp.status < 300
    except Exception:
        logger.exception("No se pudo enviar el resumen semanal de %s", nombre_cliente)
        return False


def texto_de_informe(nombre_job: str, r: dict) -> str:
    """El aviso de «rastreo terminado» de un rastreo programado, a partir de
    `shared.resumen_semanal._resumen_de_rastreo`: lo mismo que ese rastreo
    aporta al resumen, para no tener dos cifras de lo mismo."""
    from shared.resumen_semanal import ETIQUETAS, _num

    lineas = [f"*{nombre_job}* ha terminado."]
    partes = []
    for k, v in (r.get("cifras") or {}).items():
        cambio = v.get("cambio")
        partes.append(f"{_num(v.get('valor'))} {ETIQUETAS[k]}"
                      + (f" ({'+' if cambio > 0 else ''}{_num(cambio)})" if cambio else ""))
    if partes:
        lineas.append("; ".join(partes) + ".")
    inc = r.get("incidencias")
    if inc:
        lineas.append(f"Frente al anterior: {_num(inc.get('nuevas'))} incidencias nuevas"
                      + (f" y {_num(inc['resueltas'])} resueltas" if inc.get("resueltas") is not None else "")
                      + ".")
    elif r.get("incidencias_motivo"):
        lineas.append(f"Sin comparar incidencias: {r['incidencias_motivo']}")
    # Tráfico de Google entre los dos rastreos (decision 158), tal cual lo
    # redacta `trafico_entre_censos.textos`.
    lineas.extend(r.get("trafico") or [])
    criticas = [a for a in r.get("alertas") or [] if a.get("severidad") == "critical"]
    if criticas or r.get("reglas_cliente"):
        lineas.append(f"Con {len(criticas) + len(r.get('reglas_cliente') or [])} alerta(s): "
                      "ya se avisaron aparte.")
    elif r.get("comparable"):
        lineas.append("Sin alertas.")
    return "\n".join(lineas)


def avisar_de_informe(cliente, job, resumen: dict) -> bool:
    """El informe de un rastreo PROGRAMADO, al Slack del cliente que lo tenga
    activado. Los lanzados a mano no: un canario o una prueba no son algo que
    el cliente espere leer. Nunca lanza."""
    try:
        if cliente is None or getattr(job, "programacion_id", None) is None:
            return False
        from shared.slack import enviar_a_cliente

        enlace = (f"{BASE_PUBLICA}/" + RUTA_FICHA_RASTREO.format(job_id=job.id) + "/informe-cliente"
                  if BASE_PUBLICA else None)
        return enviar_a_cliente(cliente, "informe", texto_de_informe(job.name, resumen),
                                enlace, "Ver el informe") > 0
    except Exception:
        logger.exception("No se pudo avisar del informe del job %s", getattr(job, "id", "?"))
        return False


def texto_de_trafico(nombre_job: str, trafico: dict) -> str:
    """El aviso del trafico de Google que esperaba a los datos y ya esta
    (decision 165). Las frases, las de `trafico_entre_censos.textos`: el mismo
    redactor que el informe y la ficha."""
    from shared.trafico_entre_censos import textos

    lineas = [f"*{nombre_job}*: ya hay datos de Search Console para comparar el tráfico de Google "
              "con el rastreo anterior (al terminar aún no habían llegado)."]
    lineas.extend(textos(trafico))
    return "\n".join(lineas)


def avisar_de_trafico(cliente, job, trafico: dict) -> bool:
    """El trafico completado de un rastreo PROGRAMADO, al canal del cliente con
    el «informe» activo: el mismo camino que `avisar_de_informe`, que fue el que
    dijo «aún no tiene datos». Sin Slack configurado, o lanzado a mano, no hace
    nada. Nunca lanza."""
    try:
        if cliente is None or getattr(job, "programacion_id", None) is None:
            return False
        if not isinstance(trafico, dict) or trafico.get("pendiente"):
            return False
        from shared.slack import enviar_a_cliente

        enlace = (f"{BASE_PUBLICA}/" + RUTA_FICHA_RASTREO.format(job_id=job.id) if BASE_PUBLICA else None)
        return enviar_a_cliente(cliente, "informe", texto_de_trafico(job.name, trafico),
                                enlace, "Ver el rastreo") > 0
    except Exception:
        logger.exception("No se pudo avisar del trafico del job %s", getattr(job, "id", "?"))
        return False
