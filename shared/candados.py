"""La clave del candado de un job, compartida por quien lo toca entero.

El analisis la toma (`analysis.analyzer.candado_de_job`) para no correr dos
veces a la vez sobre el mismo censo, y el archivado de enlaces (decision 95)
la toma para no sacar de `links` los enlaces de un censo que alguien esta
analizando: con el re-analisis de la referencia (decision 93) eso deja de ser
teorico, porque el censo que se re-analiza es justo uno superado. Vive en
`shared/` porque el archivado no puede importar `analysis/`.
"""

from __future__ import annotations

import hashlib


def clave_de_job(job_id) -> int:
    """Clave estable de 63 bits para el advisory lock, a partir del UUID."""
    return int.from_bytes(hashlib.sha1(str(job_id).encode()).digest()[:8], "big") & 0x7FFFFFFFFFFFFFFF
