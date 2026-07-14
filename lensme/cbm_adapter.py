"""Adapter: codebase-memory-mcp graph -> lensme's graph.json contract.

lensme consumes a code graph; it doesn't build one. This maps cbm's richer
graph (tree-sitter + LSP call resolution) onto the same six-field contract
graphify satisfies, so `lensme build` works unchanged on either engine.

The contact surface stays tiny on purpose (README "six fields"): nodes carry
id/label/source_file/_origin, links carry source/target/relation. If cbm
changes its query output this one module breaks loudly, not the whole pipeline.

cbm is talked to only through the documented `cli query_graph` interface (a
read-only openCypher subset), never its internal SQLite - that schema is
explicitly not a stable contract.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

DEFAULT_CBM_BIN = "codebase-memory-mcp"
SYMBOL_LABELS = "Function|Method|Class"  # the API surface; skip Variable/Module/Route noise

# cbm edge type -> lensme relation (lensme's DEP_RELATIONS + contains)
_REL_MAP = {
    "CALLS": "calls",
    "IMPORTS": "imports",
    "USAGE": "references",
    "INHERITS": "implements",
}


def _cbm_bin(override: str | None) -> str:
    found = override or shutil.which(DEFAULT_CBM_BIN)
    if not found:
        raise FileNotFoundError(
            f"{DEFAULT_CBM_BIN} not found on PATH - install it "
            "(https://github.com/DeusData/codebase-memory-mcp) or pass --cbm-bin"
        )
    return found


def _run(bin_: str, tool: str, payload: dict) -> dict:
    """One cbm `cli <tool>` call. Logs go to stderr; stdout is the JSON result."""
    proc = subprocess.run(
        [bin_, "cli", tool], input=json.dumps(payload),
        capture_output=True, text=True, check=True,
    )
    out = proc.stdout.strip()
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        # tolerate a stray leading log line on stdout: take the last JSON line
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                return json.loads(line)
        raise


def _query(bin_: str, project: str, cypher: str) -> list[list]:
    return _run(bin_, "query_graph", {"project": project, "query": cypher}).get("rows", [])


def find_project(root: str | Path, cbm_bin: str | None = None) -> str | None:
    """cbm project name whose root_path matches `root`, or None if not indexed."""
    bin_ = _cbm_bin(cbm_bin)
    root_resolved = str(Path(root).resolve())
    data = _run(bin_, "list_projects", {})
    for p in data.get("projects", data if isinstance(data, list) else []):
        if str(Path(p.get("root_path", "")).resolve()) == root_resolved:
            return p["name"]
    return None


def index_repo(root: str | Path, cbm_bin: str | None = None, mode: str = "fast") -> str:
    """Index `root` with cbm and return its project name."""
    bin_ = _cbm_bin(cbm_bin)
    res = _run(bin_, "index_repository", {"repo_path": str(Path(root).resolve()), "mode": mode})
    if res.get("status") not in (None, "indexed"):
        raise RuntimeError(f"cbm index failed: {res.get('hint') or res}")
    proj = find_project(root, bin_)
    if not proj:
        raise RuntimeError("cbm indexed the repo but no matching project was found")
    return proj


def cbm_graph(project: str, cbm_bin: str | None = None) -> dict:
    """Pull a cbm project's graph and shape it as lensme graph.json.

    Only File + Function/Method/Class nodes are emitted; edges whose endpoints
    fall outside that set (Module/Variable/etc.) dangle and lensme drops them,
    which only sheds weak signal - CALLS/IMPORTS carry the real dependencies.
    """
    bin_ = _cbm_bin(cbm_bin)
    nodes: list[dict] = []

    for row in _query(bin_, project, "MATCH (f:File) RETURN id(f), f.name, f.file_path"):
        nid, name, path = row
        if path and name:
            nodes.append({"id": str(nid), "label": name, "source_file": path, "_origin": "ast"})

    for row in _query(
        bin_, project,
        f"MATCH (s:{SYMBOL_LABELS}) RETURN id(s), s.name, s.file_path, s.start_line",
    ):
        nid, name, path, line = row
        if not (path and name):
            continue
        node = {"id": str(nid), "label": name, "source_file": path, "_origin": "ast"}
        if line not in (None, ""):
            node["source_location"] = f"L{line}"
        nodes.append(node)

    links: list[dict] = []
    for row in _query(
        bin_, project,
        f"MATCH (f:File)-[:DEFINES]->(s:{SYMBOL_LABELS}) RETURN id(f), id(s)",
    ):
        links.append({"source": str(row[0]), "target": str(row[1]), "relation": "contains"})

    for cbm_rel, lensme_rel in _REL_MAP.items():
        for row in _query(bin_, project, f"MATCH (a)-[:{cbm_rel}]->(b) RETURN id(a), id(b)"):
            links.append({"source": str(row[0]), "target": str(row[1]), "relation": lensme_rel})

    return {"nodes": nodes, "links": links}


def build_cbm_graph_file(
    root: str | Path, out_path: str | Path,
    *, cbm_bin: str | None = None, reindex: bool = False, mode: str = "fast",
) -> tuple[str, dict]:
    """Ensure `root` is cbm-indexed, write lensme graph.json to out_path.
    Returns (project_name, stats)."""
    bin_ = _cbm_bin(cbm_bin)
    project = None if reindex else find_project(root, bin_)
    if project is None:
        project = index_repo(root, bin_, mode)
    graph = cbm_graph(project, bin_)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(graph), encoding="utf-8")
    return project, {"nodes": len(graph["nodes"]), "edges": len(graph["links"])}
