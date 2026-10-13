import json
import runpy
from pathlib import Path

import pytest
from test_cdc_surveillance import client, envelope


def test_receipt_preserves_admitted_request_and_checks_problem_response(monkeypatch, capsys):
    probe = runpy.run_path(str(Path(__file__).parents[1] / "scripts/probe_cdc_api.py"))
    def producer(measure, county):
        if county == "08013":
            raise PermissionError("missing exact retained admission")
        return envelope(measure, county)

    monkeypatch.setattr("httpx.Client", lambda **_: client(producer))
    monkeypatch.setattr("sys.argv", ["probe_cdc_api.py", "--base-url", "http://testserver"])
    probe["main"]()
    receipt = json.loads(capsys.readouterr().out)
    assert [x["request"]["geography_id"] for x in receipt["accepted"]] == [
        "13081", "19135", "12047"
    ]
    assert receipt["unapproved"]["request"]["geography_id"] == "08013"
    assert receipt["unapproved_http_status"] == receipt["unapproved"]["response"]["status"] == 503
    assert all(x["elapsed_ms"] >= 0 for x in receipt["accepted"])


def test_gateway_504_is_not_accepted_as_application_unavailable(monkeypatch, capsys):
    import httpx

    api = client()
    original_get = api.get

    def get(url, **kwargs):
        if kwargs["params"]["geography_id"] == "08013":
            return httpx.Response(
                504, content=b"<html>gateway page</html>", headers={
                    "content-type": "text/html", "x-do-orig-status": "503"
                }
            )
        return original_get(url, **kwargs)

    monkeypatch.setattr(api, "get", get)
    monkeypatch.setattr("httpx.Client", lambda **_: api)
    monkeypatch.setattr("sys.argv", ["probe_cdc_api.py", "--base-url", "http://testserver"])
    probe = runpy.run_path(str(Path(__file__).parents[1] / "scripts/probe_cdc_api.py"))
    with pytest.raises(SystemExit, match="HTTP 504; .*upstream status 503"):
        probe["main"]()
    output = capsys.readouterr().out
    assert "text/html" in output
    assert "gateway page" not in output
