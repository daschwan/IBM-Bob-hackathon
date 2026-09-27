#!/usr/bin/env python3
"""ControlProof CLI — B4.

Subcommands: provision | capture | pin | demo
Python 3.12, standard library only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _repo_root() -> Path:
    """Return the repository root (the directory containing this file)."""
    return Path(__file__).resolve().parent


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(prog="controlproof", description="ControlProof CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # provision
    p_prov = sub.add_parser("provision", help="Set up three demo workspaces")
    p_prov.add_argument("--run-dir", required=True, metavar="DIR")

    # capture
    p_cap = sub.add_parser("capture", help="Assemble an evidence bundle")
    p_cap.add_argument("--run-dir", required=True, metavar="DIR")
    p_cap.add_argument("--bundle", required=True, metavar="DIR")
    p_cap.add_argument("--bob-db", default=None, metavar="PATH")
    p_cap.add_argument("--bob-product-json", default=None, metavar="PATH")
    p_cap.add_argument(
        "--session", action="append", metavar="WORKSPACE=ID", default=[],
        help="Override session id for a workspace"
    )

    # pin
    p_pin = sub.add_parser("pin", help="Record a bundle's manifest sha256")
    p_pin.add_argument("--bundle", required=True, metavar="DIR")
    p_pin.add_argument("--default", action="store_true")

    # demo
    p_demo = sub.add_parser("demo", help="Render the judge page")
    p_demo.add_argument("--bundle", default=None, metavar="DIR")
    p_demo.add_argument("--out", default=None, metavar="DIR")
    p_demo.add_argument("--no-pin", action="store_true")

    args = parser.parse_args()

    repo_root = _repo_root()

    if args.command == "provision":
        _cmd_provision(args, repo_root)
    elif args.command == "capture":
        _cmd_capture(args, repo_root)
    elif args.command == "pin":
        _cmd_pin(args, repo_root)
    elif args.command == "demo":
        _cmd_demo(args, repo_root)


# ---------------------------------------------------------------------------
# provision
# ---------------------------------------------------------------------------


def _cmd_provision(args: argparse.Namespace, repo_root: Path) -> None:
    from controlproof.capture import provision, DEMO_PROMPT

    run_dir = Path(args.run_dir)
    draft = provision(run_dir, repo_root)

    run_nonce = draft["run_nonce"]
    print(f"Run nonce: {run_nonce}")

    for ctrl in draft["controls"]:
        print(f"  {ctrl['workspace']}: {ctrl['workspace_path']}")

    print(f"\nPrompt:\n{DEMO_PROMPT}")
    print(
        "\nReminder: set logging.enableFileLogging to true and "
        f"logging.logDir to {run_dir / 'boblogs'} "
        "in IBM Bob's settings before starting the sessions."
    )


# ---------------------------------------------------------------------------
# capture
# ---------------------------------------------------------------------------


def _cmd_capture(args: argparse.Namespace, repo_root: Path) -> None:
    from controlproof.capture import capture

    run_dir = Path(args.run_dir)
    bundle_dir = Path(args.bundle)

    # Defaults
    bob_db = args.bob_db
    if bob_db is None:
        bob_db = Path.home() / ".bob" / "db" / "bob.db"
    else:
        bob_db = Path(bob_db)

    bob_product_json = args.bob_product_json
    if bob_product_json is None:
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        bob_product_json = Path(local_app_data) / "Programs" / "IBM Bob" / "resources" / "app" / "product.json"
    else:
        bob_product_json = Path(bob_product_json)

    # Parse --session WORKSPACE=ID
    sessions: "dict[str, str] | None" = None
    if args.session:
        sessions = {}
        for s in args.session:
            if "=" not in s:
                print(f"Error: --session must be in WORKSPACE=ID form, got: {s!r}", file=sys.stderr)
                sys.exit(1)
            ws, sid = s.split("=", 1)
            sessions[ws] = sid

    manifest_sha = capture(
        run_dir=run_dir,
        bundle_dir=bundle_dir,
        repo_root=repo_root,
        bob_db=bob_db,
        bob_product_json=bob_product_json,
        sessions=sessions,
    )
    print(f"Manifest sha256: {manifest_sha}")


# ---------------------------------------------------------------------------
# pin
# ---------------------------------------------------------------------------


def _cmd_pin(args: argparse.Namespace, repo_root: Path) -> None:
    from controlproof.bundle import verify_bundle

    bundle_dir = Path(args.bundle).resolve()
    check = verify_bundle(bundle_dir)
    if check.manifest_sha256 is None:
        print("Error: could not read manifest sha256 from bundle.", file=sys.stderr)
        sys.exit(1)

    manifest_sha = check.manifest_sha256

    # Compute bundle path relative to repo root
    try:
        rel_path = bundle_dir.relative_to(repo_root).as_posix()
    except ValueError:
        # Not under repo root: use absolute posix path
        rel_path = bundle_dir.as_posix()

    pins_path = repo_root / "evidence" / "PINS.json"
    pins_path.parent.mkdir(parents=True, exist_ok=True)

    if pins_path.exists():
        try:
            pins = json.loads(pins_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pins = {"default": None, "bundles": {}}
    else:
        pins = {"default": None, "bundles": {}}

    if not isinstance(pins.get("bundles"), dict):
        pins["bundles"] = {}

    pins["bundles"][rel_path] = manifest_sha
    if args.default:
        pins["default"] = rel_path

    data = (json.dumps(pins, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    pins_path.write_bytes(data)
    print(f"Pinned {rel_path} → {manifest_sha}")


# ---------------------------------------------------------------------------
# demo
# ---------------------------------------------------------------------------


def _cmd_demo(args: argparse.Namespace, repo_root: Path) -> None:
    from controlproof.bundle import load_pins, verify_bundle
    from controlproof.receipt import build_receipt, receipt_text
    from controlproof.render import render_html

    # Resolve bundle dir
    if args.bundle:
        bundle_dir = Path(args.bundle)
    else:
        pins = load_pins(repo_root)
        default = pins.get("default")
        if not default:
            print(
                "Error: no --bundle given and PINS.json has no default.",
                file=sys.stderr,
            )
            sys.exit(1)
        bundle_dir = repo_root / default

    # Resolve pin
    pinned_sha: "str | None" = None
    if not args.no_pin:
        pins = load_pins(repo_root)
        try:
            rel = Path(bundle_dir).resolve().relative_to(repo_root).as_posix()
        except ValueError:
            rel = str(bundle_dir)
        pinned_sha = pins.get("bundles", {}).get(rel)

    receipt = build_receipt(bundle_dir, pinned_sha)

    # Output dir
    out_dir = Path(args.out) if args.out else repo_root / "docs"
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "index.html").write_text(render_html(receipt), encoding="utf-8")
    receipt_json = json.dumps(receipt, indent=2, ensure_ascii=False) + "\n"
    (out_dir / "receipt.json").write_text(receipt_json, encoding="utf-8")
    (out_dir / "receipt.txt").write_text(receipt_text(receipt), encoding="utf-8")

    print(receipt_text(receipt))
    sys.exit(0)


if __name__ == "__main__":
    main()
