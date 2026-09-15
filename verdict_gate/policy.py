"""Policies are versioned data. Changing a rule means publishing a new version, never editing one."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType

from .canonical import fingerprint, format_decimal


@dataclass(frozen=True, eq=False)
class Policy:
    version: str
    allowed_roles: Mapping[str, tuple[str, ...]]  # action type -> roles that may propose it
    currency: str
    refer_above: Decimal  # amounts strictly greater than this need another authority
    max_evidence_age_seconds: int  # evidence strictly older than this is insufficient
    summary: str

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "allowed_roles": {action: list(roles) for action, roles in sorted(self.allowed_roles.items())},
            "currency": self.currency,
            "refer_above": format_decimal(self.refer_above),
            "max_evidence_age_seconds": self.max_evidence_age_seconds,
            "summary": self.summary,
        }

    @property
    def digest(self) -> str:
        return fingerprint(self.to_dict())


POLICY_V1 = Policy(
    version="fin-transfer/2026-09-14.1",
    allowed_roles=MappingProxyType({"transfer_funds": ("finance_agent",)}),
    currency="USD",
    refer_above=Decimal("50000"),
    max_evidence_age_seconds=30 * 60,
    summary="Assignment policy: finance agents may transfer up to $50,000 to approved vendors on evidence at most 30 minutes old.",
)

# A stricter later version, used to demonstrate that retries keep the decision made under the old one.
POLICY_V2 = Policy(
    version="fin-transfer/2026-10-01.2",
    allowed_roles=MappingProxyType({"transfer_funds": ("finance_agent",)}),
    currency="USD",
    refer_above=Decimal("25000"),
    max_evidence_age_seconds=15 * 60,
    summary="Demonstration policy: referral limit lowered to $25,000 and evidence window to 15 minutes.",
)

POLICIES: Mapping[str, Policy] = MappingProxyType({p.version: p for p in (POLICY_V1, POLICY_V2)})
CURRENT_POLICY = POLICY_V1
