"""Structural diff between two ontology.json files.

The core of PR architecture reports: what components/files appeared or
vanished, which relationships got heavier, whose blast radius grew.
Component ids are deterministic (component_<domain>_<topdir>), so id equality
is meaningful across builds of the same repo.
"""
from __future__ import annotations


def _components(onto: dict) -> dict[str, dict]:
    out = {}

    def walk(n):
        if n.get("type") == "Component":
            out[n["id"]] = n
        for c in n.get("children", []):
            walk(c)
    walk(onto)
    return out


def _file_paths(onto: dict) -> set[str]:
    out = set()

    def walk(n):
        if n.get("type") == "File" and n.get("path"):
            out.add(n["path"])
        for c in n.get("children", []):
            walk(c)
    walk(onto)
    return out


def _rel_counts(onto: dict) -> dict[tuple[str, str, str], int]:
    return {
        (r["source"], r["target"], r["relation"]): r.get("count", 1)
        for r in onto.get("component_relationships", [])
    }


def diff_ontology(old: dict, new: dict) -> dict:
    oc, nc = _components(old), _components(new)
    of, nf = _file_paths(old), _file_paths(new)
    orel, nrel = _rel_counts(old), _rel_counts(new)

    rel_changes = []
    for key in sorted(set(orel) | set(nrel)):
        a, b = orel.get(key, 0), nrel.get(key, 0)
        if a != b:
            src, tgt, rel = key
            rel_changes.append({
                "source": nc.get(src, oc.get(src, {})).get("name", src),
                "target": nc.get(tgt, oc.get(tgt, {})).get("name", tgt),
                "relation": rel, "old": a, "new": b,
            })

    oimp, nimp = old.get("impact", {}), new.get("impact", {})
    impact_changes = []
    for cid in sorted(set(oimp) & set(nimp)):
        a, b = oimp[cid]["total_files"], nimp[cid]["total_files"]
        if a != b:
            impact_changes.append({
                "component": nc.get(cid, {}).get("name", cid),
                "old_total_files": a, "new_total_files": b,
            })

    return {
        "added_components": sorted(nc[c]["name"] for c in set(nc) - set(oc)),
        "removed_components": sorted(oc[c]["name"] for c in set(oc) - set(nc)),
        "added_files": sorted(nf - of),
        "removed_files": sorted(of - nf),
        "relationship_changes": rel_changes,
        "impact_changes": impact_changes,
        "stats": {"old": old.get("stats", {}), "new": new.get("stats", {})},
    }


def format_diff(d: dict) -> str:
    lines = ["lensme diff"]
    empty = True

    def sec(title, rows):
        nonlocal empty
        if rows:
            empty = False
            lines.append(f"\n{title}:")
            lines.extend(f"  {r}" for r in rows)

    sec("added components", d["added_components"])
    sec("removed components", d["removed_components"])
    sec("added files", d["added_files"])
    sec("removed files", d["removed_files"])
    sec("relationship changes", [
        f"{r['source']} --{r['relation']}-> {r['target']}: {r['old']} -> {r['new']}"
        for r in d["relationship_changes"]
    ])
    sec("impact changes (blast radius, total files)", [
        f"{r['component']}: {r['old_total_files']} -> {r['new_total_files']}"
        for r in d["impact_changes"]
    ])
    if empty:
        lines.append("  no structural changes")
    return "\n".join(lines)
