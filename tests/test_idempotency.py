"""The failure scenario: retries, changed payloads, policy changes and evidence changes."""

import threading

from verdict_gate import POLICY_V1, POLICY_V2, DecisionService, InMemoryLedger, LedgerRecord, RedisLedger, evaluate
from helpers import STALE, make


def service():
    return DecisionService(InMemoryLedger())


def test_identical_retry_returns_the_stored_decision():
    svc = service()
    first = svc.evaluate(make())
    second = svc.evaluate(make())
    assert first["replayed"] is False
    assert second == {**first, "replayed": True}


def test_retry_spelled_differently_is_still_identical():
    svc = service()
    first = svc.evaluate(make())
    respelled = make({"evaluated_at": "2026-09-14T20:20:00+00:00", "action.amount": 24000.0})
    assert svc.evaluate(dict(reversed(list(respelled.items()))))["decision_id"] == first["decision_id"]


def test_changed_payload_under_the_same_request_id_conflicts_and_keeps_the_original():
    svc = service()
    first = svc.evaluate(make())
    conflict = svc.evaluate(make({"action.amount": 42000}))
    assert conflict["verdict"] == "HALT"
    assert conflict["reason_code"] == "IDEMPOTENCY_CONFLICT"
    assert conflict["original_decision_id"] == first["decision_id"]
    assert svc.evaluate(make())["decision_id"] == first["decision_id"]  # original record untouched


def test_any_payload_change_conflicts_even_if_the_verdict_would_not_change():
    svc = service()
    svc.evaluate(make())
    assert svc.evaluate(make({"actor.id": "agent-99"}))["reason_code"] == "IDEMPOTENCY_CONFLICT"


def test_policy_change_does_not_change_a_retried_decision():
    svc = service()
    request = make({"request_id": "req-3001", "action.amount": 30000})
    first = svc.evaluate(request, policy=POLICY_V1)
    retry = svc.evaluate(request, policy=POLICY_V2)
    assert first["verdict"] == "ADMIT"
    assert retry == {**first, "replayed": True}
    assert retry["policy_version"] == POLICY_V1.version

    new_question = svc.evaluate({**request, "request_id": "req-3002"}, policy=POLICY_V2)
    assert (new_question["verdict"], new_question["policy_version"]) == ("REFER", POLICY_V2.version)


def test_evidence_change_needs_a_new_request_id():
    svc = service()
    stale = make({"request_id": "req-4001", "evidence.vendor_status_checked_at": STALE})
    refreshed = make({"request_id": "req-4001", "evidence.vendor_status_checked_at": "2026-09-14T20:18:00Z"})
    assert svc.evaluate(stale)["verdict"] == "DEFER"
    assert svc.evaluate(refreshed)["reason_code"] == "IDEMPOTENCY_CONFLICT"
    assert svc.evaluate({**refreshed, "request_id": "req-4002"})["verdict"] == "ADMIT"


def test_requests_without_an_id_halt_and_are_not_recorded():
    ledger = InMemoryLedger()
    request = make()
    del request["request_id"]
    assert DecisionService(ledger).evaluate(request)["verdict"] == "HALT"
    assert len(ledger) == 0


def test_concurrent_identical_retries_record_one_decision():
    ledger = InMemoryLedger()
    svc = DecisionService(ledger)
    results, barrier = [], threading.Barrier(16)

    def call():
        barrier.wait()
        results.append(svc.evaluate(make()))

    threads = [threading.Thread(target=call) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len({result["decision_id"] for result in results}) == 1
    assert len(ledger) == 1


def test_ledger_failure_fails_closed():
    class BrokenLedger:
        def get(self, request_id):
            raise ConnectionError("redis down")

        def put_if_absent(self, record):
            raise ConnectionError("redis down")

    decision = DecisionService(BrokenLedger()).evaluate(make())
    assert (decision["verdict"], decision["reason_code"]) == ("HALT", "LEDGER_UNAVAILABLE")
    assert decision["unrecorded_verdict"] == "ADMIT"


def test_redis_ledger_first_writer_wins():
    class FakeUpstash:
        def __init__(self):
            self.store, self.commands = {}, []

        def post(self, url, headers, json):
            self.commands.append(json)
            command, key, *rest = json
            if command == "GET":
                result = self.store.get(key)
            else:
                result = None if key in self.store else "OK"
                self.store.setdefault(key, rest[0])
            return FakeResponse({"result": result})

    class FakeResponse:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self.body

    client = FakeUpstash()
    ledger = RedisLedger("https://example.upstash.io", "token", client=client)
    first = LedgerRecord("req-1", "sha256:a", {"decision_id": "dec_1"})
    second = LedgerRecord("req-1", "sha256:b", {"decision_id": "dec_2"})
    assert ledger.put_if_absent(first) == first
    assert ledger.put_if_absent(second) == first
    assert client.commands[0][-1] == "NX"


def test_module_level_evaluate_returns_the_required_fields():
    decision = evaluate(make({"request_id": "req-module-level"}))
    assert {"request_id", "verdict", "reason", "policy_version", "evaluated_at", "decision_id"} <= decision.keys()
    assert decision["verdict"] in {"ADMIT", "HALT", "DEFER", "REFER"}
