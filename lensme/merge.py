"""Merge per-repo ontologies into one System-level view (C4 L1 foundation).

Products keep their full trees; cross-product links are derived from shared
external dependencies (the only structural signal available without a merged
code graph). UI rendering of System nodes is a follow-up - consume this via
`lensme tree` / `lensme report` for now.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone


def merge_ontologies(ontos: list[dict], name: str, description: str = "") -> dict:
    ext_products: dict[str, list[str]] = defaultdict(list)
    for o in ontos:
        for e in o.get("external", []):
            ext_products[e["name"]].append(o["name"])

    shared = [
        {"name": ext, "products": sorted(set(prods))}
        for ext, prods in sorted(ext_products.items())
        if len(set(prods)) >= 2
    ]
    stats = {
        "products": len(ontos),
        "features": sum(o["stats"].get("features", 0) for o in ontos),
        "components": sum(o["stats"].get("components", 0) for o in ontos),
        "files": sum(o["stats"].get("files", 0) for o in ontos),
        "loc": sum(o["stats"].get("loc", 0) for o in ontos),
        "shared_externals": len(shared),
    }
    return {
        "schema_version": 2,
        "meta": {
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "merged_from": [o["name"] for o in ontos],
        },
        "id": f"system_{re.sub(r'[^a-z0-9_]', '_', name.lower())}",
        "type": "System",
        "name": name,
        "description": description,
        "stats": stats,
        "children": ontos,
        "shared_externals": shared,
    }
