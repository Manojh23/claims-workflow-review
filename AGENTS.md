# Engineering instructions

- Read the [workflow and trust boundaries](docs/design.md) before changing a state transition or model adapter.
- Use synthetic fixtures. Never add actual health, payment, identity or claims data. Never commit environment settings or keys.
- Keep transport and authorization in `claims/api.py` and `claims/auth.py`, SQL transitions in `claims/store.py`, and extraction and evidence rules in `claims/provider.py` and `claims/policy.py`. Preserve the worker's lease and revision checks.
- An LLM result is untrusted. Do not add an automated approve, submit, payer integration or payment path. Keep the remote model input allowlist in place.
- For parallel work, split changes along these boundaries: UI and API contract, worker and evidence checks, tests and CI. If the API shape changes, update both the client and contract together.
- Run `python -m unittest discover -s tests -v` and `cd web && npm ci && npm run build` before requesting review. For changes to transitions, also run the HTTP demo.
