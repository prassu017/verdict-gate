"""Why each verdict wins when more than one rule applies, plus the boundaries of each rule."""

from datetime import datetime

import pytest

from verdict_gate import POLICY_V1, decide
from verdict_gate import canonical
from helpers import DELETE, STALE, make, statuses


def test_normal_request_admits_with_every_check_passing():
    decision = decide(make())
    assert decision.verdict == "ADMIT"
    assert decision.gate == 5
    assert set(statuses(decision).values()) == {"PASS"}


# --- collisions ---------------------------------------------------------------------------

def test_insufficient_balance_halts_rather_than_refers():
    decision = decide(make({"action.amount": 60000, "evidence.account_balance": 40000}))
    assert (decision.verdict, decision.gate) == ("HALT", 2)
    assert statuses(decision)["P2"] == "FAIL"  # the referral rule also fired, but a definite no wins


def test_fresh_unapproved_vendor_halts_rather_than_refers():
    decision = decide(make({"action.amount": 60000, "evidence.vendor_status": "suspended"}))
    assert (decision.verdict, decision.gate) == ("HALT", 2)


def test_over_limit_with_stale_evidence_refers_and_hands_over_the_open_question():
    decision = decide(make({"action.amount": 60000, "evidence.vendor_status_checked_at": STALE}))
    assert decision.verdict == "REFER"
    assert statuses(decision)["P4"] == "UNKNOWN"
    assert "Also for the approving authority" in decision.reason


def test_balance_alone_decides_even_when_vendor_evidence_is_stale():
    decision = decide(make({"action.amount": 20000, "evidence.account_balance": 10000, "evidence.vendor_status_checked_at": STALE}))
    assert decision.verdict == "HALT"


def test_stale_negative_vendor_evidence_defers_rather_than_halts():
    decision = decide(make({"evidence.vendor_status": "suspended", "evidence.vendor_status_checked_at": STALE}))
    assert decision.verdict == "DEFER"
    assert statuses(decision)["P3"] == "UNKNOWN"


def test_uncovered_action_halts_whatever_its_amount():
    decision = decide(make({"action.type": "delete_vendor", "action.amount": 90000}))
    assert (decision.verdict, decision.gate) == ("HALT", 1)
    assert statuses(decision)["P2"] == "N/A"


def test_wrong_role_halts_before_the_referral_rule_is_considered():
    decision = decide(make({"actor.role": "support_agent", "action.amount": 60000}))
    assert (decision.verdict, decision.gate) == ("HALT", 1)


def test_missing_balance_and_over_limit_refers():
    decision = decide(make({"action.amount": 60000, "evidence.account_balance": DELETE}))
    assert decision.verdict == "REFER"


# --- boundaries ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("changes", "verdict"),
    [
        ({"evidence.vendor_status_checked_at": "2026-09-14T19:50:00Z"}, "ADMIT"),  # exactly 30:00
        ({"evidence.vendor_status_checked_at": "2026-09-14T19:49:59Z"}, "DEFER"),  # 30:01
        ({"action.amount": 50000}, "ADMIT"),
        ({"action.amount": 50000.01}, "REFER"),
        ({"evidence.account_balance": 24000}, "ADMIT"),
        ({"evidence.account_balance": 23999.99}, "HALT"),
    ],
)
def test_rule_boundaries(changes, verdict):
    assert decide(make(changes)).verdict == verdict


# --- malformed input ----------------------------------------------------------------------

@pytest.mark.parametrize(
    ("changes", "verdict"),
    [
        ({"action.amount": True}, "HALT"),
        ({"action.amount": "24000"}, "HALT"),
        ({"action.amount": 0}, "HALT"),
        ({"request_id": DELETE}, "HALT"),
        ({"actor.role": DELETE}, "HALT"),
        ({"action.currency": "EUR"}, "DEFER"),
        ({"evaluated_at": "yesterday"}, "DEFER"),
        ({"evidence.vendor_status_checked_at": "2026-09-14T20:15:00"}, "DEFER"),  # no timezone
        ({"action.destination": DELETE}, "HALT"),
        ({"action.destination": None}, "HALT"),
        ({"action.destination": "  "}, "HALT"),
        ({"evidence.account_balance": "81000"}, "DEFER"),
    ],
)
def test_malformed_fields(changes, verdict):
    assert decide(make(changes)).verdict == verdict


@pytest.mark.parametrize("request_body", [None, "transfer", 42, [], {}])
def test_non_object_requests_halt(request_body):
    assert decide(request_body).verdict == "HALT"


def test_decide_never_reads_the_clock(monkeypatch):
    class NoClock(datetime):
        @classmethod
        def now(cls, tz=None):
            raise AssertionError("decide() read the clock")

        @classmethod
        def utcnow(cls):
            raise AssertionError("decide() read the clock")

    monkeypatch.setattr(canonical, "datetime", NoClock)
    assert decide(make(), POLICY_V1).verdict == "ADMIT"
