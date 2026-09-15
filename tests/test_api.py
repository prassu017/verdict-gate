import uuid

from fastapi.testclient import TestClient

from app import app
from verdict_gate import POLICY_V2
from verdict_gate.cases import load_cases
from helpers import make

client = TestClient(app)


def fresh(changes=None):
    return make({"request_id": f"req-{uuid.uuid4().hex[:8]}", **(changes or {})})


def test_evaluate_returns_a_decision():
    response = client.post("/api/evaluate", json=fresh())
    assert response.status_code == 200
    body = response.json()
    assert body["verdict"] == "ADMIT"
    assert {"request_id", "reason", "policy_version", "evaluated_at", "decision_id"} <= body.keys()


def test_retry_replays_and_changed_payload_is_409_with_a_decision_body():
    request = fresh()
    first = client.post("/api/evaluate", json=request).json()
    assert client.post("/api/evaluate", json=request).json() == {**first, "replayed": True}

    conflict = client.post("/api/evaluate", json={**request, "evaluated_at": "2026-09-14T20:21:00Z"})
    assert conflict.status_code == 409
    assert conflict.json()["original_decision_id"] == first["decision_id"]
    assert client.get(f"/api/ledger/{request['request_id']}").json()["decision"]["decision_id"] == first["decision_id"]


def test_decide_does_not_record():
    request = fresh()
    assert client.post("/api/decide", json=request).status_code == 200
    assert client.post("/api/decide", json={**request, "evaluated_at": "2026-09-14T20:21:00Z"}).status_code == 200
    assert client.get(f"/api/ledger/{request['request_id']}").status_code == 404


def test_policy_version_can_be_selected_and_unknown_versions_are_rejected():
    request = fresh({"action.amount": 30000})
    assert client.post(f"/api/decide?policy_version={POLICY_V2.version}", json=request).json()["verdict"] == "REFER"
    assert client.post("/api/decide?policy_version=nope", json=request).status_code == 400


def test_invalid_json_is_rejected():
    response = client.post("/api/evaluate", content=b"{not json", headers={"content-type": "application/json"})
    assert response.status_code == 400


def test_reference_endpoints():
    assert len(client.get("/api/cases").json()["cases"]) == len(load_cases())
    policies = client.get("/api/policies").json()
    assert policies["current"] in {p["version"] for p in policies["policies"]}
    assert client.get("/api/health").json()["status"] == "ok"
