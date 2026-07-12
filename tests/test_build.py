"""Self-check for codelens.build - runnable directly: python tests/test_build.py"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from codelens.build import (
    build_ontology, discover_domain_words, symbol_digest, _external_imports,
    _compute_impact,
)


def _node(nid, label, source_file):
    return {"id": nid, "label": label, "source_file": source_file, "_origin": "ast"}


def _graph():
    """Tiny fake graph: two domain dirs (billing, orders) + a flat file, with deps
    and a package.json manifest declaring externals."""
    nodes = [
        _node("f_billing_ui", "Invoice.tsx", "proj/src/components/billing/Invoice.tsx"),
        _node("f_billing_store", "billingStore.ts", "proj/src/store/billingStore.ts"),
        _node("f_billing_svc", "billingApi.ts", "proj/src/services/billingApi.ts"),
        _node("f_orders_ui", "OrderList.tsx", "proj/src/components/orders/OrderList.tsx"),
        _node("f_orders_store", "ordersStore.ts", "proj/src/store/ordersStore.ts"),
        _node("f_orders_svc", "ordersApi.ts", "proj/src/services/ordersApi.ts"),
        _node("f_app", "App.tsx", "proj/src/App.tsx"),
        _node("s_invoice_fn", "renderInvoice()", "proj/src/components/billing/Invoice.tsx"),
        # a file with the same basename in a subdir - rel_path keying regression
        _node("f_init_root", "__init__.py", "proj/src/__init__.py"),
        _node("f_init_sub", "__init__.py", "proj/src/extractors/__init__.py"),
        # package manifest with dependencies
        _node("f_pkg", "package.json", "proj/package.json"),
        _node("f_pkg_deps", "dependencies", "proj/package.json"),
        _node("f_pkg_react", "react", "proj/package.json"),
    ]
    links = [
        {"relation": "contains", "source": "f_billing_ui", "target": "s_invoice_fn"},
        {"relation": "imports", "source": "f_billing_ui", "target": "f_billing_store"},
        {"relation": "imports_from", "source": "f_billing_store", "target": "f_billing_svc"},
        {"relation": "imports", "source": "f_orders_ui", "target": "f_orders_store"},
        {"relation": "calls", "source": "f_app", "target": "f_billing_ui"},
        {"relation": "contains", "source": "f_pkg", "target": "f_pkg_deps"},
        {"relation": "contains", "source": "f_pkg_deps", "target": "f_pkg_react"},
    ]
    return {"nodes": nodes, "links": links}


def _write_sources(root: Path):
    """Real files on disk so LOC counting and import scanning have content."""
    files = {
        "proj/src/components/billing/Invoice.tsx": "import { x } from 'react'\nexport const a = 1\n",
        "proj/src/App.tsx": "import React from 'react'\n",
        "proj/src/store/billingStore.ts": "export const s = 1\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


def test_heuristic_skeleton():
    onto = build_ontology(_graph(), prefix="proj/", product_name="proj")
    feats = {f["name"]: f for f in onto["children"]}
    assert "Billing" in feats and "Orders" in feats, feats.keys()
    billing_comps = {c["name"] for c in feats["Billing"]["children"]}
    assert billing_comps == {"Components", "Store", "Services"}, billing_comps
    shared = feats["Shared"]
    entry = next(c for c in shared["children"] if c["id"].endswith("_entry"))
    assert any(f["name"] == "App.tsx" for f in entry["children"])
    rels = {(r["source"], r["target"], r["relation"]) for r in onto["component_relationships"]}
    assert ("component_billing_components", "component_billing_store", "depends_on") in rels, rels
    assert all(f["confidence"] == "INFERRED-heuristic" for f in onto["children"])


def test_schema_v1_meta_and_stats():
    onto = build_ontology(_graph(), prefix="proj/", product_name="proj")
    assert onto["schema_version"] == 2
    m = onto["meta"]
    assert m["graph_stats"]["nodes"] == 13 and m["graph_stats"]["edges"] == 7
    lc = m["level_counts"]
    assert lc["file"] == 10 and lc["external"] == 1 and lc["product"] == 1
    # relation counts rolled up
    dep = next(r for r in onto["component_relationships"]
               if r["source"] == "component_billing_components" and r["relation"] == "depends_on")
    assert dep["count"] == 1
    # functions rollup: billing components has 1 symbol
    feats = {f["name"]: f for f in onto["children"]}
    comp = next(c for c in feats["Billing"]["children"] if c["name"] == "Components")
    assert comp["stats"]["functions"] == 1
    assert "dependencies" in comp["stats"]


def test_external_integrates_with():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_sources(root)
        onto = build_ontology(_graph(), prefix="proj/", root=root, product_name="proj")
        ints = [r for r in onto["component_relationships"] if r["relation"] == "integrates_with"]
        srcs = {r["source"] for r in ints}
        # Invoice.tsx (billing components) and App.tsx (shared entry) import react
        assert "component_billing_components" in srcs, ints
        assert all(r["target"] == "f_pkg_react" for r in ints)
        assert all(r["confidence"] == "EXTRACTED" for r in ints)


def test_impact_precompute():
    rels = [
        {"source": "A", "target": "B", "relation": "depends_on"},
        {"source": "B", "target": "C", "relation": "calls"},
    ]
    stats = {"A": {"files": 2}, "B": {"files": 3}, "C": {"files": 5}}
    impact = _compute_impact(rels, stats)
    assert impact["C"]["direct"] == ["B"]
    assert impact["C"]["indirect"] == ["A"]
    assert impact["C"]["total_files"] == 10  # C(5) + B(3) + A(2)
    assert impact["A"]["direct"] == [] and impact["A"]["total_files"] == 2


def test_enrichment_overrides_by_relpath():
    enrichment = {
        "file_map": {
            "src/__init__.py": "pkg_api",
            "src/App.tsx": "app_shell",
        },
        "components": {
            "pkg_api": {"name": "Package API", "rationale": "public exports",
                        "description": "Public package surface", "responsibilities": ["Export API"],
                        "feature": "core"},
            "app_shell": {"name": "App Shell", "rationale": "root component",
                          "description": "", "responsibilities": [], "feature": "core"},
        },
    }
    onto = build_ontology(_graph(), prefix="proj/", product_name="proj", enrichment=enrichment)
    feats = {f["name"]: f for f in onto["children"]}
    comps = {c["name"]: c for c in feats["Core"]["children"]}
    assert comps["Package API"]["confidence"] == "INFERRED-llm"
    assert comps["Package API"]["description"] == "Public package surface"
    assert comps["Package API"]["responsibilities"] == ["Export API"]
    # description falls back to rationale when empty
    assert comps["App Shell"]["description"] == "root component"
    pkg_files = {f["path"] for f in comps["Package API"]["children"]}
    assert pkg_files == {"src/__init__.py"}, pkg_files  # basename collision guard

    all_paths = []

    def walk(n):
        if n.get("type") == "File":
            all_paths.append(n["path"])
        for c in n.get("children", []):
            walk(c)

    walk(onto)
    assert "src/extractors/__init__.py" in all_paths


def test_domain_words_frequency_floor():
    rels = ["src/services/aiChat.ts", "src/store/chatStore.ts"]
    tops = ["services", "store"]
    assert discover_domain_words(rels, tops) == []


def test_external_imports_matching():
    js = "import { useState } from 'react'\nimport Flow from '@xyflow/react'\n"
    assert _external_imports(js, ["react", "@xyflow/react", "zustand"]) == {"react", "@xyflow/react"}
    py = "import requests\nfrom mypkg import x\n"
    assert _external_imports(py, ["requests", "numpy"]) == {"requests"}
    # 'react' must not match 'react-dom' import or substrings
    js2 = "import x from 'react-dom'\n"
    assert _external_imports(js2, ["react"]) == set()


def test_symbol_digest():
    digest = symbol_digest(_graph(), "proj/")
    assert digest["src/components/billing/Invoice.tsx"] == ["renderInvoice()"]
    assert "src/App.tsx" in digest


def test_schema_v2_symbols_and_file_relationships():
    onto = build_ontology(_graph(), prefix="proj/", product_name="proj")
    # File nodes carry their contained symbols (name + line)
    def find_file(n, name):
        if n.get("type") == "File" and n["name"] == name:
            return n
        for c in n.get("children", []):
            hit = find_file(c, name)
            if hit:
                return hit
    inv = find_file(onto, "Invoice.tsx")
    assert inv and inv["symbols"] == [{"name": "renderInvoice()", "line": None}], inv["symbols"]
    # file-to-file edges survive alongside the component rollup
    frs = onto["file_relationships"]
    assert frs, "file_relationships must not be empty"
    assert any(r["source"] == "f_billing_ui" and r["target"] == "f_billing_store" for r in frs)
    assert all(r["relation"] in ("depends_on", "calls", "references", "implements") for r in frs)


def test_no_origin_graph_fallback():
    # Graphs from older graphify versions carry no `_origin` field at all;
    # file detection must fall back to label == basename instead of yielding 0 files.
    g = _graph()
    for n in g["nodes"]:
        n.pop("_origin", None)
    onto = build_ontology(g, prefix="proj/", product_name="legacy")
    assert onto["stats"]["files"] > 0, "no-_origin graph must still find file nodes"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok {fn.__name__}")
    print(f"all {len(tests)} tests passed")
