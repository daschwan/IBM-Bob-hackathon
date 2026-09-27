"""ControlProof judge page renderer — B4.

Render a self-contained HTML5 page from a receipt dict.
Python 3.12, standard library only. No JavaScript, no external resources.
"""
from __future__ import annotations

import html as _html
from typing import Any


def _e(value: Any) -> str:
    """HTML-escape a value (convert to str first)."""
    return _html.escape(str(value) if value is not None else "")


_STATE_KEYS = ("configured", "executed", "observed", "enforceable", "enforced")
_STATE_LABELS = ("Configured", "Executed", "Observed", "Can enforce", "Enforced")

_CARD_SENTENCES: dict[str, str] = {
    "CONTROL_ENFORCEMENT_VERIFIED": (
        "The control ran before the write and IBM Bob cancelled it."
    ),
    "CONTROL_OBSERVATIONAL_ONLY": (
        "The control detected the violation after the protected action had already occurred."
    ),
    "CONTROL_CONFIGURED_NOT_EXECUTED": (
        "The guardrail existed in configuration but never executed."
    ),
    "CONTROL_CONFLICTING_EVIDENCE": (
        "Evidence artifacts contradict each other, so no outcome is asserted."
    ),
    "EVIDENCE_NOT_VERIFIED": (
        "The evidence bundle does not match its recorded manifest, so no outcome is asserted."
    ),
}

# Map tone → CSS class background colour
_TONE_COLORS: dict[str, str] = {
    "verified": "#d4edda",
    "partial": "#fff3cd",
    "failed": "#f8d7da",
    "conflict": "#e2d9f3",
    "unknown": "#e2e3e5",
}


def _state_symbol(val: Any) -> str:
    if val is True:
        return "\u2713"
    if val is False:
        return "\u2715"
    return "\u2014"


