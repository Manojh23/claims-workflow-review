import json
import os
import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.testclient import TestClient

from claims.api import create_app
from claims.auth import Auth
from claims.fixtures import BLOCKED, READY
from claims.provider import draft_packet, propose_remote
from claims.schemas import Draft
from claims.store import Store
from claims.worker import process_one

KEY = "local-demo-unit-test-only-access"
PROVIDER = {"CLAIMS_PROVIDER": "openai", "OPENAI_API_KEY": "unit-test", "OPENAI_MODEL": "unit-test"}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        root = self.enterContext(TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {"CLAIMS_DEMO_MODE": "1", "CLAIMS_DEMO_ACCESS_KEY": KEY}, clear=True))
        self.store = Store(Path(root) / "workflow.sqlite")
        self.auth = Auth()
        self.api = self.enterContext(TestClient(create_app(self.store, self.auth)))
        self.operator = self.login("operator")
        self.reviewer = self.login("reviewer")

    def login(self, role):
        result = self.api.post("/api/demo/token", json={"role": role, "access_key": KEY})
        self.assertEqual(result.status_code, 200, result.text)
        return {"Authorization": "Bearer " + result.json()["access_token"]}

    def enqueue(self, packet=READY, key="receipt-request"):
        return self.api.post("/api/cases", headers=self.operator | {"Idempotency-Key": key}, json=packet)

    def case(self, cid, actor=None):
        return self.api.get(f"/api/cases/{cid}", headers=actor or self.operator).json()

    def decision(self, cid, outcome, version=1, actor=None):
        return self.api.post(f"/api/cases/{cid}/decision", headers=actor or self.reviewer,
                             json={"outcome": outcome, "version": version, "note": "Checked synthetic documents."})

    def test_packet_rework_and_stale_reviewer_protection(self):
        case = self.enqueue(BLOCKED).json()["id"]
        self.assertTrue(process_one(self.store))
        self.assertEqual(self.case(case)["draft"]["missing"], ["authorization_record"])
        self.assertEqual(self.decision(case, "approve").status_code, 409)
        self.assertEqual(self.decision(case, "return").json()["state"], "returned")
        repaired = deepcopy(BLOCKED)
        repaired["documents"].append({"kind": "authorization_record", "text": "Synthetic record received."})
        result = self.api.post(f"/api/cases/{case}/revisions", headers=self.operator,
                               json={"version": 1, "packet": repaired})
        self.assertEqual(result.json()["version"], 2)
        self.assertTrue(process_one(self.store))
        self.assertEqual(self.decision(case, "approve").status_code, 409)
        self.assertEqual(self.decision(case, "approve", version=2).json()["state"], "approved")
        self.assertEqual([x["action"] for x in self.case(case)["audit"]],
                         ["received", "draft_ready", "returned", "resubmitted", "draft_ready", "approved"])

    def test_idempotency_and_changed_packet(self):
        first = self.enqueue().json()
        identical = deepcopy(READY)
        identical["documents"].reverse()
        self.assertEqual(self.enqueue(identical).json(), {"id": first["id"], "replayed": True})
        self.assertEqual(self.enqueue(BLOCKED).status_code, 409)

    def test_auth_expiry_audience_and_role_separation(self):
        cid = self.enqueue().json()["id"]
        process_one(self.store)
        self.assertEqual(self.api.get(f"/api/cases/{cid}").status_code, 401)
        self.assertEqual(self.decision(cid, "approve", actor=self.operator).status_code, 403)
        now = datetime.now(timezone.utc)
        base = {"sub": "another-operator", "roles": ["operator"], "iss": self.auth.issuer,
                "aud": self.auth.audience, "exp": now + timedelta(minutes=1)}
        for patch, expected in [({}, 404), ({"exp": now - timedelta(minutes=1)}, 401), ({"aud": "another-app"}, 401)]:
            token = jwt.encode(base | patch, self.auth.demo_key, algorithm="HS256")
            self.assertEqual(self.api.get(f"/api/cases/{cid}", headers={"Authorization": "Bearer " + token}).status_code,
                             expected)

    def test_expired_worker_cannot_overwrite_new_draft(self):
        cid = self.enqueue().json()["id"]
        old = self.store.claim()
        with self.store.connection(write=True) as c:
            c.execute("UPDATE jobs SET expires=0")
        replacement = self.store.claim()
        self.assertEqual(replacement["attempts"], 2)
        self.assertFalse(self.store.complete(old, {}))
        self.assertTrue(self.store.complete(replacement, draft_packet(READY)))
        self.assertEqual(self.case(cid)["draft"]["reason"], "missing_receipt")

    def test_unknown_or_ambiguous_notice_never_qualifies(self):
        packet = deepcopy(READY)
        packet["documents"][0]["text"] = "Submission receipt not found and authorization record not attached."
        self.assertFalse(draft_packet(packet)["eligible_for_approval"])

    @patch.dict(os.environ, PROVIDER)
    def test_only_bundled_notice_sent_and_fabricated_quote_rejected(self):
        with patch("claims.provider.propose_remote",
                   return_value=Draft(reason="missing_receipt", quote="A made-up quotation.")) as proposed:
            result = draft_packet(READY)
            self.assertEqual(result["provider"], "local_fallback")
            self.assertEqual(result["evidence"]["quote"], "submission receipt not found")
            changed = deepcopy(READY)
            changed["documents"][0]["text"] += " Extra sample text."
            self.assertEqual(draft_packet(changed)["provider"], "local")
            proposed.assert_called_once_with(READY["documents"][0]["text"], "unit-test", "unit-test")

    @patch.dict(os.environ, PROVIDER)
    def test_mock_responses_api_schema_and_evidence(self):
        structured = {"reason":"missing_receipt","quote":"submission receipt not found"}
        response = {"status":"completed","output":[{"type":"message","content":[{"type":"output_text",
                     "text":json.dumps(structured)}]}]}
        def fake(request):
            body = json.loads(request.content)
            self.assertEqual(body["input"], READY["documents"][0]["text"])
            self.assertTrue(body["text"]["format"]["strict"])
            self.assertFalse(body["store"])
            return httpx.Response(200, json=response)
        real_client = httpx.Client
        with patch("claims.provider.httpx.Client", side_effect=lambda **kw: real_client(transport=httpx.MockTransport(fake), **kw)):
            self.assertEqual(propose_remote(READY["documents"][0]["text"], "unit-test", "unit-test").reason, "missing_receipt")
            out = draft_packet(READY)
            self.assertEqual(out["provider"], "openai")
            self.assertTrue(out["eligible_for_approval"])

    def test_external_rsa_issuer_validation(self):
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        env = {"CLAIMS_ISSUER": "https://idp.example.test", "CLAIMS_JWKS_URL": "https://idp.example.test/jwks"}
        with patch.dict(os.environ, env), patch("claims.auth.PyJWKClient") as jwks:
            external = Auth()
            jwks.return_value.get_signing_key_from_jwt.return_value.key = private.public_key()
            base = {"sub":"reviewer-123","iss":external.issuer,"aud":external.audience,
                    "roles":["reviewer"],"exp":datetime.now(timezone.utc)+timedelta(minutes=1)}
            self.assertFalse(external.demo)
            self.assertEqual(external.verify(jwt.encode(base, private, algorithm="RS256")).role, "reviewer")
            for claims in (base | {"iss":"https://wrong.example.test"},base | {"aud":"wrong"}):
                with self.assertRaises(HTTPException):
                    external.verify(jwt.encode(claims, private, algorithm="RS256"))

    def test_invalid_request_does_not_echo_document_text(self):
        reply = self.api.post("/api/cases", headers=self.operator | {"Idempotency-Key": "malformed-test"},
                              json={"claim_ref":"real-looking-id","documents":[{"kind":"denial_notice","text":"private-ish marker"}]})
        self.assertEqual(reply.status_code, 422)
        self.assertNotIn("private-ish marker",reply.text)
        self.assertIn("X-Request-ID",reply.headers)


if __name__ == "__main__":
    unittest.main()
