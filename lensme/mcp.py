"""MCP server over stdio: agents query the ontology for architecture context.

Zero-dependency JSON-RPC loop (MCP stdio transport is newline-delimited
JSON-RPC 2.0). Exposes ten tools over a built ontology.json (the three
component-registry tools additionally read `~/.lensme/registry`, override
with LENSME_REGISTRY):

  get_context - task-scoped bundle (files, symbols, deps, blast radius) in one
                call, trimmed to a token budget - replaces ls/grep exploration
  overview    - the Product > Feature > Component tree with stats
  search      - find any node by name/path, with its ownership chain
  component   - full detail for one component (files, relationships, impact)
  impact      - "if I modify X, what is affected?"
  path        - shortest relationship path between two nodes with directions
  explain     - everything known about one node (component or file)
  search_components / get_component / install_component
              - assembly: find a verified component, vendor it, follow the
                computed wiring plan, generate only glue

Run: lensme mcp [--ontology graphify-out/ontology.json]
Register (Claude Code): claude mcp add lensme -- lensme mcp --ontology /abs/path/ontology.json
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from .build import tokenize

PROTOCOL_VERSION = "2024-11-05"

# Generic commit-message vocabulary: carries no repo-specific signal and
# false-positive matches directory/file tokens in every codebase (e.g. "fix"
# in a task about a bug fix has nothing to do with a file named fixer.py).
TASK_STOPWORDS = {
    "fix", "fixed", "fixes", "fixing", "add", "added", "adding", "adds",
    "update", "updated", "updating", "updates", "remove", "removed",
    "removing", "support", "supporting", "allow", "allowing", "avoid",
    "handle", "handling", "make", "making", "use", "using", "used",
    "change", "changed", "changes", "changing", "improve", "improved",
    "refactor", "refactored", "bug", "issue", "error", "errors", "failing",
    "fail", "test", "tests", "testing", "for", "the", "and", "with", "from",
    "into", "when", "that", "this", "not", "only", "now", "new", "old",
}


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


def _est_tokens(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False)) // 4  # ~4 chars/token heuristic


def _task_words(task: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9_]+", task.lower()) if len(w) > 2]
    return [w for w in words if w not in TASK_STOPWORDS] or words  # never empty if task had words


def _file_score(f: dict, words: list[str]) -> int:
    # exact token match, not substring: "fix" must not hit "fixer.py" or
    # "test" must not hit "latest.py" - tokenize() is the same path->word
    # splitter build.py uses for domain discovery, so this stays consistent
    # with how features/components were named in the first place.
    path_tokens = set(tokenize(f.get("path") or f["name"]))
    score = 3 * sum(1 for w in words if w in path_tokens)
    sym_tokens = {t for s in f.get("symbols", []) for t in tokenize(s["name"])}
    score += sum(1 for w in words if w in sym_tokens)
    return score


def tool_get_context(onto: Onto, args: dict) -> dict:
    """Task-scoped context bundle: the one MCP call that replaces an agent's
    ls/grep/read exploration walk. Everything is ranked against the task words
    and trimmed to a token budget."""
    task = args.get("task", "")
    words = _task_words(task)
    budget = int(args.get("budget", 2000))
    d = onto.data()

    comp = _resolve_component(onto, args["component"]) if args.get("component") else None
    ranked_comps: list[tuple[bool, int, dict, list[dict]]] = []
    for e in onto.index().values():
        n = e["node"]
        if n["type"] != "Component":
            continue
        files: list[dict] = []

        def collect(x: dict) -> None:
            if x["type"] == "File":
                files.append(x)
            for c in x.get("children", []):
                collect(c)

        collect(n)
        head = (n["name"] + " " + n.get("description", "")).lower()
        # top-K sum, not sum-over-all-files: a component with hundreds of
        # files each weakly matching (e.g. a shared "scripts" bucket) must
        # not outscore a small component with one or two exact hits
        file_scores = sorted((_file_score(f, words) for f in files), reverse=True)
        score = sum(5 for w in words if w in head) + sum(file_scores[:5])
        # docs/tests/examples bands answer "where is X documented", never
        # "where is X implemented" - any matching source component wins first
        support = any(a.get("kind") == "support" for a in e["chain"])
        ranked_comps.append((support, score, n, files))
    ranked_comps.sort(key=lambda t: (t[0], -t[1]))

    if comp is None:
        if not words:
            return {"error": "pass a task description and/or a component name"}
        matching = [t for t in ranked_comps if t[1] > 0]
        if not matching:
            return {"error": f"nothing matches task {task!r} - try the search tool"}
        comp = matching[0][2]
    files = next(fl for _, s, c, fl in ranked_comps if c["id"] == comp["id"])

    files_out = [
        {"path": f.get("path") or f["name"], "loc": f.get("loc"),
         "symbols": [{"name": s["name"], "line": s.get("line")} for s in f.get("symbols", [])]}
        for f in sorted(files, key=lambda f: (-_file_score(f, words), -(f.get("loc") or 0)))
    ]
    rels = d.get("component_relationships", [])
    chain = onto.index()[comp["id"]]["chain"]
    out = {
        "task": task,
        "component": {**_brief(comp),
                      "owned_by": [a["name"] for a in chain if a["type"] != "Product"]},
        "responsibilities": comp.get("responsibilities", []),
        "read_first": [f["path"] for f in files_out[:3]],
        "files": files_out,
        "depends_on": sorted({onto.name_of(r["target"]) for r in rels
                              if r["source"] == comp["id"]}),
        "dependents": sorted({onto.name_of(r["source"]) for r in rels
                              if r["target"] == comp["id"]}),
        "other_candidates": [c["name"] for _, s, c, _ in ranked_comps[:4]
                             if s > 0 and c["id"] != comp["id"]],
    }
    imp = d.get("impact", {}).get(comp["id"])
    if imp:
        out["impact_if_modified"] = {
            "directly_affects": [onto.name_of(i) for i in imp["direct"]],
            "total_files": imp["total_files"],
        }

    # trim to budget: drop symbols from least-relevant files first, then the files
    while _est_tokens(out) > budget and any(f["symbols"] for f in out["files"]):
        next(f for f in reversed(out["files"]) if f["symbols"])["symbols"] = []
    while _est_tokens(out) > budget and len(out["files"]) > 3:
        out["files"].pop()
    out["budget"] = {"requested_tokens": budget, "estimated_tokens": _est_tokens(out)}
    return out


def _resolve_any(onto: Onto, ref: str) -> dict | None:
    """Component by name/id, else File by path/name substring (exact wins)."""
    comp = _resolve_component(onto, ref)
    if comp is not None:
        return comp
    ref_l = ref.lower()
    files = [e["node"] for e in onto.index().values() if e["node"]["type"] == "File"]
    exact = [f for f in files if f["name"].lower() == ref_l or (f.get("path") or "").lower() == ref_l]
    partial = [f for f in files if ref_l in (f.get("path") or f["name"]).lower()]
    return (exact or partial or [None])[0]


def tool_path(onto: Onto, args: dict) -> dict:
    """Shortest relationship path between two nodes. Component pair -> walk
    component_relationships; anything else -> walk file_relationships.
    Edges are traversed both ways; each hop reports its real direction."""
    a, b = _resolve_any(onto, args["from"]), _resolve_any(onto, args["to"])
    if a is None or b is None:
        missing = args["from"] if a is None else args["to"]
        return {"error": f"no node matching {missing!r} - try the search tool"}
    d = onto.data()
    level = "component" if a["type"] == b["type"] == "Component" else "file"
    rels = d.get(f"{level}_relationships", [])

    adj: dict[str, list[tuple[str, str, bool]]] = {}
    for r in rels:
        adj.setdefault(r["source"], []).append((r["target"], r["relation"], True))
        adj.setdefault(r["target"], []).append((r["source"], r["relation"], False))

    prev: dict[str, tuple[str, str, bool]] = {a["id"]: ("", "", True)}
    frontier = [a["id"]]
    while frontier and b["id"] not in prev:
        nxt = []
        for nid in frontier:
            for tgt, rel, fwd in adj.get(nid, ()):
                if tgt not in prev:
                    prev[tgt] = (nid, rel, fwd)
                    nxt.append(tgt)
        frontier = nxt
    if b["id"] not in prev:
        return {"from": a["name"], "to": b["name"], "level": level, "path": None,
                "note": "no relationship path found"}
    hops, cur = [], b["id"]
    while cur != a["id"]:
        src, rel, fwd = prev[cur]
        hops.append({"from": onto.name_of(src if fwd else cur), "relation": rel,
                     "to": onto.name_of(cur if fwd else src)})
        cur = src
    return {"from": a["name"], "to": b["name"], "level": level,
            "hops": len(hops), "path": list(reversed(hops))}


def tool_explain(onto: Onto, args: dict) -> dict:
    """Everything known about one node: detail for a Component, or for a File
    its symbols, owner chain, and file-level in/out edges."""
    n = _resolve_any(onto, args["name"])
    if n is None:
        return {"error": f"no node matching {args['name']!r} - try the search tool"}
    if n["type"] == "Component":
        return tool_component(onto, {"component": n["id"]})
    d = onto.data()
    frs = d.get("file_relationships", [])
    chain = onto.index()[n["id"]]["chain"]
    return {
        **_brief(n),
        "path": n.get("path"),
        "loc": n.get("loc"),
        "owned_by": [{"type": a["type"], "name": a["name"]} for a in chain
                     if a["type"] != "Product"],
        "symbols": n.get("symbols", []),
        "outgoing": [{"relation": r["relation"], "target": onto.name_of(r["target"]),
                      "count": r.get("count", 1)} for r in frs if r["source"] == n["id"]],
        "incoming": [{"relation": r["relation"], "source": onto.name_of(r["source"]),
                      "count": r.get("count", 1)} for r in frs if r["target"] == n["id"]],
    }


def _registries() -> list:
    """Registries an agent should consult: repo-shared (.lensme/registry, found
    by walking up from cwd) then personal (~/.lensme/registry). LENSME_REGISTRY
    forces a single explicit dir."""
    import os

    from .registry import resolve_registries
    return resolve_registries(os.environ.get("LENSME_REGISTRY"), ".")


def tool_search_components(onto: Onto, args: dict) -> dict:
    from .registry import manifest_summary, search_registries

    hits = search_registries(_registries(), args["need"])
    if not hits:
        return {"matches": [], "note": "no verified components match - generate this "
                                       "part from scratch, or extract one first with `lensme extract`"}
    return {"matches": [manifest_summary(m) for m in hits[:5]],
            "note": "metadata only; call get_component(detail='full') if you truly "
                    "need the implementation, install_component to vendor it"}


def tool_get_component(onto: Onto, args: dict) -> dict:
    from .registry import load_component, which_registry

    src = which_registry(_registries(), args["name"])
    if src is None:
        return {"error": f"no component {args['name']!r} in any registry"}
    manifest, vdir = load_component(src, args["name"], args.get("version"))
    if args.get("detail") != "full":
        return manifest
    sources = {}
    for f in sorted((vdir / "src").rglob("*")):
        if f.is_file():
            sources[str(f.relative_to(vdir / "src"))] = f.read_text(encoding="utf-8", errors="ignore")
    return {**manifest, "sources": sources}


def tool_install_component(onto: Onto, args: dict) -> dict:
    from .registry import install_component, which_registry

    src = which_registry(_registries(), args["name"])
    if src is None:
        return {"error": f"no component {args['name']!r} in any registry"}
    return install_component(
        src, args["name"], args["dest_dir"],
        version=args.get("version"), target_ontology=args.get("target_ontology"),
    )


TOOLS = {
    "get_context": (tool_get_context, "Task-scoped context bundle in ONE call: the owning component, ranked files with symbols, read-first suggestions, dependencies/dependents, and blast radius - trimmed to a token budget. Use this INSTEAD of exploring with ls/grep/find when starting a task.", {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "what you are trying to do, in a few words (e.g. 'fix oauth2 scope validation')"},
            "component": {"type": "string", "description": "optional: component name/id if already known"},
            "budget": {"type": "number", "description": "approx token budget for the reply (default 2000)"},
        },
        "required": [],
    }),
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
    "path": (tool_path, "Shortest relationship path between two nodes (components or files), with the relation and direction of every hop.", {
        "type": "object",
        "properties": {
            "from": {"type": "string", "description": "component/file name or path"},
            "to": {"type": "string", "description": "component/file name or path"},
        },
        "required": ["from", "to"],
    }),
    "explain": (tool_explain, "Everything known about one node: component detail, or for a file its symbols, owner chain, and file-level in/out edges.", {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "component/file name or path"}},
        "required": ["name"],
    }),
    "search_components": (tool_search_components, "Search the local registry of verified, previously-extracted components BEFORE generating commodity code (auth, CRUD, upload, ...). Returns metadata + interface only - assembling a verified component beats regenerating it.", {
        "type": "object",
        "properties": {"need": {"type": "string", "description": "what you need, in a few words (e.g. 'image generation engine')"}},
        "required": ["need"],
    }),
    "get_component": (tool_get_component, "Full manifest for one registry component; detail='full' additionally returns source files (token-expensive - prefer install_component).", {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "version": {"type": "string"},
            "detail": {"type": "string", "enum": ["interface", "full"]},
        },
        "required": ["name"],
    }),
    "install_component": (tool_install_component, "Vendor a registry component's source into the target project (shadcn-style copy) and return a computed wiring plan: what to connect where, config to provide, deps to install, and the definition of done. Follow the plan, generate only glue.", {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "dest_dir": {"type": "string", "description": "target project root (absolute path)"},
            "version": {"type": "string"},
            "target_ontology": {"type": "string", "description": "target project's ontology.json - enables computed candidate matching"},
        },
        "required": ["name", "dest_dir"],
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
            "serverInfo": {"name": "lensme", "version": "0.1.0"},
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
        sys.exit(f"{path} not found - run `lensme build` first")
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
