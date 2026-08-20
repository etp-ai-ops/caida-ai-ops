"""The result store: every packet-costing measurement is archived to disk with
enough metadata to be interpretable months later, and readable back.

The store doubles as an audit trail — active probing sends packets from
CAIDA-operated hosts to third parties — so these tests care as much about
*what metadata is recorded* as about the round trip.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def store(tmp_path, monkeypatch, am):
    """Point the result store at a temp dir for the duration of one test."""
    monkeypatch.setenv("MATTHEWPP_RESULTS_DIR", str(tmp_path))
    return tmp_path


def _index(store) -> list[dict]:
    path = store / "index.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


# --- what gets written -----------------------------------------------------


def test_measurement_is_archived(am, store):
    r = am.ping(target="a.example", vp_filter={"region": "africa"}, count=2)
    assert r["status"] == "ok"
    path = r["provenance"]["result_path"]
    assert json.loads(open(path).read())["metadata"]["function"].endswith("ping")


def test_discovery_is_not_archived(am, store):
    """Zero-packet calls must not fill the store. It is a record of probing;
    burying that in list_vps calls would defeat the purpose."""
    am.list_vps(region="africa")
    am.count_vps()
    am.nearest_vps(lat=51.5, lon=-0.13, n=2)
    assert _index(store) == []


def test_nested_calls_write_exactly_one_record(am, store):
    """check_dnssec_valid issues two dns_query calls internally. One
    user-facing measurement must produce one archived record, not three."""
    am.check_dnssec_valid(qname="example.com", vp_filter={"region": "africa"})
    assert len(_index(store)) == 1
    assert _index(store)[0]["function"].endswith("check_dnssec_valid")


def test_rejected_measurement_is_not_archived(am, store):
    """A call rejected by the limits never sent a packet, so there is nothing
    to audit."""
    am.ping(target="a.example", vp_filter={"region": "africa"}, count=999)
    assert _index(store) == []


# --- metadata quality ------------------------------------------------------


def test_record_identifies_which_backend_produced_it(am, store):
    """Without this, synthetic results are indistinguishable from real ones
    once they are sitting in a directory."""
    r = am.ping(target="a.example", vp_filter={"region": "africa"}, count=2)
    rec = json.loads(open(r["provenance"]["result_path"]).read())
    assert rec["metadata"]["backend"] == "demo"
    assert any("DEMO" in w.upper() for w in rec["warnings"])


def test_record_captures_targets_vps_and_parameters(am, store):
    r = am.ping(target="host.example", vp_filter={"region": "africa"}, count=2)
    meta = json.loads(open(r["provenance"]["result_path"]).read())["metadata"]
    assert meta["targets"] == ["host.example"]
    assert meta["vp_count"] == len(meta["vp_ids"]) > 0
    assert meta["parameters"]["count"] == 2  # exact params, for reproduction
    assert meta["host"] and meta["mux"]  # where it ran from


def test_dns_target_is_captured_from_qname(am, store):
    am.dns_query(qname="example.com", vp_filter={"region": "africa"})
    assert _index(store)[0]["targets"] == ["example.com"]


def test_traceroute_archives_vp_locations_for_later_rtt_checks(am, store):
    result = am.traceroute(target="example.com", vp_filter={"region": "east-asia"})

    locations = result["data"]["vp_locations"]
    assert locations
    assert all(location["lat"] is not None and location["lon"] is not None for location in locations.values())

    result_id = am.list_results(function="traceroute")["data"][0]["result_id"]
    archived = am.get_result(result_id)["data"]
    assert archived["data"]["vp_locations"] == locations


def test_records_are_schema_versioned(am, store):
    r = am.ping(target="a.example", vp_filter={"region": "africa"}, count=2)
    assert json.loads(open(r["provenance"]["result_path"]).read())["schema_version"] == 1


# --- reading back ----------------------------------------------------------


def test_list_and_get_round_trip(am, store):
    am.ping(target="a.example", vp_filter={"region": "africa"}, count=2)
    listed = am.list_results()["data"]
    assert len(listed) == 1
    full = am.get_result(listed[0]["result_id"])["data"]
    assert full["metadata"]["targets"] == ["a.example"]


def test_list_is_newest_first(am, store):
    am.ping(target="first.example", vp_filter={"region": "africa"}, count=1)
    am.ping(target="second.example", vp_filter={"region": "africa"}, count=1)
    assert am.list_results()["data"][0]["targets"] == ["second.example"]


def test_list_filters(am, store):
    am.ping(target="a.example", vp_filter={"region": "africa"}, count=1)
    am.dns_query(qname="b.example", vp_filter={"region": "africa"})
    assert len(am.list_results(function="ping")["data"]) == 1
    assert len(am.list_results(target="b.example")["data"]) == 1
    assert len(am.list_results(backend="live")["data"]) == 0


def test_unknown_result_id_errors_clearly(am, store):
    r = am.get_result("nope")
    assert r["status"] == "error"
    assert r["error"]["type"] == "UnknownResultIdError"


def test_torn_index_line_does_not_break_listing(am, store):
    """An interrupted write must not make the whole store unreadable."""
    am.ping(target="a.example", vp_filter={"region": "africa"}, count=1)
    (store / "index.jsonl").open("a").write('{"partial": ')
    assert len(am.list_results()["data"]) == 1


# --- export ----------------------------------------------------------------


def test_export_csv_is_one_row_per_vp(am, store):
    am.ping(target="a.example", vp_filter={"region": "africa"}, count=2)
    rid = am.list_results()["data"][0]["result_id"]
    out = am.export_result(rid, output_format="csv")["data"]
    lines = open(out["path"]).read().strip().splitlines()
    assert lines[0].startswith("vp_id,")
    assert len(lines) - 1 == out["rows"] > 0


def test_export_traceroute_flattens_to_one_row_per_hop(am, store):
    """A nested path is useless in a spreadsheet; hops must become rows."""
    am.traceroute(target="example.com", vp_filter={"region": "east-asia"})
    rid = am.list_results(function="traceroute")["data"][0]["result_id"]
    out = am.export_result(rid, output_format="csv")["data"]
    header = open(out["path"]).read().splitlines()[0]
    assert "ttl" in header and "vp_id" in header


def test_export_json_keeps_metadata(am, store):
    am.ping(target="a.example", vp_filter={"region": "africa"}, count=1)
    rid = am.list_results()["data"][0]["result_id"]
    out = am.export_result(rid, output_format="json")["data"]
    assert json.loads(open(out["path"]).read())["metadata"]["targets"] == ["a.example"]


def test_export_rejects_unknown_format(am, store):
    am.ping(target="a.example", vp_filter={"region": "africa"}, count=1)
    rid = am.list_results()["data"][0]["result_id"]
    r = am.export_result(rid, output_format="xlsx")
    assert r["status"] == "error"
    assert r["error"]["type"] == "UnsupportedFormatError"
