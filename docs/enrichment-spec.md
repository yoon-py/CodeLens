# codelens enrichment spec (for host agents)

When a codebase is a flat package (many files in one directory), codelens's
path-based heuristics cannot infer Components. A host agent (Claude, Copilot,
any LLM agent running codelens) fills the gap by classifying files into
components from their **symbol digests** - never from full file contents.

## Workflow

```bash
# 1. emit the per-file symbol digest (rel_path -> symbol labels)
codelens symbols --graph graphify-out/graph.json --prefix mypkg/ > symbols.json

# incremental: only files whose symbols changed since the last run
codelens symbols --graph graphify-out/graph.json --prefix mypkg/ --changed

# 2. the agent reads symbols.json and writes enrichment.json (schema below)

# 3. build with the enrichment
codelens build --graph graphify-out/graph.json --prefix mypkg/ \
  --name mypkg --enrichment enrichment.json --tree
```

## Classification rules for the agent

1. Group files into 5-12 components by **architectural role**, judged from
   symbol names (function/class labels), not from filenames alone.
2. Each component needs:
   - `name`: 2-4 word human name ("AST Extraction Engine")
   - `rationale`: one line of evidence for WHY these files group together
   - `description`: 1-2 sentence user-facing summary (shown in the UI panel)
   - `responsibilities`: 3-5 short imperative bullets ("Parse source into AST")
   - `feature`: snake_case group key - components sharing a feature render
     under one Feature band (e.g. `extraction_pipeline`, `interfaces`)
3. `files` entries are **paths relative to the scan prefix** - never bare
   basenames (`extractors/__init__.py` and `__init__.py` are different files).
4. Only classify what you can justify from symbols. A file you cannot place
   confidently should be **omitted** - it stays in the heuristic tree rather
   than getting a guessed label. Never invent a component to reach a count.
5. Do not re-classify files that directory heuristics already handle well
   (files under meaningful subdirectories). Focus on the flat remainder.

Everything an agent writes is tagged `INFERRED-llm` in the output ontology,
distinct from `INFERRED-heuristic` (path rules) and `EXTRACTED` (structural
facts). This audit trail is a core product guarantee - do not blur it.

## enrichment.json schema (v2)

```jsonc
{
  "_meta": { "method": "...", "target_prefix": "mypkg/" },   // optional, free-form
  "components": {
    "<snake_case_key>": {
      "name": "Human Name",
      "rationale": "why these files group together",
      "description": "user-facing 1-2 sentence summary",     // optional
      "responsibilities": ["Do X", "Do Y"],                  // optional
      "feature": "feature_group_key",                        // optional, default "core"
      "files": ["a.py", "sub/b.py"]                          // rel to prefix
    }
  }
}
```

A complete worked example: `examples/enrichment_graphify_pkg.json`
(classifies graphify's own 45-file flat package into 9 components).
