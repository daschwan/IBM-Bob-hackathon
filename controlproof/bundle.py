"""ControlProof bundle — B4.

Build, write and verify a MANIFEST.json for an evidence bundle directory.
Python 3.12, standard library only.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path


# ---------------------------------------------------------------------------
# 1. Manifest helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Return the lowercase hex sha256 of *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(bundle_dir: "str | Path") -> dict:
    """Return a manifest dict for all files in *bundle_dir* except MANIFEST.json."""
    bundle_dir = Path(bundle_dir)
    files: dict[str, str] = {}
    for p in sorted(bundle_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(bundle_dir).as_posix()
        if rel == "MANIFEST.json":
            continue
        files[rel] = _sha256_file(p)
    return {"schema": "controlproof.manifest/1", "files": files}


def write_manifest(bundle_dir: "str | Path") -> str:
    """Write MANIFEST.json and return the sha256 of the written bytes."""
    bundle_dir = Path(bundle_dir)
    manifest = build_manifest(bundle_dir)
    data = (json.dumps(manifest, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    # Ensure \n line endings (json.dumps uses \n already on all platforms,
    # but we normalise just in case)
    data = data.replace(b"\r\n", b"\n")
    manifest_path = bundle_dir / "MANIFEST.json"
    manifest_path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


# ---------------------------------------------------------------------------
# 2. Verification
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class BundleCheck:
    ok: bool
    problems: tuple[str, ...]
    manifest_sha256: "str | None"


def verify_bundle(
    bundle_dir: "str | Path",
    pinned_manifest_sha256: "str | None" = None,
) -> BundleCheck:
    """Verify the bundle at *bundle_dir* against its MANIFEST.json."""
    bundle_dir = Path(bundle_dir)
    manifest_path = bundle_dir / "MANIFEST.json"
    problems: list[str] = []
    manifest_sha256: "str | None" = None

    # Check MANIFEST.json present and parseable
    if not manifest_path.exists():
        problems.append("MANIFEST.json is missing.")
        return BundleCheck(ok=False, problems=tuple(problems), manifest_sha256=None)

    raw = manifest_path.read_bytes()
    manifest_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        manifest = json.loads(raw)
        listed: dict[str, str] = manifest.get("files", {})
    except (json.JSONDecodeError, AttributeError):
        problems.append("MANIFEST.json is not parseable JSON.")
        return BundleCheck(ok=False, problems=tuple(problems), manifest_sha256=manifest_sha256)

    # Check every listed file exists and has correct hash
    for rel, expected_sha in listed.items():
        p = bundle_dir / rel
        if not p.exists():
            problems.append(f"Listed file missing: {rel}")
        else:
            actual = _sha256_file(p)
            if actual != expected_sha:
                problems.append(f"SHA-256 mismatch: {rel}")

    # Check no unlisted files (excluding MANIFEST.json itself)
    for p in sorted(bundle_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(bundle_dir).as_posix()
        if rel == "MANIFEST.json":
            continue
        if rel not in listed:
            problems.append(f"Unlisted file present: {rel}")

    # RUN.json must be listed
    if "RUN.json" not in listed:
        problems.append("RUN.json is not listed in the manifest.")

    # hook digest consistency
    hook_rel = "hook/controlproof_hook.py"
    bundled_hook = bundle_dir / hook_rel
    if bundled_hook.exists() and hook_rel in listed:
        try:
            run_data = json.loads((bundle_dir / "RUN.json").read_bytes())
            hook_info = run_data.get("hook")
            if isinstance(hook_info, dict):
                declared_sha = hook_info.get("sha256")
                if isinstance(declared_sha, str):
                    actual_hook_sha = _sha256_file(bundled_hook)
                    if declared_sha != actual_hook_sha:
                        problems.append(
                            "RUN.json hook.sha256 differs from the sha256 of "
                            "hook/controlproof_hook.py in the bundle."
                        )
        except (json.JSONDecodeError, OSError):
            pass

    # Pin check
    if pinned_manifest_sha256 is not None:
        if manifest_sha256 != pinned_manifest_sha256:
            problems.append(
                f"Manifest sha256 {manifest_sha256!r} differs from pinned value "
                f"{pinned_manifest_sha256!r}."
            )

    ok = len(problems) == 0
    return BundleCheck(ok=ok, problems=tuple(problems), manifest_sha256=manifest_sha256)


# ---------------------------------------------------------------------------
# 3. Pins
# ---------------------------------------------------------------------------


def load_pins(repo_root: "str | Path") -> dict:
    """Load evidence/PINS.json; return default structure if absent."""
    pins_path = Path(repo_root) / "evidence" / "PINS.json"
    if not pins_path.exists():
        return {"default": None, "bundles": {}}
    try:
        obj = json.loads(pins_path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return {"default": None, "bundles": {}}
        return {
            "default": obj.get("default"),
            "bundles": obj.get("bundles", {}),
        }
    except (json.JSONDecodeError, OSError):
        return {"default": None, "bundles": {}}
