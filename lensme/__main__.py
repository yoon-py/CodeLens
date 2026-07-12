"""CLI: lensme scan | build | sync | serve | report | path | explain | symbols | tree | mcp | impact-check | hotspots | diff | merge."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import webbrowser
from pathlib import Path

from .build import build_ontology, load_enrichment, symbol_digest

CONFIG_NAME = ".lensme_config.json"
SYMCACHE_NAME = ".lensme_symbols_cache.json"


def _load_graph(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        sys.exit(f"graph not found: {p} (run graphify first)")
    return json.loads(p.read_text(encoding="utf-8"))


def print_tree(node: dict, indent: int = 0, file=sys.stdout) -> None:
    stats = node.get("stats")
    extra = f" {stats}" if stats else ""
    conf = node.get("confidence", "")
    print("  " * indent + f"[{node.get('type')}] {node.get('name')} ({conf}){extra}", file=file)
    for c in node.get("children", []):
        print_tree(c, indent + 1, file=file)


def _config_path(graph_path: str) -> Path:
    return Path(graph_path).parent / CONFIG_NAME


def _run_build(cfg: dict, quiet: bool = False) -> dict:
    onto = build_ontology(
        _load_graph(cfg["graph"]),
        prefix=cfg.get("prefix", ""),
        root=cfg.get("root", "."),
        product_name=cfg.get("name", "project"),
        product_description=cfg.get("description", ""),
        enrichment=load_enrichment(cfg.get("enrichment")),
        source_graph=cfg["graph"],
    )
    out = Path(cfg["output"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(onto, indent=2, ensure_ascii=False), encoding="utf-8")
    if not quiet:
        print(f"wrote {out}")
        print(json.dumps(onto["stats"]))
        if onto["meta"].get("enrichment_recommended"):
            print(
                "note: flat source tree - path heuristics can't infer features here.\n"
                "      run `lensme symbols` and add agent enrichment (docs/enrichment-spec.md)"
            )
    return onto


def cmd_build(args) -> None:
    cfg = {
        "graph": args.graph, "prefix": args.prefix, "root": args.root,
        "name": args.name, "description": args.description,
        "enrichment": args.enrichment, "output": args.output,
    }
    onto = _run_build(cfg)
    # persist config so `lensme sync` / `lensme serve --watch` can rebuild
    _config_path(args.graph).write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    if args.tree:
        print_tree(onto)


def _load_config(graph_path: str) -> dict:
    p = _config_path(graph_path)
    if not p.exists():
        sys.exit(f"no saved config at {p} - run `lensme build` once first")
    return json.loads(p.read_text(encoding="utf-8"))


def cmd_sync(args) -> None:
    cfg = _load_config(args.graph)
    if not args.watch:
        _run_build(cfg)
        return
    graph = Path(cfg["graph"])
    print(f"watching {graph} (Ctrl-C to stop)")
    last = graph.stat().st_mtime if graph.exists() else 0.0
    _run_build(cfg)
    try:
        while True:
            time.sleep(args.interval)
            mtime = graph.stat().st_mtime if graph.exists() else 0.0
            if mtime != last:
                last = mtime
                print(f"[{time.strftime('%H:%M:%S')}] graph.json changed - rebuilding ontology")
                try:
                    _run_build(cfg)
                except SystemExit as e:  # graph mid-write; retry next tick
                    print(f"  skipped: {e}")
    except KeyboardInterrupt:
        print("\nstopped")


def _ui_dist() -> Path | None:
    """Locate the built UI. Checks packaged assets first, then the repo layout."""
    for cand in (
        Path(__file__).parent / "ui_dist",          # packaged into the wheel
        Path(__file__).parent.parent / "ui" / "dist",  # repo checkout
    ):
        if (cand / "index.html").exists():
            return cand
    return None


def cmd_serve(args) -> None:
    import functools
    import http.server
    import threading

    dist = _ui_dist()
    if dist is None:
        sys.exit("UI not built - run `npm run build` in lensme/ui first")
    onto_path = Path(args.ontology)
    if not onto_path.exists():
        sys.exit(f"{onto_path} not found - run `lensme build` first")
    graph_html = onto_path.parent / "graph.html"  # graphify's raw code graph (Code Graph tab)
    hotspots_path = onto_path.parent / "hotspots.json"  # optional: `lensme hotspots` output
    # injected when serving graph.html: ?q=<label> focuses the matching node using
    # the globals graphify's page already exposes (RAW_NODES, focusNode)
    focus_loader = b"""<script>
