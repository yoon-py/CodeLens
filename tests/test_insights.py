"""Self-check for codelens.insights + codelens.diff - runnable directly."""
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from codelens.insights import (
    blast_radius, compute_hotspots, hotspots_json, impact_check_report, match_paths,
    collect_files,
)
from codelens.diff import diff_ontology, format_diff


def _onto(extra_files=(), rels=None, impact=None):
    files = [
        {"id": "f_a", "type": "File", "name": "a.py", "path": "pkg/a.py"},
        {"id": "f_b", "type": "File", "name": "b.py", "path": "pkg/b.py"},
        {"id": "f_c", "type": "File", "name": "c.py", "path": "pkg/c.py"},
        *extra_files,
    ]
    return {
        "id": "p", "type": "Product", "name": "p",
        "stats": {"files": len(files)},
        "children": [{
            "id": "feat_x", "type": "Feature", "name": "X",
            "children": [{
                "id": "comp_x", "type": "Component", "name": "Core",
                "children": files,
            }],
        }],
        "component_relationships": rels or [],
        "file_relationships": [
            # b depends on a, c depends on b  =>  changing a hits b (direct), c (indirect)
            {"source": "f_b", "target": "f_a", "relation": "depends_on", "count": 1},
            {"source": "f_c", "target": "f_b", "relation": "calls", "count": 2},
        ],
        "impact": impact or {},
    }


def test_blast_radius():
    frs = _onto()["file_relationships"]
    direct, indirect = blast_radius(frs, "f_a")
    assert direct == ["f_b"] and indirect == ["f_c"]
    assert blast_radius(frs, "f_c") == ([], [])


def test_match_paths_suffix():
    files = collect_files(_onto())
    m = match_paths(["repo/pkg/a.py", "pkg/b.py", "unrelated.md"], files)
    assert m["repo/pkg/a.py"]["node"]["id"] == "f_a"   # suffix match
    assert m["pkg/b.py"]["node"]["id"] == "f_b"        # exact match
    assert "unrelated.md" not in m


def test_impact_check_report():
    rep = impact_check_report(_onto(), ["repo/pkg/a.py"])
    assert "a.py" in rep and "b.py" in rep and "c.py" in rep and "Core" in rep
    # leaf file with no dependents -> empty report
    assert impact_check_report(_onto(), ["pkg/c.py"]) == ""


def test_compute_hotspots():
    commits = [["pkg/a.py", "pkg/b.py"], ["pkg/a.py", "pkg/b.py"], ["pkg/a.py"]]
    churn, cochange = compute_hotspots(commits)
    assert churn["pkg/a.py"] == 3 and churn["pkg/b.py"] == 2
    assert cochange[("pkg/a.py", "pkg/b.py")] == 2


def test_hotspots_json_structural_flag():
    onto = _onto()
    churn = Counter({"pkg/a.py": 3, "pkg/b.py": 2, "pkg/c.py": 2})
    cochange = Counter({("pkg/a.py", "pkg/b.py"): 2, ("pkg/a.py", "pkg/c.py"): 3})
    hs = hotspots_json(onto, churn, cochange, "6 months ago", 3)
    assert hs["files"]["pkg/a.py"] == 3
    assert hs["components"]["comp_x"] == 7
    by_pair = {(p["a"], p["b"]): p for p in hs["co_change"]}
    assert by_pair[("pkg/a.py", "pkg/b.py")]["structural"] is True   # f_b -> f_a edge exists
    assert by_pair[("pkg/a.py", "pkg/c.py")]["structural"] is False  # hidden coupling


def test_diff():
    old = _onto(rels=[{"source": "comp_x", "target": "ext", "relation": "calls", "count": 3}],
                impact={"comp_x": {"direct": [], "indirect": [], "total_files": 3}})
    new = _onto(
        extra_files=[{"id": "f_d", "type": "File", "name": "d.py", "path": "pkg/d.py"}],
        rels=[{"source": "comp_x", "target": "ext", "relation": "calls", "count": 7}],
        impact={"comp_x": {"direct": [], "indirect": [], "total_files": 4}},
    )
    d = diff_ontology(old, new)
    assert d["added_files"] == ["pkg/d.py"] and d["removed_files"] == []
    assert d["added_components"] == [] and d["removed_components"] == []
    rc = d["relationship_changes"][0]
    assert rc["old"] == 3 and rc["new"] == 7 and rc["relation"] == "calls"
    ic = d["impact_changes"][0]
    assert ic["old_total_files"] == 3 and ic["new_total_files"] == 4
    text = format_diff(d)
    assert "pkg/d.py" in text and "3 -> 7" in text
    # identical ontologies -> clean report
    same = diff_ontology(old, old)
    assert "no structural changes" in format_diff(same)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok {fn.__name__}")
    print(f"all {len(tests)} tests passed")
