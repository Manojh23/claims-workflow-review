READY = {
    "claim_ref": "DEMO-READY",
    "documents": [
        {"kind": "denial_notice", "text": "Synthetic payer notice: submission receipt not found."},
        {"kind": "submission_receipt", "text": "Synthetic export: received under tracking EXAMPLE-102."},
    ],
}
BLOCKED = {
    "claim_ref": "DEMO-BLOCKED",
    "documents": [
        {"kind": "denial_notice", "text": "Synthetic payer notice: authorization record not attached."},
        {"kind": "cover_sheet", "text": "Synthetic administrative cover sheet."},
    ],
}
PROVIDER_NOTICE_ALLOWLIST = frozenset(
    doc["text"] for fixture in (READY, BLOCKED) for doc in fixture["documents"] if doc["kind"] == "denial_notice"
)
