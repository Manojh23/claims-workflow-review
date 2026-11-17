import json
import logging
import time

from .provider import draft_packet
from .store import Store

logger = logging.getLogger("claims.worker")


def process_one(store, processor=draft_packet):
    job = store.claim()
    if job is None:
        return False
    now = time.perf_counter()
    try:
        result = processor(json.loads(job["payload"]))
    except Exception:
        result = None
    final = store.complete(job, result)
    logger.info("draft_job case=%s revision=%d attempt=%d finalized=%s duration_ms=%d",
                job["case_id"], job["version"], job["attempts"], final, 1000*(time.perf_counter()-now))
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    db = Store()
    while True:
        if not process_one(db):
            time.sleep(.35)
