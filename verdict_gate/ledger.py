"""The retry ledger: request_id -> (payload fingerprint, decision issued).

put_if_absent is the only write, so two concurrent retries of one request can't both record.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class LedgerRecord:
    request_id: str
    payload_fingerprint: str
    decision: Mapping[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {"request_id": self.request_id, "payload_fingerprint": self.payload_fingerprint, "decision": self.decision},
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, text: str) -> LedgerRecord:
        data = json.loads(text)
        return cls(data["request_id"], data["payload_fingerprint"], data["decision"])


class Ledger(Protocol):
    def get(self, request_id: str) -> LedgerRecord | None: ...

    def put_if_absent(self, record: LedgerRecord) -> LedgerRecord:
        """Store the record unless one exists for its request_id; return whichever is stored."""
        ...


class InMemoryLedger:
    """Process-local. Fine for tests and a single process; resets with the process."""

    def __init__(self) -> None:
        self._records: dict[str, LedgerRecord] = {}
        self._lock = threading.Lock()

    def get(self, request_id: str) -> LedgerRecord | None:
        with self._lock:
            return self._records.get(request_id)

    def put_if_absent(self, record: LedgerRecord) -> LedgerRecord:
        with self._lock:
            return self._records.setdefault(record.request_id, record)

    def __len__(self) -> int:
        return len(self._records)


class RedisLedger:
    """Upstash Redis over its REST API. SET ... NX makes the first writer win across instances."""

    def __init__(self, url: str, token: str, *, prefix: str = "verdict-gate:ledger:", client: Any = None) -> None:
        if client is None:
            import httpx

            client = httpx.Client(timeout=5.0)
        self._url = url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._prefix = prefix
        self._client = client

    def _command(self, *args: str) -> Any:
        response = self._client.post(self._url, headers=self._headers, json=list(args))
        response.raise_for_status()
        body = response.json()
        if "error" in body:
            raise RuntimeError(f"Redis error: {body['error']}")
        return body.get("result")

    def get(self, request_id: str) -> LedgerRecord | None:
        raw = self._command("GET", self._prefix + request_id)
        return None if raw is None else LedgerRecord.from_json(raw)

    def put_if_absent(self, record: LedgerRecord) -> LedgerRecord:
        if self._command("SET", self._prefix + record.request_id, record.to_json(), "NX") == "OK":
            return record
        return self.get(record.request_id) or record


def ledger_from_env(environ: Mapping[str, str] = os.environ) -> Ledger:
    """Redis when Upstash credentials are present (Vercel Marketplace names either), else in-memory."""
    url = environ.get("UPSTASH_REDIS_REST_URL") or environ.get("KV_REST_API_URL")
    token = environ.get("UPSTASH_REDIS_REST_TOKEN") or environ.get("KV_REST_API_TOKEN")
    if url and token:
        return RedisLedger(url, token)
    return InMemoryLedger()
