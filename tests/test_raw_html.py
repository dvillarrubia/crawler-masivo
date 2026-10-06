"""El HTML crudo: opcional, aparte, y guardado tambien cuando no hay contenido.

`extraction.store_raw_html` existia en la API y en el formulario desde hacia
meses y no guardaba nada: bandera muerta. Viene apagado por algo — medido en un
censo real, 170 kB de media por pagina, o sea 4,9 GB para 29.808 paginas antes
de que Postgres lo comprima.
"""

from __future__ import annotations

import types

import pytest

pytest.importorskip("fastapi")

from api.routers.results import CSV_COLUMNS  # noqa: E402
from api.schemas import PageContentResponse  # noqa: E402


def test_el_html_no_viaja_en_el_detalle_de_la_url():
    """El detalle lo pide la interfaz al abrir cada fila: si llevara el HTML,
    una ficha seria una descarga de 170 kB."""
    assert "raw_html" not in PageContentResponse.model_fields


def test_el_html_tampoco_sale_en_el_csv():
    assert not any("raw_html" in c for c in CSV_COLUMNS)


def test_la_columna_existe_en_el_modelo():
    from shared.models import PageContent

    assert "raw_html" in PageContent.__table__.columns
    # Y la migracion la aplica sola: scripts/migrate_add_missing_columns.py
    # compara el metadata con la BD viva y anade lo que falte.
    assert PageContent.__table__.columns["raw_html"].nullable is True
