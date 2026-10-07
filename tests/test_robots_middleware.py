"""El middleware de auditoria de robots.txt tiene que montarse como el de Scrapy.

No puede llamar al `__init__` del padre (aborta cuando `ROBOTSTXT_OBEY` es
False, y en modo auditoria lo es a proposito), asi que lo replica. Replicarlo a
ojo dejo fuera `self._stats`: la primera peticion moria con AttributeError, el
rastreo se quedaba a cero paginas y el vigilante lo mataba por estancamiento
media hora despues. Este test compara los dos conjuntos de atributos, asi que
si una version de Scrapy anade otro lo canta aqui y no en un rastreo real.
"""

from __future__ import annotations

import pytest

pytest.importorskip("scrapy")

from scrapy.downloadermiddlewares.robotstxt import RobotsTxtMiddleware  # noqa: E402
from scrapy.utils.test import get_crawler  # noqa: E402

from seo_crawler.middlewares import RobotsAuditMiddleware  # noqa: E402


def test_monta_los_mismos_atributos_que_scrapy():
    padre = RobotsTxtMiddleware(get_crawler(settings_dict={"ROBOTSTXT_OBEY": True}))
    nuestro = RobotsAuditMiddleware(
        get_crawler(settings_dict={"ROBOTSTXT_OBEY": False, "ROBOTS_MODE": "audit"})
    )
    faltan = set(vars(padre)) - set(vars(nuestro))
    assert not faltan, f"atributos que Scrapy crea y nosotros no: {sorted(faltan)}"
    assert nuestro._stats is not None


def test_solo_se_activa_en_modo_auditoria():
    from scrapy.exceptions import NotConfigured

    with pytest.raises(NotConfigured):
        RobotsAuditMiddleware.from_crawler(get_crawler(settings_dict={}))
