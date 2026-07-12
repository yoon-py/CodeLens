"""Git-aware insights over a built ontology.

impact-check : blast radius of the files you are about to commit (pre-commit)
hotspots     : churn + co-change from git history, joined onto the ontology

Both are read-only over ontology.json + `git` output - no graph rebuild, no LLM.
Git history is fact, not inference, so everything here is EXTRACTED-grade.
"""
from __future__ import annotations

import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

# skip mega-commits (formatting sweeps, vendored drops): co-change pairs from
# a 100-file commit say nothing about coupling
DEFAULT_MAX_COMMIT_FILES = 30


# ---------- ontology access ----------

def collect_files(onto: dict) -> list[dict]:
    """Flatten the tree: every File node with its owning component/feature."""
    out: list[dict] = []

    def walk(node: dict, comp: dict | None, feat: dict | None) -> None:
        f = node if node.get("type") == "Feature" else feat
        c = node if node.get("type") == "Component" else comp
        if node.get("type") == "File":
            out.append({"node": node, "component": c, "feature": f})
        for ch in node.get("children", []):
            walk(ch, c, f)

    walk(onto, None, None)
    return out


def match_paths(git_paths: list[str], files: list[dict]) -> dict[str, dict]:
    """git path -> file entry. Ontology paths are prefix-stripped, git paths are
    repo-relative, so match exact first, then unambiguous suffix."""
    by_path = {f["node"]["path"]: f for f in files if f["node"].get("path")}
    out = {}
    for gp in git_paths:
        if gp in by_path:
            out[gp] = by_path[gp]
            continue
        hits = [f for p, f in by_path.items() if gp.endswith("/" + p)]
        if len(hits) == 1:
            out[gp] = hits[0]
    return out


def blast_radius(file_rels: list[dict], file_id: str) -> tuple[list[str], list[str]]:
    """Reverse BFS: which files break if this one changes? -> (direct, indirect)"""
    dependents: dict[str, list[str]] = defaultdict(list)
    for r in file_rels:
        dependents[r["target"]].append(r["source"])
    direct = sorted(set(dependents.get(file_id, ())))
    seen = {file_id, *direct}
    indirect: set[str] = set()
    frontier = list(direct)
    while frontier:
        nxt = []
        for f in frontier:
            for dep in dependents.get(f, ()):
                if dep not in seen:
                    seen.add(dep)
                    indirect.add(dep)
                    nxt.append(dep)
        frontier = nxt
    return direct, sorted(indirect)


# ---------- impact-check ----------

def impact_check_report(onto: dict, changed_paths: list[str]) -> str:
    files = collect_files(onto)
    name_of = {f["node"]["id"]: f["node"]["name"] for f in files}
    comp_of = {f["node"]["id"]: (f["component"] or {}).get("name", "?") for f in files}
    matched = match_paths(changed_paths, files)
    frs = onto.get("file_relationships", [])

    lines = []
    for gp in changed_paths:
        entry = matched.get(gp)
        if entry is None:
            continue  # file not in the ontology (docs, configs, new files)
        fid = entry["node"]["id"]
        direct, indirect = blast_radius(frs, fid)
        if not direct and not indirect:
            continue
        comps = sorted({comp_of[i] for i in direct + indirect if i in comp_of})
        lines.append(f"  {entry['node']['name']}  ({(entry['component'] or {}).get('name', '?')})")
        if direct:
            lines.append(f"    direct   -> {', '.join(name_of.get(i, i) for i in direct)}")
        if indirect:
            lines.append(f"    indirect -> {', '.join(name_of.get(i, i) for i in indirect)}")
        if comps:
            lines.append(f"    components affected: {', '.join(comps)}")
    if not lines:
        return ""
    header = "codelens impact-check (informational, never blocks):"
    return "\n".join([header, *lines])


