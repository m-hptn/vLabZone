"""vLabZone broker prototype.

Stage 0 skeleton: only a health endpoint. Bookings, contexts, drivers and the
reconcile loop are added in stage 3 (see docs/roadmap.md and docs/concept.md §5.7).
"""
import os

from fastapi import FastAPI

app = FastAPI(title="vLabZone broker", version=os.environ.get("VLAB_VERSION", "dev"))


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "version": app.version, "edge_impl": os.environ.get("EDGE_IMPL", "linux")}
