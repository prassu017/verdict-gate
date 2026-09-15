"""HTTP API and console host for Verdict Gate. All decision logic lives in verdict_gate/."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from verdict_gate import CURRENT_POLICY, POLICIES, DecisionService, decide, ledger_from_env
from verdict_gate.cases import load_cases

app = FastAPI(title="Verdict Gate", version="1.0.0")
ledger = ledger_from_env()
service = DecisionService(ledger)

STATUS_BY_REASON = {"IDEMPOTENCY_CONFLICT": 409, "LEDGER_UNAVAILABLE": 503}


async def _body(request: Request):
    try:
        return await request.json(), None
    except ValueError:
        return None, JSONResponse({"error": "The request body must be JSON."}, status_code=400)


def _policy(version: str | None):
    if version is None:
        return CURRENT_POLICY, None
    if version not in POLICIES:
        return None, JSONResponse({"error": f"Unknown policy_version {version!r}.", "known": sorted(POLICIES)}, status_code=400)
    return POLICIES[version], None


@app.post("/api/evaluate")
async def evaluate_endpoint(request: Request, policy_version: str | None = None):
    """Decide and record in the retry ledger."""
    body, error = await _body(request)
    policy, policy_error = _policy(policy_version)
    if error or policy_error:
        return error or policy_error
    decision = service.evaluate(body, policy=policy)
    return JSONResponse(decision, status_code=STATUS_BY_REASON.get(decision["reason_code"], 200))


@app.post("/api/decide")
async def decide_endpoint(request: Request, policy_version: str | None = None):
    """Decide without recording: for inspecting and editing requests in the console."""
    body, error = await _body(request)
    policy, policy_error = _policy(policy_version)
    if error or policy_error:
        return error or policy_error
    return decide(body, policy).to_dict()


@app.get("/api/ledger/{request_id}")
def ledger_endpoint(request_id: str):
    try:
        record = ledger.get(request_id)
    except Exception as failure:  # noqa: BLE001
        return JSONResponse({"error": f"Ledger unavailable: {type(failure).__name__}"}, status_code=503)
    if record is None:
        return JSONResponse({"error": f"No decision recorded for {request_id}."}, status_code=404)
    return {"request_id": record.request_id, "payload_fingerprint": record.payload_fingerprint, "decision": record.decision}


@app.get("/api/policies")
def policies_endpoint():
    return {"current": CURRENT_POLICY.version, "policies": [{**p.to_dict(), "digest": p.digest} for p in POLICIES.values()]}


@app.get("/api/cases")
def cases_endpoint():
    return {"cases": [case.to_dict() for case in load_cases()]}


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return RedirectResponse("/favicon.svg", status_code=307)


@app.get("/api/health")
def health_endpoint():
    return {"status": "ok", "ledger": type(ledger).__name__, "policy": CURRENT_POLICY.version}


# Locally the app serves the console itself; on Vercel public/ is served by the CDN and isn't bundled.
CONSOLE_DIR = Path(__file__).resolve().parent / "public"
if CONSOLE_DIR.is_dir():
    app.mount("/", StaticFiles(directory=CONSOLE_DIR, html=True), name="console")
