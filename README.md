# Claims Workflow Review

A full-stack sandbox for reviewing **synthetic claim-denial packets**. Operators queue a packet, a worker extracts the denial reason and checks its evidence, and reviewers approve the current packet for internal processing or return it for rework.

**The key feature is an evidence gate.** A model may propose a reason and a quote, but the API requires a verbatim quote with a supported reason marker and derives the evidence checklist itself. A reviewer cannot approve a missing document or accidentally approve a stale revision. The app stores the decision; it does not contact a payer, determine coverage, or submit an appeal.

## Try it locally

```sh
export CLAIMS_DEMO_ACCESS_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(24))')"
docker compose up --build -d
```

1. Open [the workbench](http://localhost:8000) and sign in as **Operator** with that local key.
2. Load **Blocked example**, queue it, then switch to **Reviewer**. The authorization record is missing, so **Approve packet** is disabled.
3. Return it with a note. Switch back to **Operator**, open the returned packet and add a fictional authorization record. Resubmit to create revision two.
4. As **Reviewer**, approve the second revision and inspect the audit history.

The API schema is at [localhost:8000/docs](http://localhost:8000/docs). The same flow can be run from a checkout with `pip install -r requirements.txt` and `python -m demo.run` while the services are running.

Without Docker, install dependencies, build the frontend with `(cd web && npm ci && npm run build)`, export `CLAIMS_DEMO_MODE=1` and the key above, then run `python -m uvicorn claims.api:app --no-access-log` and `python -m claims.worker` in separate terminals. Both must use the same working directory or absolute `CLAIMS_DB` path.

## Stack and decisions

- **React + TypeScript:** intake, review queue, source evidence, missing-document indicators, revision forms, and audit history.
- **Python + FastAPI + Pydantic:** REST endpoints with validated request bodies, operator/reviewer authorization, and duplicate-intake protection.
- **SQLite:** durable worker jobs, bounded retries, expiring leases and revision-aware state transitions.
- **Structured LLM adapter:** local rules run by default. With `CLAIMS_PROVIDER=openai`, `OPENAI_API_KEY`, and `OPENAI_MODEL` configured on the worker, an optional [Responses API adapter](https://platform.openai.com/docs/guides/structured-outputs) can process only the exact two bundled synthetic denial notices. Other input stays local. A provider failure or fabricated quote falls back to rules.
- **Auth:** local demo tokens expire after 25 minutes. For API-only OAuth2 resource-server mode, set `CLAIMS_ISSUER`, `CLAIMS_AUDIENCE`, and `CLAIMS_JWKS_URL`; the API verifies RS256, issuer, audience, expiration, and an assigned operator or reviewer role. The local demo token endpoint is disabled in this mode.
- **Delivery:** Docker Compose separates the API and worker. GitHub Actions runs backend regressions and the TypeScript build. [AGENTS.md](AGENTS.md) defines boundaries for AI-assisted changes and [the design notes](docs/design.md) explain the review and retry rules.

Run `python -m unittest discover -s tests -v` and `(cd web && npm ci && npm run build)`. The default demo needs no AI provider key. The external provider and identity integration have local mocked tests; the demo is not connected to real healthcare systems, an identity tenant, or cloud infrastructure.
