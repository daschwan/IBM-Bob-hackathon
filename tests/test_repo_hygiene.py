"""Repository-level checks that hold for every task.

Written with Claude Code during event scaffolding (B0); product tests live beside it.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Claims ControlProof must never make about itself. The task specs and the tests are exempt:
# they have to name the words in order to forbid them.
BANNED_CLAIMS = [
    r"mathematically proven",
    r"\bproven\b",
    r"\bsecure\b",
    r"\bguaranteed?\b",
    r"tamper[- ]?proof",
    r"impossible to bypass",
    r"continuous assurance",
    r"\bcertified\b",
    r"security certification",
]

SCANNED_SUFFIXES = {".py", ".md", ".html", ".txt", ".json", ".js", ".css"}
EXEMPT_DIRS = {"bob-tasks", "tests", ".git", "bob_sessions", "__pycache__", ".pytest_cache"}


def _scanned_files():
    for path in ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SCANNED_SUFFIXES:
            continue
        if EXEMPT_DIRS.intersection(path.relative_to(ROOT).parts):
            continue
        yield path


def test_license_is_mit():
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")


def test_bob_sessions_folder_exists():
    assert (ROOT / "bob_sessions").is_dir()


def test_no_banned_claims():
    hits = []
    for path in _scanned_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in BANNED_CLAIMS:
            for m in re.finditer(pattern, text, flags=re.IGNORECASE):
                line = text.count("\n", 0, m.start()) + 1
                hits.append(f"{path.relative_to(ROOT)}:{line}: {m.group(0)!r}")
    assert not hits, "banned claim wording:\n" + "\n".join(hits)
