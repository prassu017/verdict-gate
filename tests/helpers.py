from copy import deepcopy

DELETE = object()

BASE = {
    "request_id": "req-1042",
    "actor": {"id": "agent-17", "role": "finance_agent"},
    "action": {"type": "transfer_funds", "amount": 24000, "currency": "USD", "destination": "vendor-882"},
    "evidence": {"account_balance": 81000, "vendor_status": "approved", "vendor_status_checked_at": "2026-09-14T20:15:00Z"},
    "evaluated_at": "2026-09-14T20:20:00Z",
}
STALE = "2026-09-14T19:35:00Z"  # 45 minutes before evaluated_at


def make(changes=None, **top_level):
    """Copy of BASE with dotted-path changes, e.g. make({"action.amount": 60000}). DELETE removes a field."""
    request = deepcopy(BASE)
    for path, value in {**(changes or {}), **top_level}.items():
        *parents, field = path.split(".")
        target = request
        for parent in parents:
            target = target[parent]
        if value is DELETE:
            target.pop(field, None)
        else:
            target[field] = value
    return request


def statuses(decision):
    return {check.check: check.status.value for check in decision.checks}
