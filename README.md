# lensme

C4-style ontology layer on top of [graphify](https://github.com/Graphify-Labs/graphify)'s `graph.json`.

![FastAPI mapped by lensme](docs/assets/map-fastapi.png)

*[FastAPI](https://github.com/fastapi/fastapi) (2,718 files) mapped with zero
config: source features first, docs/examples/tests sidelined into supporting
bands, real externals (starlette, pydantic) from `pyproject.toml`.
[Demo GIF](docs/assets/demo-fastapi.gif) shows the detail panel and
change-impact analysis.*

Turns a raw code knowledge graph (thousands of AST nodes) into a navigable,
always-fresh map a human can actually read:

```
Product > Feature > Component > Module > File   (+ External, Database)
```

Interactive UI: banded hierarchy canvas (level = row), per-level cards,
relationship edges, detail panel with metrics and change-impact analysis.

## Quick start

```bash
# once: install (from this repo)
uv tool install --editable ./lensme
(cd lensme/ui && npm install && npm run build)

# one command: extract (graphify) + build ontology + open the map
lensme scan .
```

Or step by step: `graphify .` then `lensme build --name myproject` then
`lensme serve`.

`lensme serve --watch` keeps the map fresh: when graphify rewrites
`graph.json` (its `--watch` mode or commit hook), the ontology is rebuilt
automatically and the browser picks it up within seconds.

## Commands

| command | what it does |
|---|---|
| `lensme scan [path]` | one command: graphify extract + build + serve |
| `lensme report [-o ARCHITECTURE.md]` | living architecture doc: structure, relationships, externals, blast radius, hotspots |
| `lensme path A B` | shortest relationship path between two nodes (component or file level) |
| `lensme explain X` | everything known about one node: symbols, owner chain, edges |
| `lensme merge a.json b.json --name org` | System-level view across repos, with shared externals |
| `lensme build --prefix p/ --name x [--enrichment e.json] [--tree]` | graph.json → ontology.json; saves config for `sync` |
| `lensme sync` | rebuild using the saved config |
| `lensme sync --watch` | poll graph.json, rebuild ontology on change |
| `lensme serve [--watch] [--port N]` | serve UI + ontology.json (+ graph.html, hotspots.json), open browser |
| `lensme symbols --prefix p/ [--changed]` | per-file symbol digest for agent enrichment (hash-cached) |
| `lensme tree ontology.json` | pretty-print an ontology |
| `lensme mcp [--ontology o.json]` | MCP server (stdio, zero-dep): `get_context` / `overview` / `search` / `component` / `impact` / `path` / `explain` tools for agents |
| `lensme impact-check [--repo r] [--files ...]` | blast radius of staged files - informational, never blocks; `--install-hook` writes a pre-commit hook |
| `lensme hotspots [--repo r] [--since "6 months ago"]` | git churn + co-change joined onto the ontology; flags co-changed pairs with **no** structural edge (hidden coupling); feeds the UI heatmap |
| `lensme diff old.json new.json [--json]` | structural diff: components/files added/removed, relationship count deltas, blast-radius changes - the engine for PR architecture reports |

## Git integration

```bash
# pre-commit: see the blast radius before you commit (never blocks)
lensme impact-check --install-hook --repo . --ontology graphify-out/ontology.json

# architecture time machine: churn heatmap + hidden coupling
lensme hotspots --repo . && lensme serve   # then toggle "Show Hotspots"

# PR report core: diff two builds
lensme diff main-ontology.json feature-ontology.json
```

## Design

Two stages, mirroring graphify's own extraction philosophy:

1. **Deterministic skeleton** - no API key, no LLM. Directory nesting, path-token
   domain discovery, package-manifest externals, import-statement scanning.
   Works well for domain-nested codebases (`src/components/billing/...`).
2. **Agent enrichment (optional)** - for flat packages where directories carry no
   signal, a host agent (e.g. Claude running this tool) classifies files into
   components from their symbol digests. See `docs/enrichment-spec.md`.
   No separate API key needed when run inside an agent session.

Every node carries an honest confidence tag - surfaced in the UI's
Properties tab:

- `EXTRACTED` - structural fact from the graph (files, imports, calls)
- `INFERRED-heuristic` - path/naming rule
- `INFERRED-llm` - agent classification (with rationale)

## Output schema (`ontology.json`, schema_version 2)

```jsonc
{
  "schema_version": 2,
  "meta": { "built_at", "source_graph", "graph_stats", "level_counts" },
  "type": "Product", "name": "...", "description": "...", "stats": {...},
  "children": [ /* Feature > Component > Module > File, each with
                   confidence, rationale, description, responsibilities, stats;
                   File nodes carry symbols: [{name, line}] */ ],
  "component_relationships": [   // rolled up from file-level graph edges
    { "source", "target", "relation": "depends_on|calls|references|implements|integrates_with",
      "confidence": "EXTRACTED", "count": 3 } ],
  "file_relationships": [...],   // v2: the same edges before component rollup
  "external": [...],             // from package manifests
  "database": [...],             // keyword-detected data stores
  "impact": { "<component_id>": { "direct": [...], "indirect": [...], "total_files": N } }
}
```

## Agent token savings

`get_context` gives a coding agent its starting context in one MCP call -
the owning component, files ranked by task relevance with their symbols,
read-first suggestions, dependencies, and blast radius, trimmed to a token
budget. Source components always outrank docs/tests bands.

Measured on FastAPI across 5 tasks, baseline = ls + grep + read the top-3 grep
candidates, lensme = one `get_context` call + read the suggested file
(`python examples/bench_context.py <repo> <ontology.json> "<task>"`,
chars/4 token estimate - directional, not tokenizer-exact):

| task | baseline tokens | lensme tokens | reduction |
|---|---|---|---|
| oauth2 security scopes | 48,961 | 8,008 | 84% |
| dependency injection | 31,635 | 11,880 | 62% |
| websocket support | 91,693 | 65,139 | 29% |
| background tasks | 27,244 | 2,132 | 92% |
| response model validation | 571,901 | 5,893 | 99% |

**Reduction ranges 29-99%, not a fixed multiplier**, and the two ends explain
why: the 99% case has a baseline that explodes because "response"/"model" are
common words that grep-hit deep into the docs corpus, not because lensme did
anything special. The 29% case is the honest floor - `routing.py` (the
correct answer) is itself a 63k-token file, so once found, its content
dominates both strategies' totals and exploration savings barely move the
needle. Either way `get_context` also returns the blast radius, which the
baseline walk never computes. Small sample (5 tasks, 1 repo, one author for
both the tool and the benchmark) - `examples/bench_context.py` is the whole
methodology, run it on your own repo rather than trusting a single number.

### Answer-quality benchmark: does it point at the right file?

Token savings are cheap to claim and meaningless if `get_context` points at
the wrong file. `examples/bench_accuracy.py` checks that directly, against
ground truth mined from git history (not hand-picked): single/double-file
commits under `fastapi/` become (commit message -> actually-changed file)
pairs, gitmoji/PR-number stripped, 108 pairs from FastAPI's real history.

| metric | result |
|---|---|
| `read_first[0]` is the file the commit changed | 26% (28/108) |
| changed file is in `read_first` (top 3) | 46% (50/108) |
| changed file is anywhere in the returned file list | 81% (88/108) |

This benchmark caught two real bugs, both fixed in the current build (not
adjusted after the fact - see git history): a `"scripts"` directory was
mis-classified as product source, so its 64 dev-tooling files (translation
fixers, doc generators) out-voted the 9-50 file components that were the
actual answer on pure volume; and task-word matching used raw substring
instead of tokenized comparison, so the word "fix" in a task false-matched
`fixer.py`. Fixing both moved the "anywhere" hit rate from 30% to 81%.

The remaining misses are a real ceiling of keyword matching, not a further
bug: tasks like "Add support for PEP695 `TypeAliasType`" name a Python typing
concept that appears nowhere in the target file's path or symbol names -
`get_context` can't find what isn't lexically there. This is exactly what
`meta.enrichment_recommended` and agent enrichment (below) exist for.

**Not comparable to codebase-memory-mcp's reported 83% answer quality** - different
metric (file localization vs. Q&A correctness), different ground truth
methodology, one repo vs. their 31. Their [preprint](https://arxiv.org/abs/2603.27277)
is worth reading for how a rigorous version of this benchmark looks.

## Component assembly (registry)

Most projects are combinations of commodity components. Instead of an agent
regenerating auth/CRUD/upload/TTS from scratch every time, lensme extracts
verified components from repos you own and lets an agent assemble them:

```bash
# in the source repo (ontology built):
lensme extract "Text-to-Speech"          # package into ~/.lensme/registry

# in any target project:
lensme registry search "tts narration"   # find it
lensme install text-to-speech . --target-ontology graphify-out/ontology.json
```

`install` vendors the source shadcn-style (copy it, own it - no package
dependency) and writes a **computed wiring plan** (`WIRING.md`): every
unresolved dependency is matched against the *target project's* ontology
(`auto_matched` / `needs_decision` / `missing`), config keys are checked
against the target's `.env`, external deps against its manifest, and the plan
ends with an explicit definition of done. Both sides are ontologies, so
wiring is graph matching, not guesswork. The same flow is exposed to agents
as MCP tools: `search_components` / `get_component` / `install_component`.

Verification is provenance, not marketing: each component records its source
repo + commit (`EXTRACTED`) and carries its bundled tests when the ontology
links any.

Measured on three components extracted from a real production repo
(`python examples/bench_assembly.py`, chars/4 estimate):

| component | regenerate (emit tokens) | assemble (context tokens) | saved |
|---|---|---|---|
| text-to-speech (3 providers) | 5,746 | 365 | 94% |
| image-generation | 2,835 | 308 | 89% |
| job-store | 1,208 | 242 | 80% |

The regeneration side is a lower bound (exploration and bug-iteration cost
excluded), and glue code is excluded from both sides. The honest scope
boundary: assembly wins on the commodity layer; project-unique business
logic still gets generated.

## Validated against external repos

Run on [FastAPI](https://github.com/fastapi/fastapi) (2,718 files, ~74% of
which are docs/translations - a worst case for path heuristics):

- `tests/`, `docs/`, `docs_src/` are classified as supporting bands and sorted
  after the product source instead of drowning it (before this, "Docs" was the
  top feature with 2,016 files).
- Externals are read from `pyproject.toml` / `requirements.txt`, not just
  `package.json`: starlette, pydantic, typing-extensions detected with
  `integrates_with` edge counts per component.
- Flat packages (no directory signal) set `meta.enrichment_recommended` and the
  CLI prints a hint, instead of inventing features from filename tokens.

The FastAPI failure modes are pinned as regression tests in
`tests/test_build.py` (`test_support_kinds_sidelined`,
`test_python_manifest_externals`, `test_flat_package_flag`).

## Development

```bash
python tests/test_build.py        # pipeline self-check (no deps)
python tests/test_insights.py     # impact-check / hotspots / diff self-check
cd ui && npm run dev              # UI dev server (proxies /ontology.json to :4173)
cd ui && npm run build            # typecheck + production build
```
