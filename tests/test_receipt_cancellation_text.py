"""Acceptance tests for B6 — show Bob 2.2.0 block text in the receipt.

Tests copy fixtures to pytest's tmp_path before operating on them.
The receipt may not be fully verified (bundle verification may fail on the
unbundled copy); tests only read controls[i]["evidence"]["bob_cancellation_text"].
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from controlproof.receipt import build_receipt

FIXTURE_21 = Path(__file__).parent / "fixtures" / "run-3arm"
FIXTURE_22 = Path(__file__).parent / "fixtures" / "run-3arm-bob22"

# The blocked message id in the PRE task
BLOCKED_MSG_ID = "3f9a1c7emsg05"
PRE_TASK_FILE = "3f9a1c7e5b2d4f60a8e1c3b5d7f90a12.json"


def copy_fixture(fixture: Path, tmp_path: Path) -> Path:
    dst = tmp_path / fixture.name
    shutil.copytree(str(fixture), str(dst))
    return dst


def patch_blocked_msg_content(run_dir: Path, new_content: str) -> None:
    """Set the content field of the blocked message in the PRE task."""
    p = run_dir / "bob_tasks" / PRE_TASK_FILE
    task = json.loads(p.read_text(encoding="utf-8"))
    for msg in task["messages"]:
        if msg.get("id") == BLOCKED_MSG_ID:
            msg["data"]["content"] = new_content
            break
    p.write_text(json.dumps(task, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. test_bob22_block_text_shown
# ---------------------------------------------------------------------------


def test_bob22_block_text_shown(tmp_path):
    """Bob 2.2.0 fixture: PRE cancellation text starts with the 2.2 prefix;
    POST and BADCFG have no cancellation text."""
    run_dir = copy_fixture(FIXTURE_22, tmp_path)
    receipt = build_receipt(run_dir)

    controls = receipt["controls"]
    pre_text = controls[0]["evidence"]["bob_cancellation_text"]
    post_text = controls[1]["evidence"]["bob_cancellation_text"]
    badcfg_text = controls[2]["evidence"]["bob_cancellation_text"]

    assert pre_text is not None
    assert pre_text.startswith("ControlProof CP-001-PRE denied this write: ")
    assert post_text is None
    assert badcfg_text is None


# ---------------------------------------------------------------------------
# 2. test_bob21_block_text_still_shown
# ---------------------------------------------------------------------------


def test_bob21_block_text_still_shown(tmp_path):
    """Bob 2.1.0 fixture: PRE cancellation text starts with the classic prefix."""
    run_dir = copy_fixture(FIXTURE_21, tmp_path)
    receipt = build_receipt(run_dir)

    pre_text = receipt["controls"][0]["evidence"]["bob_cancellation_text"]
    assert pre_text is not None
    assert pre_text.startswith("Tool call to write_file was cancelled: ")


# ---------------------------------------------------------------------------
# 3. test_ordinary_error_not_shown
# ---------------------------------------------------------------------------


def test_ordinary_error_not_shown(tmp_path):
    """Ordinary EACCES error (isError still true) should not populate cancellation text."""
    run_dir = copy_fixture(FIXTURE_22, tmp_path)
    patch_blocked_msg_content(
        run_dir, "Failed to write file protected/test.txt: EACCES"
    )
    receipt = build_receipt(run_dir)

    pre_text = receipt["controls"][0]["evidence"]["bob_cancellation_text"]
    assert pre_text is None
