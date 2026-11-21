"""Write experiment protocols to results/ using the fields from docs/concept.md §8."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[2] / "results"


def write_protocol(exp_id: str, title: str, passed: bool, measurements: dict | None = None,
                   setup_notes: str = "", observations: str = "", consequence: str = "") -> Path:
    RESULTS.mkdir(exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    edge = f"{os.environ.get('EDGE_IMPL', 'linux')}/{os.environ.get('EDGE_MODE', 'vrf')}"
    path = RESULTS / f"{exp_id}-{edge.replace('/', '-')}-{stamp}.json"
    path.write_text(json.dumps({
        "experiment": exp_id, "title": title, "date": stamp, "edge": edge,
        "setup": setup_notes, "result": "passed" if passed else "failed",
        "measurements": measurements or {}, "observations": observations,
        "consequence": consequence,
    }, indent=2))
    return path
