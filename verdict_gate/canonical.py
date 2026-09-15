"""Canonical forms: the only place numbers, timestamps and JSON text are normalized.

Two inputs that differ only in key order, number spelling (24000 vs 24000.00) or
timezone spelling (Z vs +00:00 vs +02:00 for the same instant) canonicalize identically.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$")


def parse_timestamp(value: Any) -> datetime | None:
    """ISO-8601 with seconds and an explicit timezone, converted to UTC. Anything else is None."""
    if not isinstance(value, str) or not _TIMESTAMP.match(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def format_timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def to_decimal(value: Any) -> Decimal | None:
    """A finite JSON number as Decimal. Booleans and strings are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    try:
        number = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def format_decimal(number: Decimal) -> str:
    text = format(number.normalize(), "f")
    return "0" if text == "-0" else text


def canonical_json(value: Any) -> str:
    """Stable JSON text: sorted keys, no whitespace, normalized numbers and UTC timestamps."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float, Decimal)):
        number = to_decimal(value)
        return format_decimal(number) if number is not None else json.dumps(str(value))
    if isinstance(value, str):
        moment = parse_timestamp(value)
        return json.dumps(format_timestamp(moment) if moment else value, ensure_ascii=False)
    if isinstance(value, Mapping):
        members = sorted(value.items(), key=lambda item: str(item[0]))
        return "{" + ",".join(f"{json.dumps(str(k), ensure_ascii=False)}:{canonical_json(v)}" for k, v in members) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(canonical_json(item) for item in value) + "]"
    raise TypeError(f"{type(value).__name__} is not JSON-compatible")


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fingerprint(value: Any) -> str:
    return "sha256:" + sha256_hex(canonical_json(value))
