"""Offline smoke coverage for each non-generic migrated capability family."""

from __future__ import annotations


def test_as_rank_demo_query() -> None:
    from caida_ai_ops import as_rank

    result = as_rank.get_customer_cone(15169)
    assert result["status"] == "ok"
    assert result["data"]["asn"] == 15169
    assert any("DEMO" in warning for warning in result["warnings"])


def test_assignment_itdk_demo_query() -> None:
    from caida_ai_ops import itdk_analysis

    result = itdk_analysis.task1_geo_adjacent_links()
    assert result["status"] == "ok"
    assert result["data"]["adjacent_count"] + result["data"]["non_adjacent_count"] > 0
    assert any("DEMO" in warning for warning in result["warnings"])
