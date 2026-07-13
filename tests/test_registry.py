"""Self-check for lensme.registry - extract -> search -> install round trip."""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lensme.build import build_ontology
from lensme.registry import (
    extract_component, install_component, load_component, search_registry,
    manifest_summary,
)
from tests.test_build import _graph, _node, _write_sources


def _setup(tmp: Path, with_tests: bool = False) -> tuple[Path, Path]:
    """Write sources + build ontology to disk; returns (onto_path, registry_dir)."""
    _write_sources(tmp)
    # billingApi.ts exists as a node but _write_sources doesn't create it
    (tmp / "proj/src/services").mkdir(parents=True, exist_ok=True)
    (tmp / "proj/src/services/billingApi.ts").write_text(
        "const KEY = process.env.BILLING_API_KEY\nexport const call = 1\n", encoding="utf-8"
    )
    g = _graph()
    if with_tests:
        g["nodes"].append(_node("f_test_inv", "invoice.test.ts", "proj/tests/invoice.test.ts"))
        g["links"].append({"relation": "imports", "source": "f_test_inv", "target": "f_billing_ui"})
        (tmp / "proj/tests").mkdir(parents=True, exist_ok=True)
        (tmp / "proj/tests/invoice.test.ts").write_text("import Invoice\n", encoding="utf-8")
    onto = build_ontology(g, prefix="proj/", root=tmp, product_name="proj")
    onto_path = tmp / "ontology.json"
    onto_path.write_text(json.dumps(onto), encoding="utf-8")
    return onto_path, tmp / "registry"


def test_extract_manifest_shape():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        onto_path, reg = _setup(tmp, with_tests=True)
        m = extract_component(onto_path, "component_billing_components",
                              registry_dir=reg, root=tmp, prefix="proj/")
        assert m["name"] == "components" and m["version"] == "1.0.0"
        assert m["confidence"] == "EXTRACTED"
        assert m["language"] == "typescript"
        # Invoice.tsx is the entry (test file imports it from outside)
        assert "src/components/billing/Invoice.tsx" in m["interface"]["entry_files"]
        assert any(e["name"] == "renderInvoice()" for e in m["interface"]["exports"])
        # depends on billingStore which lives outside this component
        assert "billingStore" in m["dependencies"]["internal_unresolved"]
        hint = next(h for h in m["wiring_hints"] if h["unresolved"] == "billingStore")
        assert hint["original_provider"] == "src/store/billingStore.ts"
        assert m["dependencies"]["external"] == ["react"]
        assert m["tests"] == ["tests/invoice.test.ts"], m["tests"]
        # snapshot exists on disk
        assert (reg / "components/1.0.0/src/src/components/billing/Invoice.tsx").exists()


def test_version_bump_and_search():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        onto_path, reg = _setup(tmp)
        m1 = extract_component(onto_path, "component_billing_components",
                               registry_dir=reg, root=tmp, prefix="proj/", name="invoice-ui")
        m2 = extract_component(onto_path, "component_billing_components",
                               registry_dir=reg, root=tmp, prefix="proj/", name="invoice-ui")
        assert (m1["version"], m2["version"]) == ("1.0.0", "1.0.1")
        hits = search_registry(reg, "invoice rendering ui")
        assert hits and hits[0]["name"] == "invoice-ui"
        assert hits[0]["version"] == "1.0.1"  # latest wins
        assert search_registry(reg, "quantum flux") == []
        s = manifest_summary(hits[0])
        assert "exports" in s and "sources" not in s  # summaries never carry code


def test_install_and_wiring_plan():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        onto_path, reg = _setup(tmp)
        extract_component(onto_path, "component_billing_services",
                          registry_dir=reg, root=tmp, prefix="proj/", name="billing-api")
        dest = tmp / "newproj"
        dest.mkdir()
        (dest / ".env.example").write_text("OTHER_KEY=x\n", encoding="utf-8")
        out = install_component(reg, "billing-api", dest, target_ontology=onto_path)
        assert out["installed_files"], "source files must be vendored"
        installed = Path(out["installed_files"][0])
        assert installed.read_text(encoding="utf-8").startswith("// lensme component:")
        plan = out["wiring_plan"]
        # BILLING_API_KEY not in target env -> add_required
        cfg = {c["key"]: c["status"] for c in plan["config"]}
        assert cfg.get("BILLING_API_KEY") == "add_required", plan["config"]
        assert "definition_of_done" in plan
        assert Path(out["wiring_doc"]).exists()


def test_wiring_matches_target_equivalent():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        onto_path, reg = _setup(tmp)
        # billing UI depends on billingStore; the target project (same ontology
        # here) HAS a billingStore -> wiring must auto-match it
        extract_component(onto_path, "component_billing_components",
                          registry_dir=reg, root=tmp, prefix="proj/", name="invoice-ui")
        dest = tmp / "newproj2"
        dest.mkdir()
        out = install_component(reg, "invoice-ui", dest, target_ontology=onto_path)
        wire = {u["unresolved"]: u for u in out["wiring_plan"]["unresolved"]}
        assert wire["billingStore"]["status"] == "auto_matched", wire
        assert wire["billingStore"]["candidates"] == ["src/store/billingStore.ts"]
        # without a target ontology the plan degrades gracefully
        out2 = install_component(reg, "invoice-ui", tmp / "newproj3")
        assert all(u["status"] == "no_target_ontology"
                   for u in out2["wiring_plan"]["unresolved"])


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok {fn.__name__}")
    print(f"all {len(tests)} tests passed")
