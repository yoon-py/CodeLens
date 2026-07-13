"""Assembly vs regeneration: token cost of reusing a registry component
versus having an agent regenerate the same functionality.

Methodology (honest limits stated):
- regeneration cost = chars/4 of the component's source. This is what the
  agent must EMIT to recreate the functionality, and it is a LOWER bound:
  real from-scratch sessions also pay input tokens for exploration, plus
  regenerate-on-bug iterations, and the result carries none of the original's
  battle-testing.
- assembly cost = everything that actually enters the agent's context in the
  assembly flow: the search_components reply (metadata only) + the
  install_component reply + WIRING.md. The vendored source itself never
  enters context - that is the whole point.
- glue code the agent still writes is excluded from BOTH sides (it is the
  same work either way).

Usage: python examples/bench_assembly.py [component ...]
       (default: every component in the registry)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from lensme.registry import (
    DEFAULT_REGISTRY, _latest_manifests, format_wiring, load_component,
    manifest_summary, wiring_plan,
)


def toks(text: str) -> int:
    return len(text) // 4


def main() -> None:
    registry = DEFAULT_REGISTRY
    names = sys.argv[1:] or [m["name"] for m in _latest_manifests(Path(registry))]
    if not names:
        sys.exit("registry is empty - run `lensme extract` first")

    print(f"{'component':<22}{'regenerate (emit)':>18}{'assemble (context)':>20}{'saved':>8}")
    total_r = total_a = 0
    for name in names:
        manifest, vdir = load_component(registry, name)
        src_tokens = sum(
            toks(f.read_text(encoding="utf-8", errors="ignore"))
            for f in (vdir / "src").rglob("*") if f.is_file()
        )
        plan = wiring_plan(manifest, Path("."), None)
        assembly_tokens = (
            toks(json.dumps(manifest_summary(manifest)))     # search reply
            + toks(json.dumps(plan))                          # install reply
            + toks(format_wiring(manifest, plan))             # WIRING.md
        )
        total_r += src_tokens
        total_a += assembly_tokens
        pct = 100 * (src_tokens - assembly_tokens) / src_tokens if src_tokens else 0
        print(f"{name:<22}{src_tokens:>18,}{assembly_tokens:>20,}{pct:>7.0f}%")
    pct = 100 * (total_r - total_a) / total_r if total_r else 0
    print(f"{'TOTAL':<22}{total_r:>18,}{total_a:>20,}{pct:>7.0f}%")
    print("\nregeneration side is a LOWER bound (no exploration/iteration cost included);")
    print("glue code is excluded from both sides. chars/4 estimate, not tokenizer-exact.")


if __name__ == "__main__":
    main()
