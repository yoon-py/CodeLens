"""Infer a C4-style ontology (Product > Feature > Component > Module > File,
plus External and Database) from a graphify graph.json.

Two-stage design, mirroring graphify's own extraction philosophy:

1. Deterministic skeleton (no API key, no LLM): directory nesting, path-token
   domain discovery, package-manifest externals, import-statement scanning.
   Confidence: INFERRED-heuristic (structural facts stay EXTRACTED).
2. Optional agent enrichment: a host agent (e.g. Claude) classifies files into
   components from their symbol lists (see ``symbol_digest``), producing an
   enrichment JSON that overrides the path-based grouping. Needed for flat
   packages where directory structure carries no signal.
   Confidence: INFERRED-llm.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 2  # v2: File.symbols[] + top-level file_relationships[]

SKIP_DIRS = {"src", "lib", "app"}  # conventional wrapper dirs to look past
GENERIC_WORDS = {
    "src", "index", "main", "app", "config", "test", "tests", "spec", "specs",
    "types", "type", "hooks", "hook", "store", "stores", "services", "service",
    "data", "views", "view", "components", "component", "styles", "style",
    "utils", "util", "lib", "libs", "i18n", "env", "d", "nodes", "node",
    "shared", "core", "common", "helpers", "helper",
    "docs", "doc", "examples", "example",  # reserved by kind_of() buckets
}
SUPPORT_KINDS = {  # conventional top-level dirs that are not product source
    "tests": "tests", "test": "tests", "__tests__": "tests", "spec": "tests", "specs": "tests",
    "docs": "docs", "doc": "docs", "documentation": "docs", "website": "docs",
    "docs_src": "examples", "examples": "examples", "example": "examples",
    "samples": "examples", "demo": "examples", "demos": "examples",
}
DEP_RELATIONS = {"imports", "imports_from", "calls", "references", "implements"}
RELATION_MAP = {
    "imports": "depends_on",
    "imports_from": "depends_on",
    "calls": "calls",
    "references": "references",
    "implements": "implements",
}
DB_KEYWORDS = ("postgres", "mysql", "sqlite", "mongo", "redis", "prisma", "supabase", "dynamodb")
TOPDIR_NAME_OVERRIDES = {"entry": "Entry Points & Configuration"}
MANIFEST_DEP_LABELS = {"dependencies", "devDependencies"}
LEVEL_KEYS = ("product", "feature", "component", "module", "file", "external", "database")


def tokenize(s: str) -> list[str]:
    s = re.sub(r"\.(tsx|ts|jsx|js|py|md|json|css|html)$", "", s)
    words = re.findall(r"[A-Za-z][a-z]*|[A-Z]+(?![a-z])|[0-9]+", s)
    return [w.lower() for w in words if len(w) > 2]


def topdir_of(rel_path: str) -> str:
    parts = rel_path.split("/")
    while len(parts) > 1 and parts[0] in SKIP_DIRS:
        parts = parts[1:]
    return "entry" if len(parts) == 1 else parts[0]


def module_of(rel_path: str) -> str | None:
    parts = rel_path.split("/")
    while len(parts) > 1 and parts[0] in SKIP_DIRS:
        parts = parts[1:]
    sub = parts[1:-1]
    return "/".join(sub) if sub else None


def kind_of(rel_path: str) -> str:
    """source | tests | docs | examples - by top-level directory convention.

    Repos like FastAPI are >70% docs/translations; without this split the
    supporting dirs drown the product source in every downstream view."""
    return SUPPORT_KINDS.get(rel_path.split("/", 1)[0].lower(), "source")


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


_DEP_SPEC_SPLIT = re.compile(r"[\s\[<>=~!;@,]")  # "pydantic>=1.7,!=1.8" -> "pydantic"


def _python_manifest_deps(path: Path) -> list[str]:
    """Package names from pyproject.toml [project.dependencies] or requirements.txt.

    Python manifests are not AST-parsed into the graph (unlike package.json),
    so externals for Python repos must come straight from disk."""
    text = _read_text(path)
    if not text:
        return []
    if path.name == "requirements.txt":
        specs = [
            l.strip() for l in text.splitlines()
            if l.strip() and not l.strip().startswith(("#", "-"))
        ]
    else:
        try:
            import tomllib
            specs = tomllib.loads(text).get("project", {}).get("dependencies", [])
        except ModuleNotFoundError:  # 3.10: no tomllib - line-scan the array
            m = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.S | re.M)
            specs = re.findall(r"['\"]([^'\"]+)['\"]", m.group(1)) if m else []
        except ValueError:  # malformed TOML - skip, never crash the build
            return []
    return [n for n in (_DEP_SPEC_SPLIT.split(s.strip(), 1)[0] for s in specs) if n]


def discover_domain_words(rel_paths: list[str], topdirs: list[str], top_n: int = 6) -> list[str]:
    """Path tokens recurring across >=2 distinct topdirs signal a business domain
    (feature) rather than an architecture layer.

    The frequency floor scales with corpus size: a token spanning 2 topdirs but
    only 2 files total (e.g. "chat" from aiChat.ts + chatStore.ts) is incidental
    word reuse, not a domain.
    """
    token_topdirs: dict[str, set[str]] = defaultdict(set)
    token_freq: Counter[str] = Counter()
    for rel, topdir in zip(rel_paths, topdirs):
        for tok in tokenize(rel):
            if tok not in GENERIC_WORDS:
                token_topdirs[tok].add(topdir)
                token_freq[tok] += 1
    min_freq = max(3, round(len(rel_paths) * 0.08))
    candidates = [
        (tok, len(dirs), token_freq[tok])
        for tok, dirs in token_topdirs.items()
        if len(dirs) >= 2 and token_freq[tok] >= min_freq
    ]
    candidates.sort(key=lambda t: (-t[1], -t[2]))
    return [tok for tok, _, _ in candidates[:top_n]]


def load_enrichment(path: str | Path | None) -> dict | None:
    """Load an agent-authored enrichment map (see docs/enrichment-spec.md).

    Schema v2::

        {"components": {"<key>": {
            "name": str, "rationale": str,
            "description": str (optional, user-facing),
            "responsibilities": [str] (optional),
            "feature": str (optional, default "core"),
            "files": ["<rel_path>", ...]}}}

    Files are keyed by path relative to the scan prefix - basenames collide
    (extractors/__init__.py vs __init__.py).
    """
    if not path or not Path(path).exists():
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    file_map: dict[str, str] = {}
    comp_meta: dict[str, dict] = {}
    for key, comp in data["components"].items():
        comp_meta[key] = {
            "name": comp["name"],
            "rationale": comp["rationale"],
            "description": comp.get("description", ""),
            "responsibilities": comp.get("responsibilities", []),
            "feature": comp.get("feature", "core"),
        }
        for rel in comp["files"]:
            file_map[rel] = key
    return {"file_map": file_map, "components": comp_meta}


def _file_level_nodes(sub_nodes: dict, prefix: str) -> dict[str, str]:
    """source_file -> node id, for AST nodes representing whole files.

    Graphs from older graphify versions carry no `_origin` marker; for those,
    label == basename is the only file-node signal we have, so only enforce
    `_origin == "ast"` when the graph actually declares origins.
    """
    has_origin = any(n.get("_origin") for n in sub_nodes.values())
    out = {}
    for nid, n in sub_nodes.items():
        sf = n["source_file"]
        if n["label"] == sf.rsplit("/", 1)[-1] and (n.get("_origin") == "ast" or not has_origin):
            out[sf] = nid
    return out


def symbol_digest(graph: dict, prefix: str) -> dict[str, list[str]]:
    """Per-file symbol lists (rel_path -> labels) - the input a host agent needs
    to author an enrichment map. Deliberately excludes file contents to keep the
    classification prompt cheap."""
    sub_nodes = {
        n["id"]: n for n in graph["nodes"]
        if n.get("source_file", "").startswith(prefix) and "/node_modules/" not in n["source_file"]
    }
    file_nodes = _file_level_nodes(sub_nodes, prefix)
    file_ids = set(file_nodes.values())
    contains: dict[str, list[str]] = defaultdict(list)
    for l in graph["links"]:
        if l["relation"] == "contains" and l["source"] in file_ids:
            tgt = sub_nodes.get(l["target"])
            if tgt:
                contains[l["source"]].append(tgt["label"])
    return {
        sf[len(prefix):]: sorted(contains.get(fid, []))
        for sf, fid in sorted(file_nodes.items())
    }


def _external_imports(text: str, external_names: list[str]) -> set[str]:
    """Which declared external packages does this source text import?

    Matches literal import statements (JS/TS import/require/from-quotes and
    Python import/from) - an import statement in the source is a structural
    fact, so resulting edges stay EXTRACTED.
    """
    hits = set()
    for name in external_names:
        esc = re.escape(name)
        # JS/TS: from 'react' | require("react") | import 'react/x'
        js = rf"""(?:from|require\s*\(|import)\s*['"]{esc}(?:['"/])"""
        # Python: import requests | from requests import x  (dots for subpkgs)
        py_name = esc.replace(r"\-", "_")
        py = rf"^\s*(?:import|from)\s+{py_name}(?:[\s.,]|$)"
        if re.search(js, text) or re.search(py, text, re.MULTILINE):
            hits.add(name)
    return hits


def _compute_impact(relationships: list[dict], comp_stats: dict[str, dict]) -> dict:
    """Reverse reachability over component relationships: if X changes, which
    components are affected? direct = 1 hop of incoming dependents,
    indirect = further transitive hops. total_files includes X itself."""
    dependents: dict[str, set[str]] = defaultdict(set)
    for r in relationships:
        dependents[r["target"]].add(r["source"])

    impact = {}
    for comp in comp_stats:
        direct = set(dependents.get(comp, ()))
        seen = {comp} | direct
        frontier = set(direct)
        indirect: set[str] = set()
        while frontier:
            nxt = set()
            for c in frontier:
                for dep in dependents.get(c, ()):
                    if dep not in seen:
                        seen.add(dep)
                        indirect.add(dep)
                        nxt.add(dep)
            frontier = nxt
        total_files = sum(comp_stats[c]["files"] for c in seen if c in comp_stats)
        impact[comp] = {
            "direct": sorted(direct),
            "indirect": sorted(indirect),
            "total_files": total_files,
        }
    return impact


def build_ontology(
    graph: dict,
    *,
    prefix: str = "",
    root: str | Path = ".",
    product_name: str = "project",
    product_description: str = "",
    enrichment: dict | None = None,
    source_graph: str = "",
) -> dict:
    root = Path(root)
    sub_nodes = {
        n["id"]: n for n in graph["nodes"]
        if n.get("source_file", "").startswith(prefix) and "/node_modules/" not in n["source_file"]
    }
    sub_links = [l for l in graph["links"] if l["source"] in sub_nodes and l["target"] in sub_nodes]
    file_nodes = _file_level_nodes(sub_nodes, prefix)

    # --- externals from package manifests present in the graph ---
    externals = []
    for nid, n in sub_nodes.items():
        if n["source_file"].endswith("package.json") and n["label"] in MANIFEST_DEP_LABELS:
            for l in sub_links:
                if l["source"] == nid and l["target"] in sub_nodes:
                    externals.append({
                        "id": l["target"],
                        "type": "External",
                        "name": sub_nodes[l["target"]]["label"],
                        "kind": n["label"],
                        "confidence": "EXTRACTED",
                    })
    # --- externals from Python manifests on disk (pyproject.toml, requirements.txt) ---
    seen_ext = {e["name"] for e in externals}
    for manifest in (root / prefix / "pyproject.toml", root / prefix / "requirements.txt"):
        for name in _python_manifest_deps(manifest):
            if name not in seen_ext:
                seen_ext.add(name)
                externals.append({
                    "id": f"external_{re.sub(r'[^a-z0-9_]', '_', name.lower())}",
                    "type": "External",
                    "name": name,
                    "kind": "dependencies",
                    "confidence": "EXTRACTED",
                })
    external_names = [e["name"] for e in externals]

    # symbols contained in each file node: name + line (v2 deep-dive payload)
    file_id_set = set(file_nodes.values())
    contained: dict[str, list[dict]] = defaultdict(list)
    for l in sub_links:
        if l["relation"] == "contains" and l["source"] in file_id_set:
            tgt = sub_nodes.get(l["target"])
            if tgt:
                loc = tgt.get("source_location", "")
                line = int(loc[1:]) if loc[:1] == "L" and loc[1:].isdigit() else None
                contained[l["source"]].append({"name": tgt["label"], "line": line})

    files = []
    for sf, fid in file_nodes.items():
        rel = sf[len(prefix):]
        text = _read_text(root / sf)
        symbols = sorted(contained.get(fid, []), key=lambda s: (s["line"] or 0, s["name"]))
        files.append({
            "id": fid,
            "rel_path": rel,
            "name": rel.rsplit("/", 1)[-1],
            "topdir": topdir_of(rel),
            "module": module_of(rel),
            "kind": kind_of(rel),
            "loc": text.count("\n") + (1 if text and not text.endswith("\n") else 0),
            "functions": len(symbols),
            "symbols": symbols,
            "external_imports": sorted(_external_imports(text, external_names)),
            "enriched": False,
        })

    # --- agent enrichment overrides path-based grouping (flat packages have no
    # directory signal, so the heuristic collapses into one catch-all component) ---
    if enrichment:
        for f in files:
            comp_key = enrichment["file_map"].get(f["rel_path"])
            if comp_key:
                f["topdir"] = comp_key
                f["module"] = None
                f["domain"] = enrichment["components"][comp_key]["feature"]
                f["enriched"] = True

    # --- domain (feature) discovery over the non-enriched remainder only;
    # tests/docs/examples are excluded so filename tokens from translations
    # and test suites can't mint fake product features ---
    plain = [f for f in files if not f["enriched"]]
    source_plain = [f for f in plain if f["kind"] == "source"]
    domain_words = discover_domain_words(
        [f["rel_path"] for f in source_plain], [f["topdir"] for f in source_plain]
    )

    def domain_of(rel_path: str) -> str:
        toks = set(tokenize(rel_path))
        for d in domain_words:
            if d in toks:
                return d
        return "shared"

    for f in plain:
        f["domain"] = domain_of(f["rel_path"]) if f["kind"] == "source" else f["kind"]

    # Flat source tree (single topdir, no cross-dir domain tokens) means the
    # heuristic has nothing to work with - flag it instead of guessing.
    source_topdirs = {f["topdir"] for f in source_plain}
    enrichment_recommended = not enrichment and (
        not domain_words or len(source_topdirs) <= 1
    )

    # --- cross-file dependency pairs, later rolled up to component level ---
    node_file_of = {nid: n["source_file"] for nid, n in sub_nodes.items()}
    file_by_relpath = {f["rel_path"]: f for f in files}

    def relpath_of(nid: str) -> str | None:
        sf = node_file_of.get(nid)
        return sf[len(prefix):] if sf and sf.startswith(prefix) else None

    file_dep_pairs: list[tuple[str, str, str]] = []
    for l in sub_links:
        if l["relation"] not in DEP_RELATIONS:
            continue
        src_rel, tgt_rel = relpath_of(l["source"]), relpath_of(l["target"])
        if (
            src_rel and tgt_rel and src_rel != tgt_rel
            and src_rel in file_by_relpath and tgt_rel in file_by_relpath
        ):
            file_dep_pairs.append((src_rel, tgt_rel, RELATION_MAP[l["relation"]]))

    db_hits = [f["rel_path"] for f in files if any(k in f["rel_path"].lower() for k in DB_KEYWORDS)]

    # --- assemble tree ---
    by_feature: dict[str, list[dict]] = defaultdict(list)
    for f in files:
        by_feature[f["domain"]].append(f)

    features = []
    component_id_lookup: dict[tuple[str, str], str] = {}
    comp_stats: dict[str, dict] = {}
    comp_external_imports: dict[str, Counter] = defaultdict(Counter)

    support_features = set(SUPPORT_KINDS.values())
    # source features first, supporting bands (tests/docs/examples) last
    for domain, flist in sorted(
        by_feature.items(), key=lambda kv: (kv[0] in support_features, kv[0])
    ):
        by_topdir: dict[str, list[dict]] = defaultdict(list)
        for f in flist:
            by_topdir[f["topdir"]].append(f)

        components = []
        for topdir, tlist in sorted(by_topdir.items()):
            comp_id = f"component_{domain}_{topdir}"
            component_id_lookup[(domain, topdir)] = comp_id
            enriched_meta = enrichment["components"].get(topdir) if enrichment else None
            if enriched_meta and any(f["enriched"] for f in tlist):
                comp_name = enriched_meta["name"]
                comp_conf = "INFERRED-llm"
                comp_rationale = enriched_meta["rationale"]
                comp_desc = enriched_meta["description"] or enriched_meta["rationale"]
                comp_resp = enriched_meta["responsibilities"]
            else:
                comp_name = TOPDIR_NAME_OVERRIDES.get(topdir, topdir.replace("_", " ").title())
                comp_conf = "INFERRED-heuristic"
                comp_rationale = f"All files under '{topdir}/' within the '{domain}' domain"
                comp_desc = comp_rationale
                comp_resp = []

            for f in tlist:
                for ext in f["external_imports"]:
                    comp_external_imports[comp_id][ext] += 1

            by_module: dict[str | None, list[dict]] = defaultdict(list)
            for f in tlist:
                by_module[f["module"]].append(f)

            children: list[dict] = []
            for module, mlist in sorted(by_module.items(), key=lambda kv: kv[0] or ""):
                entries = [
                    {
                        "id": f["id"], "type": "File", "name": f["name"], "path": f["rel_path"],
                        "loc": f["loc"], "functions": f["functions"],
                        "symbols": f["symbols"], "confidence": "EXTRACTED",
                    }
                    for f in sorted(mlist, key=lambda x: x["name"])
                ]
                if module is None:
                    children.extend(entries)
                else:
                    children.append({
                        "id": f"module_{domain}_{topdir}_{module.replace('/', '_')}",
                        "type": "Module", "name": module,
                        "confidence": "INFERRED-heuristic",
                        "rationale": f"Directory nesting under {topdir}/ ({module}/)",
                        "stats": {
                            "files": len(mlist),
                            "loc": sum(f["loc"] for f in mlist),
                            "functions": sum(f["functions"] for f in mlist),
                        },
                        "children": entries,
                    })

            comp_stats[comp_id] = {
                "files": len(tlist),
                "loc": sum(f["loc"] for f in tlist),
                "functions": sum(f["functions"] for f in tlist),
            }
            components.append({
                "id": comp_id, "type": "Component", "name": comp_name,
                "confidence": comp_conf, "rationale": comp_rationale,
                "description": comp_desc,
                "responsibilities": comp_resp,
                "stats": dict(comp_stats[comp_id]),  # dependencies filled below
                "children": children,
            })

        features.append({
            "id": f"feature_{domain}", "type": "Feature",
            "name": domain.replace("_", " ").title(),
            "kind": "support" if domain in support_features else "source",
            "confidence": "INFERRED-heuristic",
            "rationale": (
                f"Conventional supporting directory ({domain}) - kept out of the product map"
                if domain in support_features
                else f"Files whose path contains the auto-discovered domain token '{domain}'"
                if domain not in ("shared", "core")
                else "Files with no cross-cutting domain token match"
            ),
            "description": "",
            "stats": {
                "components": len(components),
                "files": len(flist),
                "loc": sum(f["loc"] for f in flist),
                "functions": sum(f["functions"] for f in flist),
            },
            "children": components,
        })

    # --- component-to-component relationships with rolled-up counts ---
    pair_counts: Counter[tuple[str, str, str]] = Counter()
    for src_rel, tgt_rel, relation in file_dep_pairs:
        sf_, tf_ = file_by_relpath[src_rel], file_by_relpath[tgt_rel]
        src_comp = component_id_lookup.get((sf_["domain"], sf_["topdir"]))
        tgt_comp = component_id_lookup.get((tf_["domain"], tf_["topdir"]))
        if src_comp and tgt_comp and src_comp != tgt_comp:
            pair_counts[(src_comp, tgt_comp, relation)] += 1

    relationships = [
        {"source": s, "target": t, "relation": rel, "confidence": "EXTRACTED", "count": c}
        for (s, t, rel), c in sorted(pair_counts.items())
    ]

    # --- file-to-file relationships (v2): same pairs before component rollup ---
    file_pair_counts: Counter[tuple[str, str, str]] = Counter()
    for src_rel, tgt_rel, relation in file_dep_pairs:
        file_pair_counts[
            (file_by_relpath[src_rel]["id"], file_by_relpath[tgt_rel]["id"], relation)
        ] += 1
    file_relationships = [
        {"source": s, "target": t, "relation": rel, "confidence": "EXTRACTED", "count": c}
        for (s, t, rel), c in sorted(file_pair_counts.items())
    ]

    # --- component -> external integrates_with edges (from import statements) ---
    ext_by_name = {e["name"]: e for e in externals}
    for comp_id, imports in sorted(comp_external_imports.items()):
        for ext_name, cnt in sorted(imports.items()):
            relationships.append({
                "source": comp_id, "target": ext_by_name[ext_name]["id"],
                "relation": "integrates_with", "confidence": "EXTRACTED", "count": cnt,
            })

    # dependencies stat = distinct outgoing targets per component
    out_targets: dict[str, set[str]] = defaultdict(set)
    for r in relationships:
        out_targets[r["source"]].add(r["target"])
    for feat in features:
        for comp in feat["children"]:
            comp["stats"]["dependencies"] = len(out_targets.get(comp["id"], ()))

    impact = _compute_impact(
        [r for r in relationships if r["relation"] != "integrates_with"], comp_stats
    )

    level_counts = {
        "product": 1,
        "feature": len(features),
        "component": sum(len(f["children"]) for f in features),
        "module": sum(
            1 for f in features for c in f["children"]
            for ch in c["children"] if ch.get("type") == "Module"
        ),
        "file": len(files),
        "external": len(externals),
        "database": len(db_hits),
    }
    communities = {n.get("community") for n in graph["nodes"] if n.get("community") is not None}

    return {
        "schema_version": SCHEMA_VERSION,
        "meta": {
            "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_graph": source_graph,
            "graph_stats": {
                "nodes": len(graph["nodes"]),
                "edges": len(graph["links"]),
                "communities": len(communities),
            },
            "level_counts": level_counts,
            "enrichment_recommended": enrichment_recommended,
        },
        "id": f"product_{re.sub(r'[^a-z0-9_]', '_', product_name.lower())}",
        "type": "Product",
        "name": product_name,
        "description": product_description,
        "stats": {
            "features": level_counts["feature"],
            "components": level_counts["component"],
            "files": level_counts["file"],
            "external": level_counts["external"],
            "database": level_counts["database"],
            "loc": sum(f["loc"] for f in files),
            "functions": sum(f["functions"] for f in files),
        },
        "discovered_domain_words": domain_words,
        "children": features,
        "component_relationships": relationships,
        "file_relationships": file_relationships,
        "external": externals,
        "database": db_hits,
        "impact": impact,
    }
