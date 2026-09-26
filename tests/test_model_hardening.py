"""Model hardening tests — task B2."""

from __future__ import annotations

import pytest

from controlproof.model import (
    NOT_DETERMINED,
    Conflict,
    ControlFacts,
    state_symbol,
    state_to_json,
)


# ---------------------------------------------------------------------------
# 23. test_rejects_non_tri_values
# ---------------------------------------------------------------------------


def test_rejects_non_tri_values():
    # configured=None, =1, ="true" each raise TypeError
    with pytest.raises(TypeError):
        ControlFacts(control_id="X", configured=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ControlFacts(control_id="X", configured=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ControlFacts(control_id="X", configured="true")  # type: ignore[arg-type]

    # likewise for target_exists_after
    with pytest.raises(TypeError):
        ControlFacts(control_id="X", target_exists_after=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ControlFacts(control_id="X", target_exists_after=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ControlFacts(control_id="X", target_exists_after="true")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 24. test_rejects_non_bool_flags
# ---------------------------------------------------------------------------


def test_rejects_non_bool_flags():
    with pytest.raises(TypeError):
        ControlFacts(control_id="X", bundle_verified="false")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ControlFacts(control_id="X", bundle_verified=1)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ControlFacts(control_id="X", absence_witnessed=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 25. test_rejects_bad_ledger_rows_and_conflicts
# ---------------------------------------------------------------------------


def test_rejects_bad_ledger_rows_and_conflicts():
    # ledger_rows=True is a bool (subclass of int) — should be rejected
    with pytest.raises(TypeError):
        ControlFacts(control_id="X", ledger_rows=True)  # type: ignore[arg-type]
    # ledger_rows=-1
    with pytest.raises((TypeError, ValueError)):
        ControlFacts(control_id="X", ledger_rows=-1)
    # conflicts=["x"] — list with non-Conflict items
    with pytest.raises(TypeError):
        ControlFacts(control_id="X", conflicts=["x"])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 26. test_state_rendering_is_strict
# ---------------------------------------------------------------------------


def test_state_rendering_is_strict():
    with pytest.raises(TypeError):
        state_symbol(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        state_to_json(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        state_to_json("yes")  # type: ignore[arg-type]