def git_staged(repo: str) -> list[str]:
    out = subprocess.run(
        ["git", "-C", repo, "diff", "--cached", "--name-only"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [l for l in out.splitlines() if l.strip()]


HOOK_BODY = """#!/bin/sh
# installed by `codelens impact-check --install-hook` - informational only
codelens impact-check --ontology {ontology} --repo "$(git rev-parse --show-toplevel)" || true
exit 0
"""


def install_hook(repo: str, ontology: str) -> str:
    hooks = Path(repo) / ".git" / "hooks"
    if not hooks.is_dir():
        raise SystemExit(f"{hooks} not found - is {repo} a git repository?")
    target = hooks / "pre-commit"
    body = HOOK_BODY.format(ontology=Path(ontology).resolve())
    if target.exists() and "codelens impact-check" not in target.read_text(encoding="utf-8"):
        raise SystemExit(
            f"{target} already exists and is not ours - append this line yourself:\n"
            f"  codelens impact-check --ontology {Path(ontology).resolve()}"
        )
    target.write_text(body, encoding="utf-8")
    target.chmod(0o755)
    return str(target)


# ---------- hotspots ----------

def parse_git_commits(
    repo: str, since: str, max_commit_files: int = DEFAULT_MAX_COMMIT_FILES,
) -> list[list[str]]:
    """git log -> list of per-commit changed-file lists (mega-commits skipped)."""
    out = subprocess.run(
        ["git", "-C", repo, "log", "--no-merges", f"--since={since}",
         "--name-only", "--pretty=format:@@%H"],
        capture_output=True, text=True, check=True,
    ).stdout
    commits: list[list[str]] = []
    cur: list[str] = []
    for line in out.splitlines():
        if line.startswith("@@"):
            if cur:
                commits.append(cur)
            cur = []
        elif line.strip():
            cur.append(line.strip())
    if cur:
        commits.append(cur)
    return [c for c in commits if len(c) <= max_commit_files]


def compute_hotspots(commits: list[list[str]]) -> tuple[Counter, Counter]:
    """-> (churn per path, co-change count per unordered path pair)"""
    churn: Counter[str] = Counter()
    cochange: Counter[tuple[str, str]] = Counter()
    for paths in commits:
        uniq = sorted(set(paths))
        churn.update(uniq)
        for a, b in combinations(uniq, 2):
            cochange[(a, b)] += 1
    return churn, cochange


def hotspots_json(
    onto: dict, churn: Counter, cochange: Counter,
    since: str, commits_scanned: int, top_pairs: int = 100,
) -> dict:
    files = collect_files(onto)
    matched = match_paths(list(churn), files)

    file_churn: dict[str, int] = {}       # ontology File.path -> commits
    comp_churn: Counter[str] = Counter()  # component id -> commits
    id_by_git: dict[str, str] = {}
    for gp, entry in matched.items():
        p = entry["node"]["path"]
        file_churn[p] = file_churn.get(p, 0) + churn[gp]
        if entry["component"]:
            comp_churn[entry["component"]["id"]] += churn[gp]
        id_by_git[gp] = entry["node"]["id"]

    # structural = an extracted file relationship exists in either direction;
    # a frequently co-changed pair with NO structural link is hidden coupling
    linked = set()
    for r in onto.get("file_relationships", []):
        linked.add((r["source"], r["target"]))
        linked.add((r["target"], r["source"]))

    pairs = []
    for (a, b), n in cochange.most_common():
        if n < 2 or a not in matched or b not in matched:
            continue
        pairs.append({
            "a": matched[a]["node"]["path"],
            "b": matched[b]["node"]["path"],
            "count": n,
            "structural": (id_by_git[a], id_by_git[b]) in linked,
        })
        if len(pairs) >= top_pairs:
            break

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "since": since,
        "commits_scanned": commits_scanned,
        "files": file_churn,
        "components": dict(comp_churn),
        "co_change": pairs,
    }


def format_hotspots(hs: dict, onto: dict) -> str:
    comp_name = {}

    def walk(n):
        if n.get("type") == "Component":
            comp_name[n["id"]] = n["name"]
        for c in n.get("children", []):
            walk(c)
    walk(onto)

    lines = [f"hotspots since '{hs['since']}' - {hs['commits_scanned']} commits scanned"]
    lines.append("\ncomponent churn:")
    for cid, n in sorted(hs["components"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {n:4d}  {comp_name.get(cid, cid)}")
    lines.append("\ntop files:")
    for p, n in sorted(hs["files"].items(), key=lambda kv: -kv[1])[:10]:
        lines.append(f"  {n:4d}  {p}")
    hidden = [p for p in hs["co_change"] if not p["structural"]]
    if hidden:
        lines.append("\nhidden coupling (co-change with no extracted relationship):")
        for p in hidden[:10]:
            lines.append(f"  {p['count']:3d}x  {p['a']}  <->  {p['b']}")
    return "\n".join(lines)
