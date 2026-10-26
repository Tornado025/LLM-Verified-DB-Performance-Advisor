"""Pinned BenchBase configuration and raw-unit/error-histogram parsing controls."""

import json
import xml.etree.ElementTree as ET

import pytest

from app.benchbase import ROOT, _config, parse_results, parse_summary
from app.errors import AdvisorError, BadRequest


def test_profile_configs():
    for profile, _mult in [("normal", 1), ("flash5", 5), ("flash10", 10), ("flash20", 20)]:
        root = ET.parse(ROOT / f"benchbase/configs/ch_{profile}.xml").getroot()
        assert root.find("scalefactor").text == "10"
        assert root.find("works/work").get("arrival") == "poisson"
        assert root.find("works/work/weights[@bench='tpcc']").text == "45,43,4,4,4"
        assert len(root.find("works/work/weights[@bench='chbenchmark']").text.split(",")) == 22
        assert root.find("password").text == "${env:DPA_BENCHBASE_PASSWORD}"
    with pytest.raises(BadRequest):
        _config("qc_live", "normal", 10, 60)


def test_parse_raw_latency_units_and_errors(tmp_path):
    (tmp_path / "test.raw.csv").write_text(
        "Transaction Name,Latency (microseconds)\nNewOrder,1000\nNewOrder,3000\nPayment,2000\n"
    )
    hist = {key: {"HISTOGRAM": {}} for key in ["completed", "aborted", "rejected", "unexpected"]}
    hist["unexpected"]["HISTOGRAM"] = {
        "com.oltpbenchmark.benchmarks.tpcc.procedures.NewOrder/01": 2
    }
    hist["completed"]["HISTOGRAM"] = {"com.oltpbenchmark.benchmarks.tpcc.procedures.NewOrder/01": 1}
    (tmp_path / "histograms.json").write_text(json.dumps(hist))
    (tmp_path / "sample.summary.json").write_text(
        json.dumps(
            {
                "Elapsed Time (nanoseconds)": 2_000_000_000,
                "Measured Requests": 7,
                "Throughput (requests/second)": 3.5,
                "Goodput (requests/second)": 2.5,
            }
        )
    )
    parsed = parse_results(tmp_path)
    assert parsed["NewOrder"]["mean"] == 2 and parsed["NewOrder"]["p50"] == 2
    assert parsed["NewOrder"]["count"] == 2 and parsed["NewOrder"]["errors"] == 2
    assert parse_summary(tmp_path)["elapsed_seconds"] == 2
    assert parse_summary(tmp_path)["measured_requests"] == 7
    assert parsed["NewOrder"]["successful_attempts_histogram"] == 1
    assert parsed["Payment"]["errors"] == 0


def test_missing_benchbase_output_fails_closed(tmp_path):
    with pytest.raises(AdvisorError):
        parse_results(tmp_path)
    (tmp_path / "test.raw.csv").write_text(
        "Transaction Name,Latency (microseconds)\nNewOrder,1000\n"
    )
    parsed = parse_results(tmp_path)
    assert parsed["NewOrder"]["errors"] is None
    assert parsed["Delivery"]["count"] == 0 and parsed["Delivery"]["mean"] is None
    assert parse_summary(tmp_path)["throughput_per_second"] is None
    (tmp_path / "histograms.json").write_text("{}")
    assert parse_results(tmp_path)["NewOrder"]["errors"] is None
