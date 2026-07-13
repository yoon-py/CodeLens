"""Answer-quality benchmark for get_context: does it point at the file a real
commit actually touched?

Ground truth is mined from git history, not hand-picked: single/double-file
commits under the product source dir become (task, correct_files) pairs,
task = the commit message with gitmoji/PR-number stripped. This avoids the
self-selection bias of choosing tasks by hand (see bench_context.py's
docstring for the same caveat on the token-count benchmark).

Usage:
  python examples/bench_accuracy.py <repo_root> <ontology.json> [--src-prefix fastapi/] [-n 60]
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lensme.mcp import Onto, tool_get_context

SKIP_WORDS = ("release notes", "bump ", "release version", "merge pull", "merge branch")
GITMOJI = re.compile(r"^[^\w(]+\s*")  # leading emoji/symbols before the text
PR_TAG = re.compile(r"\s*\(#\d+\)\s*$")


def mine_commits(repo: Path, src_prefix: str, limit: int) -> list[dict]:
    log = subprocess.run(
        ["git", "log", "-2000", "--name-only", "--pretty=format:COMMIT|%H|%s", "--", src_prefix],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout
    commits, cur = [], None

    def flush(c):
        if not c:
            return
        src = [f for f in c["files"] if f.startswith(src_prefix) and f.endswith(".py")]
        msg_l = c["msg"].lower()
        if 1 <= len(src) <= 2 and len(c["msg"]) > 15 and not any(w in msg_l for w in SKIP_WORDS):
            c["src"] = src
            commits.append(c)

    for line in log.splitlines():
        if line.startswith("COMMIT|"):
            flush(cur)
            _, h, msg = line.split("|", 2)
            cur = {"hash": h, "msg": msg, "files": []}
        elif line.strip() and cur is not None:
            cur["files"].append(line.strip())
    flush(cur)
    return commits[:limit]


def clean_task(msg: str) -> str:
    return PR_TAG.sub("", GITMOJI.sub("", msg)).strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_root")
    ap.add_argument("ontology_path")
    ap.add_argument("--src-prefix", default="fastapi/")
    ap.add_argument("-n", type=int, default=60)
    args = ap.parse_args()

    repo = Path(args.repo_root)
    commits = mine_commits(repo, args.src_prefix, args.n)
    onto = Onto(Path(args.ontology_path))

    hit1 = hit3 = hit_any = comp_hit = errors = 0
    misses = []
    for c in commits:
        task = clean_task(c["msg"])
        out = tool_get_context(onto, {"task": task})
        if "error" in out:
            errors += 1
            misses.append((task, c["src"], "ERROR: " + out["error"]))
            continue
        returned = [f["path"] for f in out["files"]]
        correct = set(c["src"])
        is1 = bool(correct & set(out["read_first"][:1]))
        is3 = bool(correct & set(out["read_first"]))
        isany = bool(correct & set(returned))
        hit1 += is1
        hit3 += is3
        hit_any += isany
        # component-level: is the correct file at least under the same top-level
        # dir as the resolved component (looser signal than an exact file hit)?
        comp_dir = out["component"].get("id", "")
        if any(comp_dir.split("_")[-1] in f for f in correct):
            comp_hit += 1
        if not isany:
            misses.append((task, c["src"], f"got: {returned[:3]}"))

    n = len(commits)
    print(f"ground truth: {n} commits under {args.src_prefix} (git history, not hand-picked)\n")
    print(f"{'metric':<45}{'hit rate':>10}")
    print(f"{'read_first[0] == changed file':<45}{hit1}/{n} = {100*hit1/n:.0f}%")
    print(f"{'changed file in read_first (top-3)':<45}{hit3}/{n} = {100*hit3/n:.0f}%")
    print(f"{'changed file anywhere in returned files':<45}{hit_any}/{n} = {100*hit_any/n:.0f}%")
    print(f"{'errors (no component matched)':<45}{errors}/{n} = {100*errors/n:.0f}%")
    print(f"\n{len(misses)} misses (task -> expected -> got):")
    for task, expected, got in misses[:15]:
        print(f"  {task[:55]:<57} expected {expected} | {got}")


if __name__ == "__main__":
    main()