(function () {
  var q = new URLSearchParams(location.search).get('q');
  if (!q || typeof RAW_NODES === 'undefined' || typeof focusNode !== 'function') return;
  var needle = q.toLowerCase();
  var hit = RAW_NODES.find(function (n) { return n.label.toLowerCase() === needle; })
         || RAW_NODES.find(function (n) { return n.label.toLowerCase().indexOf(needle) !== -1; });
  if (!hit) return;
  var go = function () { try { focusNode(hit.id); } catch (e) {} };
  setTimeout(go, 400);      // during stabilization
  setTimeout(go, 2500);     // again once physics settles
})();
</script></body>"""

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(dist), **kw)

        def _serve_bytes(self, body: bytes, ctype: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = self.path.split("?")[0]
            if route == "/ontology.json":
                self._serve_bytes(onto_path.read_bytes(), "application/json")
                return
            if route == "/hotspots.json":
                if hotspots_path.exists():
                    self._serve_bytes(hotspots_path.read_bytes(), "application/json")
                else:
                    self.send_error(404, "no hotspots.json - run `lensme hotspots` first")
                return
            if route == "/graph.html":
                if graph_html.exists():
                    body = graph_html.read_bytes().replace(b"</body>", focus_loader, 1)
                    self._serve_bytes(body, "text/html; charset=utf-8")
                else:
                    self.send_error(404, "graph.html not found - run graphify export first")
                return
            super().do_GET()

        def log_message(self, fmt, *a):  # quiet
            pass

    if args.watch:
        cfg = _load_config(args.graph)
        graph = Path(cfg["graph"])

        def watcher():
            last = graph.stat().st_mtime if graph.exists() else 0.0
            while True:
                time.sleep(args.interval)
                mtime = graph.stat().st_mtime if graph.exists() else 0.0
                if mtime != last:
                    last = mtime
                    print(f"[{time.strftime('%H:%M:%S')}] graph.json changed - rebuilding ontology")
                    try:
                        _run_build(cfg, quiet=True)
                    except SystemExit:
                        pass

        threading.Thread(target=watcher, daemon=True).start()

    url = f"http://127.0.0.1:{args.port}"
    print(f"serving {dist.name} + {onto_path} at {url}" + (" (watching graph.json)" if args.watch else ""))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    if not args.no_open:
        threading.Timer(0.3, functools.partial(webbrowser.open, url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


def cmd_scan(args) -> None:
    """The single entry point: extract (graphify) + build + serve in one command."""
    import shutil
    import subprocess

    root = Path(args.path).resolve()
    graph = root / "graphify-out" / "graph.json"
    gf = shutil.which("graphify")
    if not args.skip_extract and gf:
        print("extracting code graph (graphify update)...")
        subprocess.run([gf, "update", str(root)], check=True)
    elif not graph.exists():
        sys.exit(
            "graphify not found and no existing graph.json.\n"
            "install graphify first: https://github.com/Graphify-Labs/graphify"
        )
    else:
        print(f"graphify not run - using existing {graph}")

    cfg = {
        "graph": str(graph), "prefix": "", "root": str(root),
        "name": args.name or root.name, "description": "",
        "enrichment": None, "output": str(graph.parent / "ontology.json"),
    }
    _run_build(cfg)
    _config_path(str(graph)).write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    args.graph, args.ontology = str(graph), cfg["output"]
    cmd_serve(args)


def cmd_report(args) -> None:
    from .report import generate_report

    onto = json.loads(Path(args.ontology).read_text(encoding="utf-8"))
    hotspots_path = Path(args.ontology).parent / "hotspots.json"
    hotspots = (json.loads(hotspots_path.read_text(encoding="utf-8"))
                if hotspots_path.exists() else None)
    md = generate_report(onto, hotspots)
    if args.output == "-":
        print(md)
    else:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"wrote {args.output}")


def _cli_onto(ontology: str):
    from .mcp import Onto

    p = Path(ontology)
    if not p.exists():
        sys.exit(f"{p} not found - run `lensme build` first")
    return Onto(p)


def cmd_path(args) -> None:
    from .mcp import tool_path

    out = tool_path(_cli_onto(args.ontology), {"from": args.src, "to": args.dst})
    if out.get("error"):
        sys.exit(out["error"])
    if out.get("path") is None:
        print(f"{out['from']} -/-> {out['to']}: no relationship path ({out['level']} level)")
        return
    print(f"{out['from']} -> {out['to']} ({out['level']} level, {out['hops']} hops)")
    for h in out["path"]:
        print(f"  {h['from']} -[{h['relation']}]-> {h['to']}")


def cmd_explain(args) -> None:
    from .mcp import tool_explain

    out = tool_explain(_cli_onto(args.ontology), {"name": args.name})
    if out.get("error"):
        sys.exit(out["error"])
    json.dump(out, sys.stdout, indent=2, ensure_ascii=False)
    print()


def cmd_merge(args) -> None:
    from .merge import merge_ontologies

    ontos = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.ontologies]
    system = merge_ontologies(ontos, args.name, args.description)
    Path(args.output).write_text(
        json.dumps(system, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"wrote {args.output}")
    print(json.dumps(system["stats"]))
    for s in system["shared_externals"]:
        print(f"  shared: {s['name']} <- {', '.join(s['products'])}")
    if args.tree:
        print_tree(system)


def cmd_symbols(args) -> None:
    digest = symbol_digest(_load_graph(args.graph), args.prefix)
    cache_path = Path(args.graph).parent / SYMCACHE_NAME
    if args.changed:
        old = {}
        if cache_path.exists():
            old = json.loads(cache_path.read_text(encoding="utf-8")).get(args.prefix, {})
        digest = {
            rel: syms for rel, syms in digest.items()
            if hashlib.sha1(json.dumps(syms).encode()).hexdigest() != old.get(rel)
        }
    # always refresh the cache with current hashes
    full = symbol_digest(_load_graph(args.graph), args.prefix) if args.changed else digest
    cache = {}
    if cache_path.exists():
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    cache[args.prefix] = {
        rel: hashlib.sha1(json.dumps(syms).encode()).hexdigest() for rel, syms in full.items()
    }
    cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
    json.dump(digest, sys.stdout, indent=2, ensure_ascii=False)
    print()


def cmd_impact_check(args) -> None:
    from .insights import git_staged, impact_check_report, install_hook

    if args.install_hook:
        print(f"installed {install_hook(args.repo, args.ontology)}")
        return
    onto = json.loads(Path(args.ontology).read_text(encoding="utf-8"))
    changed = args.files if args.files is not None else git_staged(args.repo)
    report = impact_check_report(onto, changed)
    if report:
        print(report)
    # informational only: always exit 0


def cmd_hotspots(args) -> None:
    from .insights import compute_hotspots, format_hotspots, hotspots_json, parse_git_commits

    onto = json.loads(Path(args.ontology).read_text(encoding="utf-8"))
    commits = parse_git_commits(args.repo, args.since, args.max_commit_files)
    churn, cochange = compute_hotspots(commits)
    hs = hotspots_json(onto, churn, cochange, args.since, len(commits))
    out = Path(args.output) if args.output else Path(args.ontology).parent / "hotspots.json"
    out.write_text(json.dumps(hs, indent=2, ensure_ascii=False), encoding="utf-8")
    print(format_hotspots(hs, onto))
    print(f"\nwrote {out}")


def cmd_diff(args) -> None:
    from .diff import diff_ontology, format_diff

    old = json.loads(Path(args.old).read_text(encoding="utf-8"))
    new = json.loads(Path(args.new).read_text(encoding="utf-8"))
    d = diff_ontology(old, new)
    print(json.dumps(d, indent=2, ensure_ascii=False) if args.json else format_diff(d))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="lensme", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--graph", default="graphify-out/graph.json", help="path to graphify graph.json")

    sc = sub.add_parser("scan", help="one command: extract (graphify) + build + serve")
    sc.add_argument("path", nargs="?", default=".", help="repo to map (default: cwd)")
    sc.add_argument("--name", default=None, help="product name (default: directory name)")
    sc.add_argument("--port", type=int, default=4173)
    sc.add_argument("--no-open", action="store_true", help="do not open a browser")
    sc.add_argument("--skip-extract", action="store_true", help="reuse existing graph.json")
    sc.add_argument("--watch", action="store_true", help="rebuild when graph.json changes")
    sc.add_argument("--interval", type=float, default=2.0)
    sc.set_defaults(fn=cmd_scan)

    b = sub.add_parser("build", parents=[common], help="build ontology.json from graph.json")
    b.add_argument("--prefix", default="", help="source_file prefix to scope to (e.g. 'myproj/')")
    b.add_argument("--root", default=".", help="repo root for reading source files")
    b.add_argument("--name", default="project", help="product name")
    b.add_argument("--description", default="", help="product description")
    b.add_argument("--enrichment", default=None, help="agent-authored enrichment JSON (see symbols cmd)")
    b.add_argument("-o", "--output", default="graphify-out/ontology.json")
    b.add_argument("--tree", action="store_true", help="print the tree after writing")
    b.set_defaults(fn=cmd_build)

    sy = sub.add_parser("sync", parents=[common],
                        help="rebuild ontology using the config saved by the last `build`")
    sy.add_argument("--watch", action="store_true", help="poll graph.json and rebuild on change")
    sy.add_argument("--interval", type=float, default=2.0, help="watch poll interval seconds")
    sy.set_defaults(fn=cmd_sync)

    sv = sub.add_parser("serve", parents=[common], help="serve the UI + ontology.json locally")
    sv.add_argument("--ontology", default="graphify-out/ontology.json")
    sv.add_argument("--port", type=int, default=4173)
    sv.add_argument("--no-open", action="store_true", help="do not open a browser")
    sv.add_argument("--watch", action="store_true",
                    help="also rebuild ontology when graph.json changes (uses saved config)")
    sv.add_argument("--interval", type=float, default=2.0)
    sv.set_defaults(fn=cmd_serve)

    s = sub.add_parser("symbols", parents=[common],
                       help="emit per-file symbol digest for agent enrichment authoring")
    s.add_argument("--prefix", default="", help="source_file prefix to scope to")
    s.add_argument("--changed", action="store_true",
                   help="only files whose symbols changed since last run (hash cache)")
    s.set_defaults(fn=cmd_symbols)

    t = sub.add_parser("tree", help="pretty-print an existing ontology.json")
    t.add_argument("ontology", help="path to ontology.json")
    t.set_defaults(fn=lambda a: print_tree(json.loads(Path(a.ontology).read_text(encoding="utf-8"))))

    m = sub.add_parser("mcp", help="MCP server over stdio - agents query the ontology")
    m.add_argument("--ontology", default="graphify-out/ontology.json")
    m.set_defaults(fn=lambda a: __import__("lensme.mcp", fromlist=["serve"]).serve(a.ontology))

    rp = sub.add_parser("report", help="generate ARCHITECTURE.md from the ontology")
    rp.add_argument("--ontology", default="graphify-out/ontology.json")
    rp.add_argument("-o", "--output", default="ARCHITECTURE.md", help="'-' for stdout")
    rp.set_defaults(fn=cmd_report)

    pa = sub.add_parser("path", help="shortest relationship path between two nodes")
    pa.add_argument("src", help="component/file name or path")
    pa.add_argument("dst", help="component/file name or path")
    pa.add_argument("--ontology", default="graphify-out/ontology.json")
    pa.set_defaults(fn=cmd_path)

    ex = sub.add_parser("explain", help="everything known about one node")
    ex.add_argument("name", help="component/file name or path")
    ex.add_argument("--ontology", default="graphify-out/ontology.json")
    ex.set_defaults(fn=cmd_explain)

    mg = sub.add_parser("merge", help="merge per-repo ontologies into a System-level view")
    mg.add_argument("ontologies", nargs="+", help="ontology.json files to merge")
    mg.add_argument("--name", default="system", help="system name")
    mg.add_argument("--description", default="")
    mg.add_argument("-o", "--output", default="system-ontology.json")
    mg.add_argument("--tree", action="store_true", help="print the merged tree")
    mg.set_defaults(fn=cmd_merge)

    ic = sub.add_parser("impact-check",
                        help="blast radius of staged files (pre-commit, informational)")
    ic.add_argument("--ontology", default="graphify-out/ontology.json")
    ic.add_argument("--repo", default=".", help="git repository to read staged files from")
    ic.add_argument("--files", nargs="*", default=None,
                    help="check these paths instead of the staged set")
    ic.add_argument("--install-hook", action="store_true",
                    help="write a non-blocking pre-commit hook into --repo")
    ic.set_defaults(fn=cmd_impact_check)

    hs = sub.add_parser("hotspots",
                        help="churn + co-change from git history, joined onto the ontology")
    hs.add_argument("--ontology", default="graphify-out/ontology.json")
    hs.add_argument("--repo", default=".", help="git repository to read history from")
    hs.add_argument("--since", default="6 months ago")
    hs.add_argument("--max-commit-files", type=int, default=30,
                    help="skip commits touching more files than this (noise)")
    hs.add_argument("-o", "--output", default=None,
                    help="output path (default: hotspots.json next to the ontology)")
    hs.set_defaults(fn=cmd_hotspots)

    df = sub.add_parser("diff", help="structural diff between two ontology.json files")
    df.add_argument("old")
    df.add_argument("new")
    df.add_argument("--json", action="store_true", help="machine-readable output")
    df.set_defaults(fn=cmd_diff)

    args = ap.parse_args(argv)
    try:
        args.fn(args)
    except BrokenPipeError:  # e.g. `lensme tree ... | head`
        sys.stderr.close()


if __name__ == "__main__":
    main()
