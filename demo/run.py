"""Drive the real HTTP API and separate worker with an entirely synthetic case."""
import os
import time
from copy import deepcopy
from uuid import uuid4

import httpx

from claims.fixtures import BLOCKED


def main():
    key = os.environ["CLAIMS_DEMO_ACCESS_KEY"]
    base_url = os.getenv("CLAIMS_URL", "http://127.0.0.1:8000")
    with httpx.Client(base_url=base_url, timeout=5, trust_env=False) as client:
        def login(role):
            r = client.post("/api/demo/token", json={"role": role, "access_key": key})
            r.raise_for_status()
            return {"Authorization": "Bearer " + r.json()["access_token"]}

        operator, reviewer = login("operator"), login("reviewer")
        packet = deepcopy(BLOCKED)
        packet["claim_ref"] = "DEMO-" + uuid4().hex[:10].upper()
        r = client.post("/api/cases", headers=operator | {"Idempotency-Key": uuid4().hex}, json=packet)
        r.raise_for_status()
        path = "/api/cases/" + r.json()["id"]

        def wait_for_draft():
            for _ in range(80):
                item = client.get(path, headers=reviewer)
                item.raise_for_status()
                if item.json()["state"] == "needs_review":
                    return item.json()
                time.sleep(.15)
            raise RuntimeError("Worker did not draft the packet in time.")

        record = wait_for_draft()
        assert record["draft"]["missing"] == ["authorization_record"]
        approve = {"version": 1, "outcome": "approve", "note": "Synthetic example: packet reviewed."}
        result = client.post(path + "/decision", headers=reviewer, json=approve)
        assert result.status_code == 409, result.text
        client.post(path + "/decision", headers=reviewer,
                    json=approve | {"outcome":"return","note":"Attach the synthetic authorization record."}).raise_for_status()
        packet["documents"].append({"kind":"authorization_record", "text":"Synthetic authorization record attached."})
        client.post(path + "/revisions", headers=operator, json={"version":1,"packet":packet}).raise_for_status()
        assert wait_for_draft()["draft"]["eligible_for_approval"]
        result = client.post(path + "/decision", headers=reviewer, json=approve | {"version":2})
        result.raise_for_status()
        final = client.get(path, headers=reviewer).json()
        print(final["claim_ref"], final["state"], "v" + str(final["version"]))
        print(" → ".join(event["action"] for event in final["audit"]))


if __name__ == "__main__":
    main()
