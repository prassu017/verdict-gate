"""Identical canonical decision inputs must produce the same determination, every time."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from verdict_gate import decide
from verdict_gate.canonical import fingerprint, format_timestamp
from helpers import BASE, make

EVALUATED_AT = datetime(2026, 9, 14, 20, 20, tzinfo=timezone.utc)


def _shuffled(value, rng):
    if isinstance(value, dict):
        items = list(value.items())
        rng.shuffle(items)
        return {key: _shuffled(item, rng) for key, item in items}
    return value


@given(st.randoms(use_true_random=False))
def test_key_order_never_changes_the_decision(rng):
    shuffled = _shuffled(deepcopy(BASE), rng)
    assert decide(shuffled).to_dict() == decide(BASE).to_dict()
    assert fingerprint(shuffled) == fingerprint(BASE)


def test_number_and_timezone_spellings_are_canonical():
    variants = [
        make({"action.amount": 24000.0}),
        make({"action.amount": Decimal("24000.00")}),
        make({"evaluated_at": "2026-09-14T22:20:00+02:00", "evidence.vendor_status_checked_at": "2026-09-14T16:15:00-04:00"}),
    ]
    expected = decide(BASE).decision_id
    assert all(decide(variant).decision_id == expected for variant in variants)


def test_different_request_ids_get_different_decision_ids_but_the_same_input_hash():
    a, b = decide(make()), decide(make({"request_id": "req-9999"}))
    assert a.decision_id != b.decision_id
    assert a.input_hash == b.input_hash


def _request(amount, balance, status, age_seconds, role, kind, currency):
    request = make({"action.amount": amount, "action.type": kind, "action.currency": currency, "actor.role": role})
    evidence = request["evidence"]
    for key, value in (("account_balance", balance), ("vendor_status", status)):
        if value is None:
            del evidence[key]
        else:
            evidence[key] = value
    if age_seconds is None:
        del evidence["vendor_status_checked_at"]
    else:
        evidence["vendor_status_checked_at"] = format_timestamp(EVALUATED_AT - timedelta(seconds=age_seconds))
    return request


requests = st.builds(
    _request,
    amount=st.one_of(st.integers(-10, 200_000), st.decimals(min_value=Decimal("0.01"), max_value=Decimal("200000"), places=2).map(float)),
    balance=st.one_of(st.none(), st.integers(0, 200_000)),
    status=st.sampled_from(["approved", "pending", "suspended", None]),
    age_seconds=st.one_of(st.none(), st.integers(-600, 7200)),
    role=st.sampled_from(["finance_agent", "support_agent"]),
    kind=st.sampled_from(["transfer_funds", "delete_vendor"]),
    currency=st.sampled_from(["USD", "USD", "EUR"]),
)


@settings(max_examples=400)
@given(requests)
def test_precedence_invariants_hold_for_any_request(request):
    decision = decide(request)
    again = decide(deepcopy(request))
    status = {check.check: check.status.value for check in decision.checks}

    assert decision.to_dict() == again.to_dict()
    assert (decision.verdict == "ADMIT") == all(value == "PASS" for value in status.values())
    if decision.verdict == "DEFER":
        assert "FAIL" not in status.values()
    if decision.verdict == "REFER":
        assert status["P2"] == "FAIL" and "FAIL" not in {status[name] for name in ("P6", "P1", "REQ", "P3", "P5")}
    if status["P5"] == "FAIL":
        assert decision.verdict == "HALT"
