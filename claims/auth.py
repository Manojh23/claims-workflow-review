import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import HTTPException
from jwt import PyJWKClient


@dataclass(frozen=True)
class Actor:
    sub: str
    role: str


class Auth:
    def __init__(self):
        self.issuer = os.getenv("CLAIMS_ISSUER", "urn:claims-review:local")
        self.audience = os.getenv("CLAIMS_AUDIENCE", "claims-workflow-review")
        endpoint = os.getenv("CLAIMS_JWKS_URL")
        if endpoint and (not endpoint.startswith("https://") or not os.getenv("CLAIMS_ISSUER", "").startswith("https://")):
            raise ValueError("External OAuth2 mode needs explicit HTTPS issuer and JWKS URL.")
        self.jwks = PyJWKClient(endpoint, timeout=4) if endpoint else None
        self.demo = os.getenv("CLAIMS_DEMO_MODE") == "1" and self.jwks is None
        if not self.demo and self.jwks is None:
            raise RuntimeError("Configure an OAuth2 JWT issuer or explicitly enable local demo mode.")
        self.demo_key = secrets.token_urlsafe(48)
        configured = os.getenv("CLAIMS_DEMO_ACCESS_KEY", "")
        if self.demo and len(configured) < 20:
            raise RuntimeError("Demo mode requires a 20+ character CLAIMS_DEMO_ACCESS_KEY.")
        self.demo_access_digest = hashlib.sha256(configured.encode()).digest()

    def issue(self, role, key):
        if not self.demo:
            raise HTTPException(404, "Demo sign-in is disabled.")
        if not hmac.compare_digest(hashlib.sha256(key.encode()).digest(), self.demo_access_digest):
            raise HTTPException(401, "Invalid local access key.")
        now = datetime.now(timezone.utc)
        return jwt.encode({"sub": f"demo-{role}", "roles": [role], "iss": self.issuer, "aud": self.audience,
                           "iat": now, "exp": now + timedelta(minutes=25)}, self.demo_key, algorithm="HS256")

    def verify(self, token):
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key if self.jwks else self.demo_key
            claims = jwt.decode(token, key, algorithms=["RS256"] if self.jwks else ["HS256"],
                                issuer=self.issuer, audience=self.audience,
                                options={"require": ["exp", "iss", "sub", "aud"]})
        except (jwt.PyJWTError, ValueError, TypeError) as exc:
            raise HTTPException(401, "Invalid or expired bearer token.", headers={"WWW-Authenticate": "Bearer"}) from exc
        roles = claims.get("roles", [])
        if not isinstance(roles, list):
            raise HTTPException(403, "Invalid role claim.")
        # Explicitly assign one role per identity to separate operators from reviewers.
        granted = {r for r in roles if isinstance(r, str) and r in ("operator", "reviewer")}
        if len(granted) != 1:
            raise HTTPException(403, "One claims workbench role is required.")
        return Actor(claims["sub"], granted.pop())
