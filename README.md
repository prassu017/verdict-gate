# Verdict Gate

Decides whether an agent's already-chosen action may proceed, using only the request, policy and supplied evidence. Returns exactly one verdict: ADMIT, HALT, DEFER or REFER. No LLM, no clock reads.

- **Console:** https://verdict-gate.vercel.app (flow, test run, retries, design and answers)
- **Python:** `verdict_gate.evaluate(request) -> dict`; pure core `decide(request, policy)`
- **HTTP:** `POST /api/evaluate` (recorded), `POST /api/decide` (not recorded)

```bash
pip install -r requirements-dev.txt
pytest
uvicorn app:app --reload
```

## Decision ladder

Every check runs and reports PASS, FAIL, UNKNOWN or N/A. The first gate that fires decides:

1. Uncovered action (P6), disallowed role (P1) or malformed request → **HALT**
2. Fresh evidence shows an unapproved vendor (P3), or amount exceeds balance (P5) → **HALT**
3. Amount above $50,000 (P2) → **REFER**
4. Needed evidence missing, unreadable or older than 30 minutes (P4) → **DEFER**
5. Otherwise → **ADMIT**

## Retries

A ledger (Upstash Redis on Vercel, in-memory locally) maps `request_id` to a canonical payload fingerprint and the decision issued. Writes use `SET NX`, so concurrent retries can't both record.

- **Identical retry:** stored decision returned with `replayed: true`.
- **Same id, changed payload:** HALT `IDEMPOTENCY_CONFLICT` (HTTP 409); the original stands.
- **Policy changed:** a retry keeps its original decision and policy version; a new `request_id` gets the new policy.
- **Evidence changed:** that's a changed payload, so it conflicts under the same id and gets a fresh decision under a new one.
- **Ledger unreachable:** HALT `LEDGER_UNAVAILABLE`.

## Answers

**1. Assumptions.** USD only (other currencies DEFER). The caller supplies a trustworthy `evaluated_at`. Balance carries no timestamp, so it counts as current. Boundaries pass: exactly 30:00 old, exactly $50,000, amount equal to balance. Timestamps need a timezone. Requests need a `request_id`, a destination and a positive amount; anything else HALTs.

**2. DEFER vs REFER.** DEFER means facts are missing, and the same caller can fix that with better evidence. REFER means authority is missing; no evidence lets this service approve.

**3. Precedence.** Authority limits who may say yes, not who may say no. A determinable HALT beats REFER, and REFER beats DEFER because fresh evidence still couldn't yield ADMIT. Stale evidence decides nothing, so stale "suspended" DEFERs. The ladder is one table, `GATES`, in `evaluator.py`.

**4. Sameness.** Two evaluations are the same when policy version and digest match and the canonical decision inputs match. Those inputs are the fields the rules read, with key order, number spelling and timezone spelling normalized (numbers are never rounded). `decision_id` hashes request_id, inputs and policy. Hypothesis tests check this.

**5. Before irreversible production actions.** Server-stamped time; signed evidence with provenance; authenticated callers bound to `actor.id`; a durable append-only audit log; reviewed, signed policy releases; the executor redeeming each `decision_id` exactly once; alerting on unusual HALT or DEFER rates; an independent review of the rules.

**6. Deliberately not built.** Authentication, rate limiting, currency conversion, evidence signatures, a policy DSL or editor, the referral workflow itself, and ledger retention.
