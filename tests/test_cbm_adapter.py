"""Self-check for lensme.cbm_adapter - maps cbm query output to lensme's graph
contract. Hermetic: cbm's `_query` is stubbed with the exact row shapes
measured against a real cbm index (no binary, no network needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import lensme.cbm_adapter as cbm
from lensme.build import build_ontology

# Rows exactly as `cbm cli query_graph` returns them (columns match the RETURN
# clause order in cbm_graph). Two files, cross-file call + import.
_FILE_ROWS = [
    ["643", "oauth2.py", "fastapi/security/oauth2.py"],
    ["700", "exceptions.py", "fastapi/exceptions.py"],
    ["", "noname.py", ""],  # missing path -> must be skipped
]
_SYMBOL_ROWS = [
    ["800", "OAuth2", "fastapi/security/oauth2.py", "26"],
    ["801", "__call__", "fastapi/security/oauth2.py", "40"],
    ["900", "HTTPException", "fastapi/exceptions.py", "10"],
    ["999", "orphan", "", "5"],  # missing path -> skipped
]
_DEFINES = [["643", "800"], ["643", "801"], ["700", "900"]]
_CALLS = [["801", "900"]]      # oauth2.__call__ calls exceptions.HTTPException
_IMPORTS = [["643", "900"]]    # oauth2.py imports HTTPException (File -> symbol)
_USAGE = [["801", "12345"]]    # dangles (target not emitted) -> lensme drops it
_INHERITS: list = []


def _fake_query(bin_, project, cypher):
    if "f:File)" in cypher and "DEFINES" not in cypher:
        return _FILE_ROWS
    if "DEFINES" in cypher:
        return _DEFINES
    if "s:Function|Method|Class)" in cypher:
        return _SYMBOL_ROWS
    if ":CALLS]" in cypher:
        return _CALLS
    if ":IMPORTS]" in cypher:
        return _IMPORTS
    if ":USAGE]" in cypher:
        return _USAGE
    if ":INHERITS]" in cypher:
        return _INHERITS
    raise AssertionError(f"unexpected query: {cypher}")


def _patched():
    cbm._query = _fake_query
    cbm._cbm_bin = lambda override=None: "fake-cbm"


def test_cbm_graph_shape():
    _patched()
    g = cbm.cbm_graph("proj")
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"643", "700", "800", "801", "900"}, ids  # empty-path nodes dropped
    oauth = next(n for n in g["nodes"] if n["id"] == "643")
    assert oauth["label"] == "oauth2.py" and oauth["_origin"] == "ast"
    sym = next(n for n in g["nodes"] if n["id"] == "800")
    assert sym["source_location"] == "L26"
    rels = {(l["source"], l["target"], l["relation"]) for l in g["links"]}
    assert ("643", "800", "contains") in rels
    assert ("801", "900", "calls") in rels
    assert ("643", "900", "imports") in rels
    assert ("801", "12345", "references") in rels  # emitted; lensme drops it later


def test_cbm_graph_builds_ontology():
    _patched()
    g = cbm.cbm_graph("proj")
    onto = build_ontology(g, prefix="fastapi/", product_name="fastapi-cbm")
    # both files show up
    paths = []

    def walk(n):
        if n.get("type") == "File":
            paths.append(n["path"])
        for c in n.get("children", []):
            walk(c)

    walk(onto)
    assert "security/oauth2.py" in paths and "exceptions.py" in paths
    # cross-file dependency survived, rolled up to FILE ids (oauth2 643 -> exceptions 700):
    # the CALLS/IMPORTS edges pointed at symbol 900, which lives in file 700
    frs = {(r["source"], r["target"]) for r in onto["file_relationships"]}
    assert ("643", "700") in frs, frs
    # the dangling USAGE edge (target 12345 never emitted) was dropped
    assert all(r["target"] != "12345" for r in onto["file_relationships"])
    # symbols attached to their file
    oauth_file = next(n for n in _iter(onto) if n.get("type") == "File"
                      and n["path"] == "security/oauth2.py")
    assert any(s["name"] == "OAuth2" for s in oauth_file["symbols"])


def _iter(n):
    yield n
    for c in n.get("children", []):
        yield from _iter(c)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in tests:
        fn()
        print(f"ok {fn.__name__}")
    print(f"all {len(tests)} tests passed")
