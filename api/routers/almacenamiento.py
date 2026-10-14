"""Cuanto ocupa la base de datos y en que (decision 96).

Existe para poder decidir QUE borrar sin entrar en el servidor: sin esto, la
unica forma de saber que el disco se esta llenando es que se llene, y la unica
pista de que censo pesa es su numero de URLs, que no dice nada de los enlaces
—la tabla que de verdad crece: 46 GB y 181 millones de filas en produccion
antes de agrupar los enlaces (decision 94)—. Los Parquet de los censos
archivados (decision 95) van aparte: estan fuera de Postgres.

Router propio y no `/api/jobs/almacenamiento`: en el router de jobs la ruta
caeria debajo de `GET /{job_id}`, que declara el id como UUID, y la peticion
moriria con un 422 sobre un id invalido en vez de contestar.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from shared.almacenamiento import resumen_de_disco
from shared.database import get_session

from api.schemas import Almacenamiento

router = APIRouter(prefix="/api/almacenamiento", tags=["almacenamiento"])


@router.get("", response_model=Almacenamiento)
def ver_almacenamiento(db: Session = Depends(get_session)):
    """Tamano total de la base y desglose por tabla, de mas a menos.

    Lo lee `pg_total_relation_size`, asi que incluye indices y TOAST, que es
    la mitad del peso en `links` y en `page_content`. Las filas por tabla son
    `reltuples` —la estimacion del planificador— y no un `count(*)`: contar
    exacto son minutos en la tabla grande y aqui se quiere un orden de
    magnitud, no una cifra para un informe.
    """
    return resumen_de_disco(db)
