"""La version del codigo que hizo cada cosa (decision 66).

Vive en `shared/` y no en el worker porque la escriben DOS: el worker al
marcar un rastreo como `running` (`jobs.crawler_version`) y el analizador al
terminar (`jobs.analisis_version`). Son dos preguntas distintas y hace falta
contestar las dos: un re-analisis cambia las cifras de un censo **sin que
cambie el sitio ni el rastreo**, asi que comparar dos censos analizados con
codigo distinto puede atribuir al cliente un cambio que es nuestro.

Sin argumento de construccion queda `dev`, y no una cadena vacia que parezca
una version.
"""

from __future__ import annotations

import os

VERSION = os.getenv("CRAWLER_VERSION", "dev")