def render_html(receipt: dict) -> str:
    """Render a self-contained HTML5 judge page from *receipt*."""
    run = receipt.get("run", {})
    bundle = receipt.get("bundle", {})
    bundle_verified = bundle.get("verified", False)
    policy_text = run.get("policy_text") or ""

    parts: list[str] = []

    parts.append(
        "<!DOCTYPE html>"
        "<html lang=\"en\">"
        "<head>"
        "<meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>ControlProof</title>"
        "<style>"
        "body{font-family:-apple-system,'Segoe UI',system-ui,sans-serif;margin:0;padding:2.5rem;"
        "background:#f7f8fa;color:#1f2328;line-height:1.55;max-width:1400px;margin-left:auto;margin-right:auto;}"
        "h1{font-size:2.5rem;letter-spacing:.05em;margin:0 0 .25rem;}"
        ".tagline{color:#57606a;font-size:1.15rem;margin-bottom:1rem;}"
        ".policy{font-size:.95rem;color:#1f2328;background:#fff;border:1px solid #e5e7eb;"
        "border-radius:6px;padding:.7rem 1rem;margin-bottom:1.25rem;}"
        ".banner{background:#f8d7da;border:1px solid #f5c2c7;border-radius:4px;"
        "padding:.75rem 1rem;margin-bottom:1.5rem;font-weight:bold;}"
        ".banner-problems{margin:.25rem 0 0 1rem;font-weight:normal;font-size:.9rem;}"
        ".bundle-ok{color:#198754;margin-bottom:1.5rem;font-size:.95rem;}"
        ".cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1.25rem;margin-bottom:2rem;}"
        ".card{min-width:0;border-radius:10px;border:1px solid #e5e7eb;padding:1.5rem;background:#fff;}"
        ".card-title{font-weight:700;font-size:1.05rem;margin-bottom:.9rem;}"
        ".state-table{width:100%;border-collapse:collapse;font-size:.95rem;margin-bottom:1rem;}"
        ".state-table td{padding:.25rem .35rem;}"
        ".state-table td:first-child{color:#57606a;}"
        ".state-table td:last-child{font-weight:700;text-align:center;width:2rem;}"
        ".result-label{font-weight:750;font-size:1rem;margin-bottom:.4rem;}"
        ".card-sentence{font-size:.92rem;margin-bottom:.65rem;min-height:3rem;}"
        ".conflict-codes{font-size:.8rem;color:#57606a;}"
        "details{margin-top:.65rem;font-size:.85rem;}"
        "summary{cursor:pointer;color:#2563eb;font-weight:500;}"
        ".ev-table{width:100%;table-layout:fixed;border-collapse:collapse;margin-top:.65rem;}"
        ".ev-table td{padding:.3rem .35rem;vertical-align:top;border-top:1px solid rgba(0,0,0,.09);"
        "overflow-wrap:anywhere;word-break:break-word;}"
        ".ev-table td:first-child{color:#57606a;width:36%;white-space:normal;}"
        ".footer{margin-top:2rem;padding-top:1rem;border-top:1px solid #e5e7eb;"
        "text-align:center;font-size:.8rem;color:#57606a;}"
        "@media (max-width:900px){body{padding:1.25rem;}.cards{grid-template-columns:1fr;}h1{font-size:2rem;}}"
        "</style>"
        "</head>"
        "<body>"
    )

    # Header
    parts.append(
        "<h1>CONTROLPROOF</h1>"
        "<div class=\"tagline\">Same policy. Same action. Three different realities.</div>"
        f"<div class=\"policy\">{_e(policy_text)}</div>"
    )

    # Bundle line
    if not bundle_verified:
        parts.append("<div class=\"banner\">EVIDENCE NOT VERIFIED — the bundle does not match its recorded manifest. No outcome is shown.</div>")
        problems = bundle.get("problems", [])
        if problems:
            parts.append("<ul class=\"banner-problems\">")
            for p in problems:
                parts.append(f"<li>{_e(p)}</li>")
            parts.append("</ul>")
    else:
        parts.append("<div class=\"bundle-ok\">&#10003; Evidence bundle matches its recorded manifest.</div>")

    # Cards
    parts.append("<div class=\"cards\">")
    for ctrl in receipt.get("controls", []):
        title = ctrl.get("title", ctrl.get("control_id", ""))
        result = ctrl.get("result", "")
        label = ctrl.get("label", "")
        tone = ctrl.get("tone", "unknown")
        reason = ctrl.get("reason", "")
        states = ctrl.get("states", {})
        conflicts = ctrl.get("conflicts", [])
        evidence = ctrl.get("evidence", {})

        bg_color = _TONE_COLORS.get(tone, "#e2e3e5")
        parts.append(f"<div class=\"card\" style=\"background:{bg_color}\">")
        parts.append(f"<div class=\"card-title\">{_e(title)}</div>")

        # State table
        parts.append("<table class=\"state-table\">")
        for lbl, key in zip(_STATE_LABELS, _STATE_KEYS):
            val = states.get(key)
            sym = _state_symbol(val)
            parts.append(f"<tr><td>{_e(lbl)}</td><td>{_e(sym)}</td></tr>")
        parts.append("</table>")

        parts.append(f"<div class=\"result-label\">{_e(label)}</div>")

        # Card sentence
        if result in _CARD_SENTENCES:
            sentence = _CARD_SENTENCES[result]
            parts.append(f"<div class=\"card-sentence\">{_e(sentence)}</div>")
            # For CONFLICTING_EVIDENCE: also list conflict codes
            if result == "CONTROL_CONFLICTING_EVIDENCE" and conflicts:
                codes = ", ".join(_e(c.get("code", "")) for c in conflicts)
                parts.append(f"<div class=\"conflict-codes\">{codes}</div>")
        else:
            parts.append(f"<div class=\"card-sentence\">{_e(reason)}</div>")

        # Evidence details
        session_id = ctrl.get("session_id")
        bob_version = ctrl.get("bob_version")
        configured_event = ctrl.get("configured_event")
        actual_event = ctrl.get("actual_event")
        lifecycle_authority_source = ctrl.get("lifecycle_authority_source")

        ledger_rows = evidence.get("ledger_rows", 0)
        excluded_rows = evidence.get("excluded_rows", {})
        bob_cancel_text = evidence.get("bob_cancellation_text")
        pf_after = evidence.get("protected_files_after", [])
        conflict_meanings = evidence.get("conflict_meanings", {})
        notes = evidence.get("notes", [])

        parts.append("<details><summary>View Evidence</summary>")
        parts.append("<table class=\"ev-table\">")

        def _row(lbl: str, val: Any) -> str:
            return f"<tr><td>{_e(lbl)}</td><td>{_e(val)}</td></tr>"

        parts.append(_row("Session ID", session_id or "—"))
        parts.append(_row("Bob version", bob_version or "—"))
        parts.append(_row("Configured event", configured_event or "—"))
        parts.append(_row("Actual event", actual_event or "—"))
        parts.append(_row("Ledger rows", ledger_rows))

        excl_str = ", ".join(f"{k}: {v}" for k, v in excluded_rows.items()) if excluded_rows else "—"
        parts.append(_row("Excluded rows", excl_str))

        parts.append(_row("Bob cancellation text", bob_cancel_text or "—"))

        pf_str = ", ".join(pf_after) if pf_after else "—"
        parts.append(_row("Protected files after", pf_str))

        if conflict_meanings:
            for code, meaning in conflict_meanings.items():
                parts.append(_row(f"Conflict: {code}", meaning))
        else:
            parts.append(_row("Conflicts", "—"))

        parts.append(_row("Lifecycle authority source", lifecycle_authority_source or "—"))

        notes_str = "; ".join(notes) if notes else "—"
        parts.append(_row("Notes", notes_str))

        parts.append(_row("Reason", reason))

        parts.append("</table>")
        parts.append("</details>")

        parts.append("</div>")  # card

    parts.append("</div>")  # cards

    parts.append(
        "<div class=\"footer\">Made with IBM Bob</div>"
        "</body>"
        "</html>"
    )

    return "".join(parts)
