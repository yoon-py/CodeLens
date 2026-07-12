"""MCP server over stdio: agents query the ontology for architecture context.

Zero-dependency JSON-RPC loop (MCP stdio transport is newline-delimited
JSON-RPC 2.0). Exposes four tools over a built ontology.json:

  overview   - the Product > Feature > Component tree with stats
  search     - find any node by name/path, with its ownership chain
  component  - full detail for one component (files, relationships, impact)
  impact     - "if I modify X, what is affected?"

Run: ontomap mcp [--ontology graphify-out/ontology.json]
Register (Claude Code): claude mcp add ontomap -- ontomap mcp --ontology /abs/path/ontology.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROTOCOL_VERSION = "2024-11-05"


# ---------- ontology access (reload when the file changes: always-fresh maps) ----------

class Onto:
    def __init__(self, path: Path):
        self.path = path
        self._mtime = 0.0
        self._data: dict = {}
        self._index: dict[str, dict] = {}   # id -> {"node": n, "chain": [ancestor names]}

    def data(self) -> dict:
        mtime = self.path.stat().st_mtime
        if mtime != self._mtime:
            self._data = json.loads(self.path.read_text(encoding="utf-8"))
            self._index = {}
            self._walk(self._data, [])
            self._mtime = mtime
        return self._data

    def _walk(self, node: dict, chain: list[dict]) -> None:
        self._index[node["id"]] = {"node": node, "chain": list(chain)}
        for c in node.get("children", []):
            self._walk(c, chain + [node])

    def index(self) -> dict[str, dict]:
        self.data()
        return self._index

    def name_of(self, node_id: str) -> str:
        e = self.index().get(node_id)
        return e["node"]["name"] if e else node_id


# ---------- tool implementations ----------

def _brief(n: dict) -> dict:
    out = {"id": n["id"], "type": n["type"], "name": n["name"]}
    if n.get("description"):
        out["description"] = n["description"]
    if n.get("stats"):
        out["stats"] = n["stats"]
    if n.get("confidence"):
        out["confidence"] = n["confidence"]
    return out


def tool_overview(onto: Onto, args: dict) -> dict:
    d = onto.data()

    def tree(n: dict) -> dict:
        out = _brief(n)
        kids = [c for c in n.get("children", []) if c["type"] in ("Feature", "Component")]
        if kids:
            out["children"] = [tree(c) for c in kids]
        return out

    return {
        "product": tree(d),
        "meta": d.get("meta", {}),
        "relationship_count": len(d.get("component_relationships", [])),
    }


def tool_search(onto: Onto, args: dict) -> dict:
    q = args["query"].lower()
    hits = []
    for e in onto.index().values():
        n = e["node"]
        hay = (n["name"] + " " + (n.get("path") or "")).lower()
        # schema v2: File nodes carry symbols - match function/class names too
        sym_hits = [s for s in n.get("symbols", []) if q in s["name"].lower()]
        if q in hay or sym_hits:
            hit = {
                **_brief(n),
                "owned_by": [{"type": a["type"], "name": a["name"], "id": a["id"]}
                             for a in e["chain"] if a["type"] != "Product"],
            }
            if sym_hits:
                hit["matched_symbols"] = [
                    {"name": s["name"], "line": s.get("line")} for s in sym_hits[:10]
                ]
            hits.append(hit)
    hits.sort(key=lambda h: (h["name"].lower() != q, "matched_symbols" not in h, len(h["name"])))
    return {"query": args["query"], "matches": hits[:20], "total": len(hits)}


def _resolve_component(onto: Onto, ref: str) -> dict | None:
    idx = onto.index()
    if ref in idx and idx[ref]["node"]["type"] == "Component":
        return idx[ref]["node"]
    ref_l = ref.lower()
    comps = [e["node"] for e in idx.values() if e["node"]["type"] == "Component"]
    exact = [c for c in comps if c["name"].lower() == ref_l]
    partial = [c for c in comps if ref_l in c["name"].lower()]
    return (exact or partial or [None])[0]


def tool_component(onto: Onto, args: dict) -> dict:
    comp = _resolve_component(onto, args["component"])
    if comp is None:
        return {"error": f"no component matching {args['component']!r} - try the search tool"}
    d = onto.data()
    files = []

    def collect(n: dict) -> None:
        if n["type"] == "File":
            files.append({"name": n["name"], "path": n.get("path"), "loc": n.get("loc"),
                          "functions": n.get("functions")})
        for c in n.get("children", []):
            collect(c)

    collect(comp)
    rels = d.get("component_relationships", [])
    out = {
        **_brief(comp),
        "rationale": comp.get("rationale"),
        "responsibilities": comp.get("responsibilities", []),
        "files": sorted(files, key=lambda f: -(f["loc"] or 0)),
        "outgoing": [{"relation": r["relation"], "target": onto.name_of(r["target"]),
                      "count": r.get("count", 1)} for r in rels if r["source"] == comp["id"]],
        "incoming": [{"relation": r["relation"], "source": onto.name_of(r["source"]),
                      "count": r.get("count", 1)} for r in rels if r["target"] == comp["id"]],
    }
    imp = d.get("impact", {}).get(comp["id"])
    if imp:
        out["impact_if_modified"] = {
            "directly_affects": [onto.name_of(i) for i in imp["direct"]],
            "indirectly_affects": [onto.name_of(i) for i in imp["indirect"]],
            "total_files": imp["total_files"],
        }
    return out


def tool_impact(onto: Onto, args: dict) -> dict:
    comp = _resolve_component(onto, args["component"])
    if comp is None:
        return {"error": f"no component matching {args['component']!r} - try the search tool"}
    imp = onto.data().get("impact", {}).get(comp["id"])
    if not imp:
        return {"component": comp["name"], "note": "no impact data (leaf with no dependents)"}
    return {
        "component": comp["name"],
        "directly_affects": [onto.name_of(i) for i in imp["direct"]],
        "indirectly_affects": [onto.name_of(i) for i in imp["indirect"]],
        "total_files_in_blast_radius": imp["total_files"],
    }


TOOLS = {
    "overview": (tool_overview, "Architecture overview: the Product > Feature > Component tree with stats and descriptions. Start here.", {
        "type": "object", "properties": {}, "required": [],
    }),
    "search": (tool_search, "Find files/components/features by name or path substring. Returns each match with its ownership chain (which feature/component owns it).", {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "name or path substring"}},
        "required": ["query"],
    }),
    "component": (tool_component, "Full detail for one component: description, responsibilities, files, in/out relationships, and impact-if-modified.", {
        "type": "object",
        "properties": {"component": {"type": "string", "description": "component name or id"}},
        "required": ["component"],
    }),
    "impact": (tool_impact, "Blast radius of modifying a component: which components are directly/indirectly affected and how many files are involved.", {
        "type": "object",
        "properties": {"component": {"type": "string", "description": "component name or id"}},
        "required": ["component"],
    }),
}


# ---------- JSON-RPC stdio loop ----------

def _handle(onto: Onto, req: dict) -> dict | None:
    method = req.get("method", "")
    rid = req.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ontomap", "version": "0.1.0"},
        }
    elif method == "tools/list":
        result = {"tools": [
            {"name": name, "description": desc, "inputSchema": schema}
            for name, (_, desc, schema) in TOOLS.items()
        ]}
    elif method == "tools/call":
        name = req["params"]["name"]
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32602, "message": f"unknown tool {name!r}"}}
        try:
            payload = TOOLS[name][0](onto, req["params"].get("arguments", {}))
            result = {"content": [{"type": "text",
                                   "text": json.dumps(payload, indent=2, ensure_ascii=False)}]}
        except Exception as e:  # tool errors go back as MCP tool errors, not crashes
            result = {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
    elif method == "ping":
        result = {}
    elif rid is None:  # notification (e.g. notifications/initialized) - no response
        return None
    else:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def serve(ontology_path: str) -> None:
    path = Path(ontology_path)
    if not path.exists():
        sys.exit(f"{path} not found - run `ontomap build` first")
    onto = Onto(path)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = _handle(onto, req)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
