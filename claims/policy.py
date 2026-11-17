"""Fictional administrative rules. This module never submits a claim."""
from .schemas import Draft

POLICY_VERSION = "synthetic-checklist-v1"
CHECKLIST = {
    "missing_receipt": ["submission_receipt"],
    "missing_auth_record": ["authorization_record"],
    "identifier_mismatch": ["cover_sheet", "submission_receipt"],
    "unknown": [],
}
MARKERS = {
    "missing_receipt": "submission receipt not found",
    "missing_auth_record": "authorization record not attached",
    "identifier_mismatch": "identifier does not match",
}


def rule_draft(notice: str) -> Draft:
    matches = [(reason, position, len(phrase)) for reason, phrase in MARKERS.items()
               if (position := notice.lower().find(phrase)) >= 0]
    if len(matches) != 1:
        return Draft(reason="unknown", quote="No supported reason found")
    reason, start, length = matches[0]
    return Draft(reason=reason, quote=notice[start:start + length])


def check(payload: dict, proposed: Draft, source: str) -> dict:
    docs = {d["kind"]: d["text"] for d in payload["documents"]}
    notice = docs["denial_notice"]
    known = rule_draft(notice)
    verified = (proposed.reason != "unknown" and proposed.reason == known.reason
                and proposed.quote in notice and known.quote in proposed.quote)
    reason = proposed.reason if verified else "unknown"
    required = CHECKLIST[reason]
    missing = [kind for kind in required if kind not in docs]
    return {"policy": POLICY_VERSION, "reason": reason, "provider": source,
            "evidence": {"document": "denial_notice", "quote": proposed.quote} if verified else None,
            "required": required, "missing": missing, "eligible_for_approval": verified and not missing}
