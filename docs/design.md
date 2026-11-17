# Workflow and trust boundaries

| State | Writer | Rule |
| --- | --- | --- |
| Queued | Operator | Idempotent intake, or rework of an existing returned revision. |
| Needs review | Worker | A lease holder persists the verified draft, or the worker exhausts three attempts and records that no draft is available. |
| Returned | Reviewer | The reviewer can return a packet with a note, including when no draft was produced. |
| Approved | Reviewer | Only the current revision with a verified reason and all required synthetic document types can be approved. Approval means readiness for the demo's internal queue; there is no payer action. |

Intake stores the packet snapshot and worker job in one transaction. The worker leases a job, releases the transaction, drafts outside the lock, then commits only with the matching lease token. Expired leases can be reclaimed; decisions are guarded separately by the current case state and revision. An operator can read their own cases, and a reviewer can read the local team's queue. API responses are not cacheable. API logs record request IDs, route names, status codes and durations; worker logs record generated case IDs, attempts and durations. Neither prints document bodies or bearer tokens. The database audit records actor, revision, action and any explicit human note.

The structured model receives only an exact bundled notice. The model cannot set policy, change cases, or call tools. A candidate must contain a verbatim source quote with a supported reason marker; server code then derives required evidence from a versioned rule table. An unrecognized or ambiguous notice cannot be approved. The model path is a sandbox for testing structured extraction; it does not extend the rule vocabulary. Document type presence is the only attachment check in this prototype; the reviewer evaluates document contents.

The checked-in Compose stack is a local demo. It shares a SQLite volume between the API and worker on a single host. For an external IdP, the API can validate OAuth2 access tokens via a configured HTTPS JWKS endpoint; this repo does not provide a browser OAuth login flow or SAML integration. A real deployment would need organizational authorization, data governance, protected database storage, operational monitoring and a retention policy.
