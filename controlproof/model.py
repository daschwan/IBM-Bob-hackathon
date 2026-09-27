"""ControlProof core state model — B1.

Pure Python 3.12, standard library only. No file I/O, no parsing of Bob artifacts.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

# ---------------------------------------------------------------------------
# 1. Tri-valued states
# ---------------------------------------------------------------------------


class _NotDetermined:
    """Singleton sentinel. bool() raises TypeError; compare with ``is``."""

    _instance: "_NotDetermined | None" = None

    def __new__(cls) -> "_NotDetermined":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __bool__(self) -> bool:
        raise TypeError(
            "NOT_DETERMINED cannot be used as a boolean. Compare with 'is'."
        )

    def __repr__(self) -> str:
        return "NOT_DETERMINED"


NOT_DETERMINED = _NotDetermined()

# tri type alias — True, False, or NOT_DETERMINED
tri = bool | _NotDetermined

STATES = ("configured", "executed", "observed", "enforceable", "enforced")


def state_symbol(value: tri) -> str:
    """Return ✓, ✕, or — for tri-valued state."""
    if value is NOT_DETERMINED:
        return "\u2014"
    if value is True:
        return "\u2713"
    if value is False:
        return "\u2715"
    raise TypeError(f"state_symbol: expected True, False or NOT_DETERMINED, got {value!r}")


def state_to_json(value: tri) -> "bool | str":
    """Serialize a tri-valued state to a JSON-compatible type."""
    if value is NOT_DETERMINED:
        return "not_determined"
    if value is True:
        return True
    if value is False:
        return False
    raise TypeError(f"state_to_json: expected True, False or NOT_DETERMINED, got {value!r}")


# ---------------------------------------------------------------------------
# 2. Lifecycle authority
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class LifecycleAuthority:
    """Per-Bob-version table: which hook events can block a tool call."""

    bob_version: str
    blocking: Mapping[str, bool]
    source: str

    def blocking_capable(self, event: "str | None") -> tri:
        """Return the blocking capability for *event*, or NOT_DETERMINED."""
        if event is None or event not in self.blocking:
            return NOT_DETERMINED
        return self.blocking[event]


AUTHORITIES: dict[str, LifecycleAuthority] = {
    "2.1.0": LifecycleAuthority(
        bob_version="2.1.0",
        blocking={
            "SessionStart": False,
            "UserPromptSubmit": True,
            "PreToolUse": True,
            "PostToolUse": False,
            "Stop": False,
        },
        source=(
            "IBM Bob 1.126.0+bob2.1.0 hook runtime; only UserPromptSubmit and PreToolUse "
            "honour a blocking exit (exit code 2), other events log \"<event> hooks cannot "
            "block\"; observed in the author's pre-event research "
            "SPIKE-BOB-CONTROLPROOF-001 (2026-09-20)."
        ),
    ),
    "2.2.0": LifecycleAuthority(
        bob_version="2.2.0",
        blocking={
            "SessionStart": False,
            "UserPromptSubmit": True,
            "PreToolUse": True,
            "PostToolUse": False,
            "PostCompact": False,
            "Stop": False,
        },
        source=(
            "IBM Bob 1.126.0+bob2.2.0 (commit 30bf4b86) hook runtime in the bob-code "
            "extension; exit code 2 blocks except at SessionStart, PostCompact, PostToolUse "
            "and Stop, which log \"<event> hooks cannot block\"; read from the installed "
            "extension on 2026-09-26. PreCompact is deliberately absent: it fires around "
            "context compaction, not around a tool call, and whether its blocking exit can "
            "prevent a tool call is not established, so it stays NOT_DETERMINED."
        ),
    ),
}


def authority_for(bob_version: "str | None") -> "LifecycleAuthority | None":
    """Return the LifecycleAuthority for *bob_version*, or None."""
    if bob_version is None:
        return None
    return AUTHORITIES.get(bob_version)


def blocking_capable(event: "str | None", authority: "LifecycleAuthority | None") -> tri:
    """Return blocking capability; NOT_DETERMINED when authority is None."""
    if authority is None:
        return NOT_DETERMINED
    return authority.blocking_capable(event)


# ---------------------------------------------------------------------------
# 3. Evidence schema
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Conflict:
    """A load-bearing contradiction between artifacts."""

    code: str
    artifacts: tuple[str, ...]
    detail: str
    affects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for entry in self.affects:
            if entry not in STATES:
                raise TypeError(
                    f"Conflict.affects entry {entry!r} is not a valid state; "
                    f"must be one of {STATES}"
                )


_TRI_VALUES = (True, False, NOT_DETERMINED)
_TRI_FIELDS = (
    "configured",
    "ledger_corroborated",
    "target_in_payload",
    "policy_triggered",
    "bob_cancelled_citing_control",
    "target_exists_after",
)
_BOOL_FIELDS = ("absence_witnessed", "bundle_verified")
_OPTIONAL_STR_FIELDS = ("session_id", "bob_version", "configured_event", "actual_event")


@dataclasses.dataclass(frozen=True)
class ControlFacts:
    """Normalized facts about one control in one Bob session."""

    control_id: str
    session_id: "str | None" = None
    bob_version: "str | None" = None
    configured: tri = NOT_DETERMINED
    configured_event: "str | None" = None
    ledger_rows: int = 0
    ledger_corroborated: tri = NOT_DETERMINED
    absence_witnessed: bool = False
    actual_event: "str | None" = None
    target_in_payload: tri = NOT_DETERMINED
    policy_triggered: tri = NOT_DETERMINED
    bob_cancelled_citing_control: tri = NOT_DETERMINED
    target_exists_after: tri = NOT_DETERMINED
    conflicts: tuple[Conflict, ...] = ()
    bundle_verified: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.control_id, str) or not self.control_id:
            raise TypeError("control_id must be a non-empty str")
        for field in _TRI_FIELDS:
            v = getattr(self, field)
            if v is not True and v is not False and v is not NOT_DETERMINED:
                raise TypeError(
                    f"ControlFacts.{field} must be True, False or NOT_DETERMINED, got {v!r}"
                )
        for field in _BOOL_FIELDS:
            v = getattr(self, field)
            if type(v) is not bool:
                raise TypeError(
                    f"ControlFacts.{field} must be exactly bool, got {type(v).__name__!r}"
                )
        if type(self.ledger_rows) is not int or isinstance(self.ledger_rows, bool):
            raise TypeError("ControlFacts.ledger_rows must be exactly int")
        if self.ledger_rows < 0:
            raise ValueError("ControlFacts.ledger_rows must be >= 0")
        if not isinstance(self.conflicts, tuple) or not all(
            isinstance(c, Conflict) for c in self.conflicts
        ):
            raise TypeError("ControlFacts.conflicts must be a tuple of Conflict")
        for field in _OPTIONAL_STR_FIELDS:
            v = getattr(self, field)
            if v is not None and not isinstance(v, str):
                raise TypeError(
                    f"ControlFacts.{field} must be None or str, got {type(v).__name__!r}"
                )


def has_any_evidence(facts: ControlFacts) -> bool:
    """True when there is any evidence at all for this control."""
    return (
        facts.configured is not NOT_DETERMINED
        or facts.ledger_rows > 0
        or facts.absence_witnessed
    )


# ---------------------------------------------------------------------------
# 4. State derivation
# ---------------------------------------------------------------------------


def derive_configured(facts: ControlFacts) -> tri:
    return facts.configured


def derive_executed(facts: ControlFacts) -> tri:
    if facts.ledger_rows > 0:
        if facts.ledger_corroborated is True:
            return True
        return NOT_DETERMINED
    # ledger_rows == 0
    if facts.absence_witnessed:
        return False
    return NOT_DETERMINED


def derive_observed(facts: ControlFacts, executed: tri) -> tri:
    if executed is False:
        return False
    if executed is NOT_DETERMINED:
        return NOT_DETERMINED
    # executed is True
    return facts.target_in_payload


def derive_enforceable(executed: tri, actual_event: "str | None", authority: "LifecycleAuthority | None") -> tri:
    """ENFORCEABLE comes only from the lifecycle-authority table.

    Must not read policy decision, cancellation, after-state, or enforced.
    """
    if executed is True:
        return blocking_capable(actual_event, authority)
    return NOT_DETERMINED


def derive_enforced(
    facts: ControlFacts,
    executed: tri,
    observed: tri,
    enforceable: tri,
) -> tri:
    # 1
    if executed is False:
        return False
    # 2
    if executed is NOT_DETERMINED:
        return NOT_DETERMINED
    # 3
    if observed is False:
        return False
    if observed is NOT_DETERMINED:
        return NOT_DETERMINED
    # 4
    if facts.target_exists_after is True:
        return False
    # 5
    if facts.policy_triggered is False:
        return False
    if facts.policy_triggered is NOT_DETERMINED:
        return NOT_DETERMINED
    # 6
    if enforceable is False:
        return False
    if enforceable is NOT_DETERMINED:
        return NOT_DETERMINED
    # 7
    if facts.target_exists_after is NOT_DETERMINED:
        return NOT_DETERMINED
    # 8  (target_exists_after is False here)
    if facts.bob_cancelled_citing_control is True:
        return True
    return NOT_DETERMINED


def derive_states(facts: ControlFacts) -> dict[str, tri]:
    """Derive all five tri-valued states from *facts*."""
    authority = authority_for(facts.bob_version)

    configured = derive_configured(facts)
    executed = derive_executed(facts)
    observed = derive_observed(facts, executed)
    enforceable = derive_enforceable(executed, facts.actual_event, authority)
    enforced = derive_enforced(facts, executed, observed, enforceable)

    states: dict[str, tri] = {
        "configured": configured,
        "executed": executed,
        "observed": observed,
        "enforceable": enforceable,
        "enforced": enforced,
    }

    # Withdraw any state named in a conflict's affects
    for conflict in facts.conflicts:
        for state_name in conflict.affects:
            states[state_name] = NOT_DETERMINED

    return states


# ---------------------------------------------------------------------------
# 5. Results
# ---------------------------------------------------------------------------

CONTROL_ENFORCEMENT_VERIFIED = "CONTROL_ENFORCEMENT_VERIFIED"
CONTROL_OBSERVATIONAL_ONLY = "CONTROL_OBSERVATIONAL_ONLY"
CONTROL_CONFIGURED_NOT_EXECUTED = "CONTROL_CONFIGURED_NOT_EXECUTED"
CONTROL_NOT_CONFIGURED = "CONTROL_NOT_CONFIGURED"
CONTROL_ACTION_NOT_OBSERVED = "CONTROL_ACTION_NOT_OBSERVED"
CONTROL_NOT_ENFORCEABLE = "CONTROL_NOT_ENFORCEABLE"
CONTROL_NOT_DETERMINED = "CONTROL_NOT_DETERMINED"
CONTROL_CONFLICTING_EVIDENCE = "CONTROL_CONFLICTING_EVIDENCE"
EVIDENCE_NOT_VERIFIED = "EVIDENCE_NOT_VERIFIED"
NO_EVIDENCE = "NO_EVIDENCE"

RESULT_LABELS: dict[str, str] = {
    CONTROL_ENFORCEMENT_VERIFIED: "ENFORCEMENT VERIFIED",
    CONTROL_OBSERVATIONAL_ONLY: "OBSERVATIONAL ONLY",
    CONTROL_CONFIGURED_NOT_EXECUTED: "CONFIGURED \u2014 NOT EXECUTED",
    CONTROL_NOT_CONFIGURED: "NOT CONFIGURED",
    CONTROL_ACTION_NOT_OBSERVED: "ACTION NOT OBSERVED",
    CONTROL_NOT_ENFORCEABLE: "NOT ENFORCEABLE",
    CONTROL_NOT_DETERMINED: "NOT DETERMINED",
    CONTROL_CONFLICTING_EVIDENCE: "CONFLICTING EVIDENCE",
    EVIDENCE_NOT_VERIFIED: "EVIDENCE NOT VERIFIED",
    NO_EVIDENCE: "NO EVIDENCE",
}

RESULT_TONE: dict[str, str] = {
    CONTROL_ENFORCEMENT_VERIFIED: "verified",
    CONTROL_OBSERVATIONAL_ONLY: "partial",
    CONTROL_CONFIGURED_NOT_EXECUTED: "failed",
    CONTROL_NOT_CONFIGURED: "failed",
    CONTROL_ACTION_NOT_OBSERVED: "partial",
    CONTROL_NOT_ENFORCEABLE: "partial",
    CONTROL_NOT_DETERMINED: "unknown",
    CONTROL_CONFLICTING_EVIDENCE: "conflict",
    EVIDENCE_NOT_VERIFIED: "unknown",
    NO_EVIDENCE: "unknown",
}

CONFIDENT_RESULTS: frozenset[str] = frozenset(
    {
        CONTROL_ENFORCEMENT_VERIFIED,
        CONTROL_OBSERVATIONAL_ONLY,
        CONTROL_CONFIGURED_NOT_EXECUTED,
        CONTROL_NOT_CONFIGURED,
        CONTROL_ACTION_NOT_OBSERVED,
        CONTROL_NOT_ENFORCEABLE,
    }
)


def _reason(facts: ControlFacts, states: dict[str, tri], result: str) -> str:
    """One plain sentence naming the missing link or the outcome."""
    event = facts.actual_event or facts.configured_event or "an unknown lifecycle event"

    if result == CONTROL_ENFORCEMENT_VERIFIED:
        return (
            f"The control ran at {event}, received the prohibited write, "
            "and Bob cancelled the tool call citing this control."
        )
    if result == CONTROL_OBSERVATIONAL_ONLY:
        return (
            f"The control ran at {event}; the prohibited action had already occurred, "
            "so it could record the violation but could not prevent it."
        )
    if result == CONTROL_CONFIGURED_NOT_EXECUTED:
        return (
            "The control is present in the Bob configuration but produced no execution "
            "evidence in this session, so it inspected nothing and blocked nothing."
        )
    if result == CONTROL_NOT_CONFIGURED:
        return "No hook for this control was found in the applicable Bob configuration."
    if result == CONTROL_ACTION_NOT_OBSERVED:
        return "The control ran but did not receive the governed action in its payload."
    if result == CONTROL_NOT_ENFORCEABLE:
        return (
            f"The control ran at {event}, which cannot prevent a tool call at that "
            "lifecycle point."
        )
    if result == CONTROL_NOT_DETERMINED:
        return "Insufficient evidence to determine the control outcome."
    if result == CONTROL_CONFLICTING_EVIDENCE:
        return "Contradicting artifacts were found; the outcome cannot be resolved."
    if result == EVIDENCE_NOT_VERIFIED:
        return "The evidence bundle has not been verified; the result is withheld."
    if result == NO_EVIDENCE:
        return "No evidence was found for this control in this session."
    return "Outcome unknown."


def classify(facts: ControlFacts, states: dict[str, tri]) -> str:
    """Classify the control outcome. Fail-closed gates first, then a ladder."""
    configured = states["configured"]
    executed = states["executed"]
    observed = states["observed"]
    enforceable = states["enforceable"]
    enforced = states["enforced"]

    # 1. fail-closed gate
    if facts.bundle_verified is not True:
        return EVIDENCE_NOT_VERIFIED

    # 2. no evidence
    if not has_any_evidence(facts):
        return NO_EVIDENCE

    # 3. conflicting evidence
    if facts.conflicts:
        return CONTROL_CONFLICTING_EVIDENCE

    # 4. configured
    if configured is False:
        return CONTROL_NOT_CONFIGURED
    if configured is not True:
        return CONTROL_NOT_DETERMINED

    # 5. executed
    if executed is False:
        return CONTROL_CONFIGURED_NOT_EXECUTED
    if executed is not True:
        return CONTROL_NOT_DETERMINED

    # 6. observed
    if observed is False:
        return CONTROL_ACTION_NOT_OBSERVED
    if observed is not True:
        return CONTROL_NOT_DETERMINED

    # 7. enforcement verified
    if enforceable is True and enforced is True:
        return CONTROL_ENFORCEMENT_VERIFIED

    # 8. enforceable False
    if enforceable is False:
        if facts.policy_triggered is True and facts.target_exists_after is True:
            return CONTROL_OBSERVATIONAL_ONLY
        return CONTROL_NOT_ENFORCEABLE

    # 9. otherwise
    return CONTROL_NOT_DETERMINED


# ---------------------------------------------------------------------------
# 6. Record
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class ControlRecord:
    """The complete evaluated outcome for one control in one session."""

    control_id: str
    session_id: "str | None"
    states: dict[str, tri]
    result: str
    label: str
    tone: str
    confident: bool
    reason: str
    conflicts: tuple[Conflict, ...]
    configured_event: "str | None"
    actual_event: "str | None"
    bob_version: "str | None"
    lifecycle_authority_source: "str | None"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return {
            "control_id": self.control_id,
            "session_id": self.session_id,
            "states": {k: state_to_json(v) for k, v in self.states.items()},
            "result": self.result,
            "label": self.label,
            "tone": self.tone,
            "confident": self.confident,
            "reason": self.reason,
            "conflicts": [
                {
                    "code": c.code,
                    "artifacts": list(c.artifacts),
                    "detail": c.detail,
                }
                for c in self.conflicts
            ],
            "configured_event": self.configured_event,
            "actual_event": self.actual_event,
            "bob_version": self.bob_version,
            "lifecycle_authority_source": self.lifecycle_authority_source,
        }


def evaluate(facts: ControlFacts) -> ControlRecord:
    """Run derive_states then classify and return a ControlRecord."""
    states = derive_states(facts)
    result = classify(facts, states)
    authority = authority_for(facts.bob_version)
    return ControlRecord(
        control_id=facts.control_id,
        session_id=facts.session_id,
        states=states,
        result=result,
        label=RESULT_LABELS[result],
        tone=RESULT_TONE[result],
        confident=result in CONFIDENT_RESULTS,
        reason=_reason(facts, states, result),
        conflicts=facts.conflicts,
        configured_event=facts.configured_event,
        actual_event=facts.actual_event,
        bob_version=facts.bob_version,
        lifecycle_authority_source=authority.source if authority is not None else None,
    )
