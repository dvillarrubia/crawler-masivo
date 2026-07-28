"""Unit tests for structured-data validation (``analysis.sd_validation``).

Pins the conservative validation behaviour: missing ``@type`` is an error,
missing a required property for a known rich-result type is a warning, and
anything else (unknown types, odd shapes) is valid — no false positives.
"""

from __future__ import annotations

from analysis.sd_validation import (
    sd_item_types,
    validate_sd_item,
    validate_structured_data,
)


def test_sd_item_types_variants():
    assert sd_item_types({"@type": "Product"}) == ["Product"]
    assert sd_item_types({"@type": ["Product", "Thing"]}) == ["Product", "Thing"]
    assert sd_item_types({"type": "Article"}) == ["Article"]
    assert sd_item_types({"name": "x"}) == []


def test_valid_product_is_ok():
    assert validate_structured_data({"@type": "Product", "name": "Widget"}) == ("ok", [])


def test_product_missing_name_is_warning():
    status, issues = validate_structured_data({"@type": "Product", "image": "a.png"})
    assert status == "warning"
    assert any("name" in i for i in issues)


def test_missing_type_is_error():
    status, issues = validate_structured_data({"name": "x"})
    assert status == "error"
    assert issues == ["missing @type"]


def test_unknown_type_is_ok():
    # Types we don't have rules for must never be flagged.
    assert validate_structured_data({"@type": "WebPage", "foo": 1}) == ("ok", [])


def test_graph_container_aggregates_children():
    raw = {"@graph": [
        {"@type": "Article", "headline": "h"},   # ok
        {"@type": "Product"},                      # missing name -> warning
    ]}
    status, issues = validate_structured_data(raw)
    assert status == "warning"
    assert any("Product" in i and "name" in i for i in issues)


def test_graph_with_missing_type_is_error():
    raw = {"@graph": [{"@type": "Article", "headline": "h"}, {"name": "no type"}]}
    status, _ = validate_structured_data(raw)
    assert status == "error"


def test_bare_list_of_items():
    raw = [{"@type": "Organization", "name": "Acme"}, {"@type": "Person", "name": "Ann"}]
    assert validate_structured_data(raw) == ("ok", [])


def test_non_dict_non_list_is_ok():
    assert validate_structured_data("nonsense") == ("ok", [])
    assert validate_structured_data(None) == ("ok", [])


def test_multi_type_checks_each_known_type():
    # Missing headline for the Article side of a multi-type node.
    # validate_sd_item returns a flat list of problem strings.
    issues = validate_sd_item({"@type": ["Article", "CreativeWork"], "name": "x"})
    assert any("Article" in i and "headline" in i for i in issues)
