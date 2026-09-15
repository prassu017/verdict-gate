import pytest

from verdict_gate import POLICIES, decide
from verdict_gate.cases import load_cases

CASES = load_cases()
BY_ID = {case.id: case for case in CASES}


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_case_verdict(case):
    decision = decide(case.request, POLICIES[case.policy_version])
    assert decision.verdict == case.expected_verdict, decision.reason
    if case.expected_reason_code:
        assert decision.reason_code == case.expected_reason_code


def test_required_categories_are_covered():
    categories = [case.category for case in CASES]
    required = {"Normal ADMIT", "Explicit HALT", "Stale evidence", "Missing evidence", "Another authority", "Unknown action", "Multiple rules"}
    assert required <= set(categories)
    assert categories.count("Multiple rules") >= 2


@pytest.mark.parametrize("case", [c for c in CASES if c.same_decision_as], ids=lambda c: c.id)
def test_equivalent_requests_share_a_decision_id(case):
    other = BY_ID[case.same_decision_as]
    assert decide(case.request, POLICIES[case.policy_version]).to_dict() == decide(other.request, POLICIES[other.policy_version]).to_dict()
