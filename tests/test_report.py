"""Self-check for lensme.report + lensme.merge - runnable directly."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lensme.build import build_ontology
from lensme.merge import merge_ontologies
from lensme.report import generate_report
from tests.test_build import _graph


def test_report_sections():
    onto = build_ontology(_graph(), prefix="proj/", product_name="proj")
    md = generate_report(onto)
    assert md.startswith("# proj - architecture")
    for section in ("## Structure", "## Component relationships",
                    "## External dependencies", "## Change impact"):
        assert section in md, f"missing {section}"
    assert "Billing" in md and "react" in md
    assert "INFERRED-heuristic" in md  # confidence tags survive into the doc


def test_report_hotspots_optional():
    onto = build_ontology(_graph(), prefix="proj/", product_name="proj")
    hs = {
        "since": "6 months ago", "commits_scanned": 42,
        "files": {"src/components/billing/Invoice.tsx": 7},
        "co_change": [{"a": "src/App.tsx", "b": "src/store/billingStore.ts",
                       "count": 5, "structural": False}],
    }
    md = generate_report(onto, hs)
    assert "## Git hotspots" in md and "Invoice.tsx | 7" in md
    assert "Hidden coupling" in md and "5x together" in md


def test_merge_shared_externals():
    a = build_ontology(_graph(), prefix="proj/", product_name="alpha")
    b = build_ontology(_graph(), prefix="proj/", product_name="beta")
    system = merge_ontologies([a, b], "acme")
    assert system["type"] == "System" and system["stats"]["products"] == 2
    shared = {s["name"]: s["products"] for s in system["shared_externals"]}
    assert shared == {"react": ["alpha", "beta"]}, shared
    assert [c["name"] for c in system["children"]] == ["alpha", "beta"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok {fn.__name__}")
    print(f"all {len(tests)} tests passed")
