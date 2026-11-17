"""SQLite transactions, immutable input revisions, audit events, and worker leases."""
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
  id TEXT PRIMARY KEY, owner TEXT NOT NULL, claim_ref TEXT NOT NULL, intake_key TEXT NOT NULL,
  intake_digest TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL,
  UNIQUE(owner,intake_key), UNIQUE(owner,claim_ref)
);
CREATE TABLE IF NOT EXISTS revisions (
  case_id TEXT NOT NULL REFERENCES cases(id), version INTEGER NOT NULL,
  payload TEXT NOT NULL, draft TEXT, PRIMARY KEY(case_id,version)
);
CREATE TABLE IF NOT EXISTS jobs (
  case_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
  lease TEXT, expires INTEGER NOT NULL DEFAULT 0, attempts INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY(case_id,version),
  FOREIGN KEY(case_id,version) REFERENCES revisions(case_id,version)
);
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), version INTEGER NOT NULL,
  actor TEXT NOT NULL, action TEXT NOT NULL, note TEXT, recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS job_poll_idx ON jobs(status,expires);
CREATE INDEX IF NOT EXISTS audit_case_idx ON audit(case_id,id);
CREATE INDEX IF NOT EXISTS state_owner_idx ON cases(state,owner);
"""


class Problem(Exception):
    def __init__(self, code, message):
        self.status = code
        self.message = message


def encode(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def digest(obj):
    return hashlib.sha256(encode(obj).encode()).hexdigest()


def normalize(payload):
    return payload | {"documents": sorted(payload["documents"], key=lambda item: item["kind"])}


class Store:
    def __init__(self, path=None):
        self.path = Path(path or os.getenv("CLAIMS_DB", "data/review.sqlite"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as c:
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    @contextmanager
    def connection(self, write=False):
        c = sqlite3.connect(self.path, timeout=5)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                c.execute("BEGIN IMMEDIATE")
            with c:
                yield c
        finally:
            c.close()

    @staticmethod
    def _audit(c, case_id, version, actor, action, note=None):
        c.execute("INSERT INTO audit(case_id,version,actor,action,note) VALUES(?,?,?,?,?)",
                  (case_id, version, actor, action, note))

    @staticmethod
    def _revision(c, case_id, version, payload):
        c.execute("INSERT INTO revisions(case_id,version,payload) VALUES(?,?,?)", (case_id, version, encode(payload)))
        c.execute("INSERT INTO jobs(case_id,version) VALUES(?,?)", (case_id, version))

    @staticmethod
    def _case(c, case_id, actor):
        row = c.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone()
        if not row or (actor.role == "operator" and row["owner"] != actor.sub):
            raise Problem(404, "Case not found.")
        return row

    def create(self, actor, key, payload):
        payload = normalize(payload)
        fingerprint = digest(payload)
        with self.connection(write=True) as c:
            old = c.execute("SELECT * FROM cases WHERE owner=? AND intake_key=?", (actor.sub, key)).fetchone()
            if old:
                if old["intake_digest"] != fingerprint:
                    raise Problem(409, "That intake key is already bound to a different packet.")
                return {"id": old["id"], "replayed": True}
            if c.execute("SELECT 1 FROM cases WHERE owner=? AND claim_ref=?", (actor.sub, payload["claim_ref"])).fetchone():
                raise Problem(409, "This claim reference already exists. Revise the original case if returned.")
            new_id = str(uuid4())
            c.execute("INSERT INTO cases VALUES(?,?,?,?,?,1,'queued')",
                      (new_id, actor.sub, payload["claim_ref"], key, fingerprint))
            self._revision(c, new_id, 1, payload)
            self._audit(c, new_id, 1, actor.sub, "received")
            return {"id": new_id, "replayed": False}

    def list_cases(self, actor):
        with self.connection() as c:
            prefix = "WHERE owner=?" if actor.role == "operator" else ""
            return [dict(row) for row in c.execute(
                "SELECT id,claim_ref,state,version FROM cases " + prefix + " ORDER BY rowid DESC LIMIT 100",
                (actor.sub,) if prefix else ())]

    def read(self, case_id, actor):
        with self.connection() as c:
            c.execute("BEGIN")
            row = self._case(c, case_id, actor)
            r = c.execute("SELECT * FROM revisions WHERE case_id=? AND version=?", (case_id, row["version"])).fetchone()
            logs = c.execute("SELECT version,actor,action,note,recorded_at FROM audit WHERE case_id=? ORDER BY id",
                             (case_id,)).fetchall()
            return {k: row[k] for k in ("id", "claim_ref", "version", "state")} | {
                "documents": json.loads(r["payload"])["documents"],
                "draft": json.loads(r["draft"]) if r["draft"] else None,
                "audit": [dict(log) for log in logs]}

    def revised(self, case_id, actor, version, payload):
        with self.connection(write=True) as c:
            row = self._case(c, case_id, actor)
            if row["state"] != "returned" or version != row["version"]:
                raise Problem(409, "This case has changed or is not open for rework.")
            if payload["claim_ref"] != row["claim_ref"]:
                raise Problem(422, "The claim reference cannot change.")
            self._revision(c, case_id, version + 1, normalize(payload))
            c.execute("UPDATE cases SET version=?, state='queued' WHERE id=?", (version + 1, case_id))
            self._audit(c, case_id, version + 1, actor.sub, "resubmitted")
            return {"id": case_id, "version": version + 1}

    def decide(self, case_id, actor, decision):
        with self.connection(write=True) as c:
            row = self._case(c, case_id, actor)
            if row["owner"] == actor.sub:
                raise Problem(403, "An independent reviewer must decide this case.")
            if row["state"] != "needs_review" or row["version"] != decision.version:
                raise Problem(409, "This case has changed or already has a decision.")
            r = c.execute("SELECT draft FROM revisions WHERE case_id=? AND version=?",
                          (case_id, decision.version)).fetchone()
            draft = json.loads(r["draft"] or "null")
            if decision.outcome == "approve" and not (draft and draft["eligible_for_approval"]):
                raise Problem(409, "This packet lacks verified evidence or required documentation.")
            state = "approved" if decision.outcome == "approve" else "returned"
            c.execute("UPDATE cases SET state=? WHERE id=?", (state, case_id))
            self._audit(c, case_id, decision.version, actor.sub, state, decision.note)
            return {"id": case_id, "state": state, "version": decision.version}

    def claim(self):
        now = int(time.time())
        with self.connection(write=True) as c:
            expired = c.execute("""SELECT * FROM jobs
                WHERE status='running' AND expires<=? AND attempts>=3""", (now,)).fetchall()
            for row in expired:
                c.execute("UPDATE jobs SET status='failed',lease=NULL WHERE case_id=? AND version=?",
                          (row["case_id"], row["version"]))
                c.execute("UPDATE cases SET state='needs_review' WHERE id=? AND version=?",
                          (row["case_id"], row["version"]))
                self._audit(c, row["case_id"], row["version"], "system", "draft_unavailable")
            row = c.execute("""SELECT j.*,r.payload FROM jobs j JOIN revisions r USING(case_id,version)
                WHERE j.attempts<3 AND (j.status='queued' OR (j.status='running' AND j.expires<=?))
                ORDER BY j.rowid LIMIT 1""", (now,)).fetchone()
            if not row:
                return None
            lease = str(uuid4())
            c.execute("""UPDATE jobs SET status='running',lease=?,expires=?,attempts=attempts+1
                WHERE case_id=? AND version=?""", (lease, now + 45, row["case_id"], row["version"]))
            return dict(row) | {"lease": lease, "attempts": row["attempts"] + 1}

    def complete(self, job, result):
        cid, version, lease = job["case_id"], job["version"], job["lease"]
        state = "done" if result is not None else ("queued" if job["attempts"] < 3 else "failed")
        with self.connection(write=True) as c:
            r = c.execute("UPDATE jobs SET status=?,lease=NULL WHERE case_id=? AND version=? AND lease=?",
                          (state, cid, version, lease))
            if not r.rowcount or state == "queued":
                return False
            c.execute("UPDATE revisions SET draft=? WHERE case_id=? AND version=?",
                      (encode(result) if result is not None else None, cid, version))
            c.execute("UPDATE cases SET state='needs_review' WHERE id=? AND version=?", (cid, version))
            self._audit(c, cid, version, "system", "draft_ready" if result is not None else "draft_unavailable")
            return True
