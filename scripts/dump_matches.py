"""Dump analyzed offers with scores, URLs and failed requirements.

Usage (on the machine holding the production DB):
    uv run python scripts/dump_matches.py [limit]

Prints one block per offer: id, score, title @ company, canonical URL and the
X (unmapped) requirement names -- the calibration evidence for the scorer.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"


def _failed_names(breakdown_json: str) -> list[str]:
    """Extract the requirement names with status 'X' from stored JSON.

    Tolerates both stored shapes: a bare list, or a dict with a 'breakdown'
    key. Requirement entries may nest the name under 'requirement' as a dict
    or carry it directly as a string.
    """
    data = json.loads(breakdown_json)
    items = data.get("breakdown", []) if isinstance(data, dict) else data
    names: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status", ""))
        if status.lower() in ("x", "✗"):
            req = item.get("requirement")
            if isinstance(req, dict):
                name = req.get("name")
            else:
                name = req
            if name:
                names.append(str(name))
    return names


def main() -> int:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    if not DB_PATH.exists():
        print(f"no DB at {DB_PATH} -- run from a machine with production state")
        return 1
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        """
        SELECT o.id, o.posicion, o.empresa, o.url_oferta, o.origen, m.score,
               m.breakdown_json
        FROM offers o
        JOIN match_results m ON m.offer_id = o.id
        ORDER BY o.id DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if not rows:
        print("no analyzed offers found")
        return 0
    for oid, title, company, url, origen, score, breakdown in rows:
        failed = _failed_names(breakdown or "[]")
        print(f"#{oid} [{score}/100] ({origen}) {title} @ {company}")
        print(f"   {url}")
        print(f"   X: {', '.join(failed) if failed else '(none)'}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
