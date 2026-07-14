"""Component registry: extract verified components from an ontology, search
them, and install them into other projects with a computed wiring plan.

The assembly thesis: most projects are combinations of commodity components
(auth, CRUD, upload, ...). An agent should search/install a verified component
and generate only glue + project-unique logic, not regenerate thousands of
lines. The manifest is all an agent reads at assembly time - implementation
source never enters its context unless explicitly requested.

Verification is provenance, not marketing: every component records which repo
and commit it came from (EXTRACTED) plus its bundled tests. The wiring plan is
the ontology-control-tower move - both the component's home repo and the
target project have ontologies, so "what do I connect this to?" is graph
matching, not guesswork.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .build import _python_manifest_deps, _read_text, tokenize

DEFAULT_REGISTRY = Path.home() / ".lensme" / "registry"     # personal, ~/
PROJECT_REGISTRY_REL = Path(".lensme") / "registry"          # team-shared, committed in-repo
MANIFEST_SCHEMA_VERSION = 1

_ENV_PATTERNS = (
    re.compile(r"""os\.environ(?:\.get)?[\[(]\s*['"]([A-Z][A-Z0-9_]+)['"]"""),
    re.compile(r"""os\.getenv\(\s*['"]([A-Z][A-Z0-9_]+)['"]"""),
    re.compile(r"""process\.env\.([A-Z][A-Z0-9_]+)"""),
    re.compile(r"""import\.meta\.env\.([A-Z][A-Z0-9_]+)"""),
)
_COMMENT_PREFIX = {
    ".py": "#", ".rb": "#", ".sh": "#",
    ".ts": "//", ".tsx": "//", ".js": "//", ".jsx": "//", ".go": "//",
    ".rs": "//", ".java": "//", ".c": "//", ".cpp": "//",
}


# ---------- shared helpers ----------

def _words(text: str) -> list[str]:
    return tokenize(text)


def _walk_files(node: dict, out: list[dict]) -> None:
    if node.get("type") == "File":
        out.append(node)
    for c in node.get("children", []):
        _walk_files(c, out)


def _component_files(onto: dict, comp: dict) -> list[dict]:
    files: list[dict] = []
    _walk_files(comp, files)
    return files


def _find_component(onto: dict, ref: str) -> dict | None:
    """Component by id or name (exact then substring) - mirrors mcp._resolve_component
    but works on a plain ontology dict (no Onto file wrapper needed here)."""
    comps: list[dict] = []

    def walk(n: dict) -> None:
        if n.get("type") == "Component":
            comps.append(n)
        for c in n.get("children", []):
            walk(c)

    walk(onto)
    ref_l = ref.lower()
    by_id = [c for c in comps if c["id"] == ref]
    exact = [c for c in comps if c["name"].lower() == ref_l]
    partial = [c for c in comps if ref_l in c["name"].lower()]
    return (by_id or exact or partial or [None])[0]


def _file_index(onto: dict) -> dict[str, dict]:
    """File node id -> node, over the whole ontology."""
    out: dict[str, dict] = {}

    def walk(n: dict) -> None:
        if n.get("type") == "File":
            out[n["id"]] = n
        for c in n.get("children", []):
            walk(c)

    walk(onto)
    return out


def _load_build_config(onto_path: Path) -> dict:
    """root/prefix saved by `lensme build` next to the graph; defaults if absent."""
    for cand in (onto_path.parent / ".lensme_config.json",):
        if cand.exists():
            try:
                return json.loads(cand.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
    return {}


def _git_head(repo: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, OSError):
        return ""


def _git_remote(repo: Path) -> str:
    try:
        url = subprocess.run(
            ["git", "-C", str(repo), "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return re.sub(r"^git@([^:]+):", r"https://\1/", url).removesuffix(".git")
    except (subprocess.CalledProcessError, OSError):
        return ""


# ---------- license: honesty layer for imported (not-your-own) code ----------

# Weak + strong copyleft: vendoring these into proprietary code has obligations,
# so install flags them. Permissive licenses only require attribution.
COPYLEFT = {"GPL-2.0", "GPL-3.0", "LGPL-2.1", "LGPL-3.0", "AGPL-3.0", "MPL-2.0", "EPL-2.0"}
_LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "COPYING", "COPYING.md")
_LICENSE_SIGNATURES = (  # order matters: check specific before generic
    ("AGPL-3.0", ("GNU AFFERO GENERAL PUBLIC LICENSE",)),
    ("GPL-3.0", ("GNU GENERAL PUBLIC LICENSE", "Version 3")),
    ("GPL-2.0", ("GNU GENERAL PUBLIC LICENSE", "Version 2")),
    ("LGPL-3.0", ("GNU LESSER GENERAL PUBLIC LICENSE", "Version 3")),
    ("LGPL-2.1", ("GNU LESSER GENERAL PUBLIC LICENSE", "Version 2.1")),
    ("MPL-2.0", ("Mozilla Public License", "2.0")),
    ("Apache-2.0", ("Apache License", "Version 2.0")),
    ("MIT", ("Permission is hereby granted, free of charge",)),
    ("ISC", ("ISC License",)),
    ("BSD-3-Clause", ("Redistribution and use", "Neither the name")),
    ("BSD-2-Clause", ("Redistribution and use",)),
    ("Unlicense", ("This is free and unencumbered software released into the public domain",)),
)


def detect_license(root: str | Path) -> tuple[str | None, str | None]:
    """(SPDX id, license-file relpath) for a repo root. 'UNKNOWN' if a license
    file exists but isn't recognized; (None, None) if there's no license file."""
    root = Path(root)
    for fname in _LICENSE_FILES:
        path = root / fname
        if path.exists():
            text = _read_text(path)
            for spdx, needles in _LICENSE_SIGNATURES:
                if all(n.lower() in text.lower() for n in needles):
                    return spdx, fname
            return "UNKNOWN", fname
    return None, None


# ---------- registry resolution: personal (~/) vs team-shared (in-repo) ----------

def _git_root(start: str | Path) -> Path:
    try:
        out = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return Path(out)
    except (subprocess.CalledProcessError, OSError):
        return Path(start).resolve()


def project_registry_for(start: str | Path) -> Path:
    """Where `extract --share` writes: <repo-root>/.lensme/registry, committed so
    teammates install without re-extracting."""
    return _git_root(start) / PROJECT_REGISTRY_REL


def find_project_registry(start: str | Path) -> Path | None:
    """Walk up from `start` for a committed .lensme/registry, not escaping the repo."""
    cur = Path(start).resolve()
    for d in (cur, *cur.parents):
        cand = d / PROJECT_REGISTRY_REL
        if cand.is_dir():
            return cand
        if (d / ".git").exists():
            break
    return None


def resolve_registries(explicit: str | Path | None, cwd: str | Path = ".") -> list[Path]:
    """Ordered registries to consult. Explicit --registry wins outright; otherwise
    the repo's shared registry (if any) shadows the personal one by name."""
    if explicit:
        return [Path(explicit)]
    dirs: list[Path] = []
    proj = find_project_registry(cwd)
    if proj:
        dirs.append(proj)
    if DEFAULT_REGISTRY.exists():
        dirs.append(DEFAULT_REGISTRY)
    return dirs or [DEFAULT_REGISTRY]


def list_registries(dirs: list[Path]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for d in dirs:
        for m in _latest_manifests(Path(d)):
            if m["name"] not in seen:
                seen.add(m["name"])
                out.append(m)
    return out


def search_registries(dirs: list[Path], query: str) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for d in dirs:
        for m in search_registry(d, query):
            if m["name"] not in seen:
                seen.add(m["name"])
                out.append(m)
    return out


def which_registry(dirs: list[Path], name: str) -> Path | None:
    for d in dirs:
        if (Path(d) / name).is_dir():
            return Path(d)
    return None


# ---------- extract ----------

def extract_component(
    onto_path: str | Path,
    component_ref: str,
    *,
    registry_dir: str | Path = DEFAULT_REGISTRY,
    root: str | Path | None = None,
    prefix: str | None = None,
    name: str | None = None,
    imported: bool = False,
    source_url: str | None = None,
    license: str | None = None,
) -> dict:
    """Package one ontology component into the registry. Returns the manifest.

    `imported=True` marks a component pulled from someone else's repo: confidence
    becomes IMPORTED (you're trusting an external source, not your own prod-tested
    code), and license/source_url are captured so vendoring stays honest."""
    onto_path = Path(onto_path)
    onto = json.loads(onto_path.read_text(encoding="utf-8"))
    cfg = _load_build_config(onto_path)
    root = Path(root if root is not None else cfg.get("root", "."))
    prefix = prefix if prefix is not None else cfg.get("prefix", "")

    detected_license, license_file = detect_license(root)
    lic = license or detected_license

    comp = _find_component(onto, component_ref)
    if comp is None:
        raise ValueError(f"no component matching {component_ref!r} in {onto_path}")

    files = _component_files(onto, comp)
    if not files:
        raise ValueError(f"component {comp['name']!r} contains no files")
    file_ids = {f["id"] for f in files}
    paths = {f["id"]: f["path"] for f in files}
    all_files = _file_index(onto)
    frs = onto.get("file_relationships", [])

    # entry files = files other parts of the repo import; the component's surface
    inbound = {r["target"] for r in frs if r["target"] in file_ids and r["source"] not in file_ids}
    entry_files = sorted(paths[i] for i in inbound) or sorted(f["path"] for f in files)
    entry_set = set(entry_files)
    exports = [
        {"name": s["name"], "file": f["path"], "line": s.get("line")}
        for f in files if f["path"] in entry_set
        for s in f.get("symbols", [])
    ]

    # unresolved boundary: what the component reaches for OUTSIDE itself
    wiring_hints, seen_providers = [], set()
    for r in frs:
        if r["source"] in file_ids and r["target"] not in file_ids:
            provider = all_files.get(r["target"])
            if provider is None or provider["path"] in seen_providers:
                continue
            seen_providers.add(provider["path"])
            stem = Path(provider["path"]).stem
            import_lines = set()
            for fid in file_ids:
                text = _read_text(root / (prefix + paths[fid]))
                for line in text.splitlines():
                    if stem in line and re.search(r"\b(import|from|require)\b", line):
                        import_lines.add(line.strip())
            wiring_hints.append({
                "unresolved": stem,
                "original_provider": provider["path"],
                "symbols_used": [s["name"] for s in provider.get("symbols", [])][:10],
                "import_as_seen": sorted(import_lines)[:3],
            })

    # externals actually imported by this component (integrates_with edges)
    ext_targets = {
        r["target"] for r in onto.get("component_relationships", [])
        if r["source"] == comp["id"] and r["relation"] == "integrates_with"
    }
    externals = sorted(
        e["name"] for e in onto.get("external", []) if e["id"] in ext_targets
    )

    # bundled tests: files in the tests support band that reference this component
    test_ids = set()
    tests_files: dict[str, dict] = {}
    for feat in onto.get("children", []):
        if feat.get("id") == "feature_tests":
            tfiles: list[dict] = []
            _walk_files(feat, tfiles)
            tests_files = {f["id"]: f for f in tfiles}
    for r in frs:
        if r["source"] in tests_files and r["target"] in file_ids:
            test_ids.add(r["source"])

    # config points from source text
    config_points: set[str] = set()
    texts: dict[str, str] = {}
    for f in files:
        text = _read_text(root / (prefix + f["path"]))
        texts[f["path"]] = text
        for pat in _ENV_PATTERNS:
            config_points.update(pat.findall(text))

    langs = Counter(Path(f["path"]).suffix for f in files)
    lang_ext = langs.most_common(1)[0][0] if langs else ""
    language = {".py": "python", ".ts": "typescript", ".tsx": "typescript",
                ".js": "javascript", ".jsx": "javascript", ".go": "go",
                ".rs": "rust", ".rb": "ruby"}.get(lang_ext, lang_ext.lstrip("."))

    comp_name = name or re.sub(r"[^a-z0-9]+", "-", comp["name"].lower()).strip("-")
    registry_dir = Path(registry_dir)
    version = _next_version(registry_dir, comp_name)
    dest = registry_dir / comp_name / version
    src_dest = dest / "src"

    manifest = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "name": comp_name,
        "version": version,
        "language": language,
        "description": comp.get("description") or comp.get("rationale") or "",
        "confidence": "IMPORTED" if imported else "EXTRACTED",
        "provenance": {
            "repo": onto.get("name", ""),
            "commit": _git_head(root),
            "component_id": comp["id"],
            "extracted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_url": source_url or _git_remote(root),
            "license": lic,
            "license_file": license_file,
        },
        "interface": {
            "entry_files": entry_files,
            "exports": exports,
            "internal_files": sorted(f["path"] for f in files if f["path"] not in entry_set),
        },
        "dependencies": {
            "external": externals,
            "internal_unresolved": sorted(h["unresolved"] for h in wiring_hints),
        },
        "config_points": sorted(config_points),
        "tests": sorted(tests_files[i]["path"] for i in test_ids),
        "files_dir": "src/",
        "wiring_hints": wiring_hints,
    }

    # snapshot sources (and bundled tests) into the registry
    for f in files:
        out = src_dest / f["path"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(texts[f["path"]], encoding="utf-8")
    for tid in test_ids:
        tpath = tests_files[tid]["path"]
        out = dest / "tests" / tpath
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(_read_text(root / (prefix + tpath)), encoding="utf-8")
    dest.mkdir(parents=True, exist_ok=True)
    # bundle the upstream license text so vendoring carries the obligation
    if license_file and (root / license_file).exists():
        (dest / "LICENSE").write_text(_read_text(root / license_file), encoding="utf-8")
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return manifest


def _next_version(registry_dir: Path, name: str) -> str:
    comp_dir = registry_dir / name
    if not comp_dir.exists():
        return "1.0.0"
    versions = sorted(
        (tuple(int(x) for x in v.name.split(".")) for v in comp_dir.iterdir()
         if re.fullmatch(r"\d+\.\d+\.\d+", v.name)),
        reverse=True,
    )
    if not versions:
        return "1.0.0"
    major, minor, patch = versions[0]
    return f"{major}.{minor}.{patch + 1}"


# ---------- search / list ----------

def _latest_manifests(registry_dir: Path) -> list[dict]:
    out = []
    if not registry_dir.exists():
        return out
    for comp_dir in sorted(registry_dir.iterdir()):
        if not comp_dir.is_dir():
            continue
        versions = sorted(
            (v for v in comp_dir.iterdir() if (v / "manifest.json").exists()),
            key=lambda v: tuple(int(x) for x in v.name.split(".") if x.isdigit()),
            reverse=True,
        )
        if versions:
            try:
                out.append(json.loads((versions[0] / "manifest.json").read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
    return out


def search_registry(registry_dir: str | Path, query: str) -> list[dict]:
    """Rank latest-version manifests against the query (token match, top-K style)."""
    words = [w for w in _words(query)]
    scored = []
    for m in _latest_manifests(Path(registry_dir)):
        hay_name = set(_words(m["name"] + " " + m.get("description", "")))
        hay_exports = set()
        for e in m.get("interface", {}).get("exports", []):
            hay_exports.update(_words(e["name"]))
        score = 3 * sum(1 for w in words if w in hay_name) + sum(1 for w in words if w in hay_exports)
        if score > 0:
            scored.append((score, m))
    scored.sort(key=lambda t: -t[0])
    return [m for _, m in scored]


def manifest_summary(m: dict) -> dict:
    """What an agent needs to decide - never the implementation."""
    return {
        "name": m["name"],
        "version": m["version"],
        "language": m["language"],
        "description": m.get("description", ""),
        "confidence": m.get("confidence"),
        "provenance": {k: m["provenance"][k] for k in ("repo", "source_url", "license", "extracted_at")
                       if m.get("provenance", {}).get(k)},
        "exports": [e["name"] for e in m.get("interface", {}).get("exports", [])][:15],
        "external_deps": m.get("dependencies", {}).get("external", []),
        "unresolved": m.get("dependencies", {}).get("internal_unresolved", []),
        "config_points": m.get("config_points", []),
        "has_tests": bool(m.get("tests")),
    }


def load_component(registry_dir: str | Path, name: str, version: str | None = None) -> tuple[dict, Path]:
    comp_dir = Path(registry_dir) / name
    if not comp_dir.exists():
        raise ValueError(f"no component {name!r} in registry {registry_dir}")
    if version is None:
        candidates = sorted(
            (v for v in comp_dir.iterdir() if (v / "manifest.json").exists()),
            key=lambda v: tuple(int(x) for x in v.name.split(".") if x.isdigit()),
            reverse=True,
        )
        if not candidates:
            raise ValueError(f"component {name!r} has no valid versions")
        vdir = candidates[0]
    else:
        vdir = comp_dir / version
        if not (vdir / "manifest.json").exists():
            raise ValueError(f"no version {version} of {name!r}")
    return json.loads((vdir / "manifest.json").read_text(encoding="utf-8")), vdir


# ---------- install + wiring plan ----------

def install_component(
    registry_dir: str | Path,
    name: str,
    dest_dir: str | Path,
    *,
    version: str | None = None,
    target_ontology: str | Path | None = None,
) -> dict:
    """Vendor the component's source into dest_dir/<name>/ (shadcn-style: copy
    and own it) and compute the wiring plan against the target project."""
    manifest, vdir = load_component(registry_dir, name, version)
    dest_root = Path(dest_dir) / name
    installed = []
    src_root = vdir / "src"
    for src in sorted(src_root.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(src_root)
        out = dest_root / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        text = src.read_text(encoding="utf-8", errors="ignore")
        prefix = _COMMENT_PREFIX.get(src.suffix)
        if prefix:
            prov = manifest.get("provenance", {})
            lic = f" license {prov['license']}" if prov.get("license") else ""
            stamp = (f"{prefix} lensme component: {manifest['name']}@{manifest['version']} "
                     f"from {prov.get('source_url') or prov.get('repo', '?')} "
                     f"({manifest['confidence']}{lic})\n")
            text = stamp + text
        out.write_text(text, encoding="utf-8")
        installed.append(str(out))
    tests_root = vdir / "tests"
    if tests_root.exists():
        for src in sorted(tests_root.rglob("*")):
            if src.is_file():
                out = dest_root / "tests" / src.relative_to(tests_root)
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, out)
                installed.append(str(out))
    # carry the upstream license alongside the vendored source
    if (vdir / "LICENSE").exists():
        out = dest_root / "LICENSE"
        shutil.copyfile(vdir / "LICENSE", out)
        installed.append(str(out))

    plan = wiring_plan(manifest, dest_dir, target_ontology)
    (dest_root / "WIRING.md").write_text(format_wiring(manifest, plan), encoding="utf-8")
    return {"installed_files": installed, "wiring_plan": plan,
            "wiring_doc": str(dest_root / "WIRING.md")}


def wiring_plan(manifest: dict, dest_dir: str | Path, target_ontology: str | Path | None) -> dict:
    """Match the component's unresolved boundary against the TARGET project's
    ontology. Both sides are ontologies - wiring is graph matching, not guessing."""
    dest_dir = Path(dest_dir)
    target = None
    if target_ontology and Path(target_ontology).exists():
        target = json.loads(Path(target_ontology).read_text(encoding="utf-8"))

    unresolved_out = []
    target_files = list(_file_index(target).values()) if target else []
    for hint in manifest.get("wiring_hints", []):
        query_words = set(_words(hint["unresolved"] + " " + " ".join(hint.get("symbols_used", []))))
        candidates = []
        for f in target_files:
            toks = set(_words(f["path"]))
            for s in f.get("symbols", []):
                toks.update(_words(s["name"]))
            score = sum(1 for w in query_words if w in toks)
            if Path(f["path"]).stem.lower() == hint["unresolved"].lower():
                score += 5  # exact provider-name match
            if score > 0:
                candidates.append((score, f["path"]))
        candidates.sort(key=lambda t: -t[0])
        if not candidates:
            status, matched = "missing", []
        elif len(candidates) == 1 or candidates[0][0] >= 2 * candidates[1][0]:
            status, matched = "auto_matched", [candidates[0][1]]
        else:
            status, matched = "needs_decision", [p for _, p in candidates[:3]]
        unresolved_out.append({
            "unresolved": hint["unresolved"],
            "status": status if target else "no_target_ontology",
            "candidates": matched,
            "original_shape": {
                "provider": hint["original_provider"],
                "symbols_used": hint.get("symbols_used", []),
                "import_as_seen": hint.get("import_as_seen", []),
            },
        })

    # config points vs target env files
    env_text = ""
    for env_name in (".env", ".env.example", ".env.local"):
        env_text += _read_text(dest_dir / env_name)
    config_out = [
        {"key": k, "status": "present" if k in env_text else "add_required"}
        for k in manifest.get("config_points", [])
    ]

    # external deps vs target manifests
    target_deps: set[str] = set()
    for mf in (dest_dir / "pyproject.toml", dest_dir / "requirements.txt"):
        target_deps.update(_python_manifest_deps(mf))
    pkg = dest_dir / "package.json"
    if pkg.exists():
        try:
            pj = json.loads(pkg.read_text(encoding="utf-8"))
            target_deps.update(pj.get("dependencies", {}))
            target_deps.update(pj.get("devDependencies", {}))
        except (OSError, json.JSONDecodeError):
            pass
    deps_out = [
        {"name": d, "status": "present" if d in target_deps else "install_required"}
        for d in manifest.get("dependencies", {}).get("external", [])
    ]

    prov = manifest.get("provenance", {})
    lic = prov.get("license")
    if manifest.get("confidence") == "IMPORTED" or lic:
        license_out = {
            "spdx": lic,
            "source_url": prov.get("source_url"),
            "action": (
                "copyleft/unknown - review obligations before vendoring into proprietary code"
                if (lic in COPYLEFT or lic in (None, "UNKNOWN")) else
                "permissive - keep the bundled LICENSE file for attribution"
            ),
        }
    else:
        license_out = None

    return {
        "unresolved": unresolved_out,
        "config": config_out,
        "external_deps": deps_out,
        "license": license_out,
        "definition_of_done": (
            "bundled tests pass in the target project"
            if manifest.get("tests") else
            "component imports resolve and the target project's own checks pass"
        ),
    }


def format_wiring(manifest: dict, plan: dict) -> str:
    L = [f"# Wiring: {manifest['name']}@{manifest['version']}", ""]
    L += ["## 1. Adapt imports", ""]
    if not plan["unresolved"]:
        L += ["- nothing to wire - component is self-contained", ""]
    for u in plan["unresolved"]:
        L += [f"- **{u['unresolved']}** ({u['status']})"]
        shape = u["original_shape"]
        L += [f"  - original shape: `{shape['provider']}` "
              f"providing {', '.join(shape['symbols_used'][:5]) or '?'}"]
        for imp in shape["import_as_seen"]:
            L += [f"  - as seen in source: `{imp}`"]
        if u["candidates"]:
            L += [f"  - target candidate(s): {', '.join('`' + c + '`' for c in u['candidates'])}"]
        elif u["status"] == "missing":
            L += ["  - no equivalent in target - create one matching the original shape"]
        L += [""]
    L += ["## 2. Provide config", ""]
    L += [f"- `{c['key']}`: {c['status']}" for c in plan["config"]] or ["- none"]
    L += ["", "## 3. Install deps", ""]
    L += [f"- `{d['name']}`: {d['status']}" for d in plan["external_deps"]] or ["- none"]
    lic = plan.get("license")
    if lic:
        warn = "⚠️ " if "copyleft/unknown" in lic["action"] else ""
        L += ["", "## 4. License", "",
              f"- {warn}**{lic['spdx'] or 'UNKNOWN'}** - {lic['action']}"]
        if lic.get("source_url"):
            L += [f"- source: {lic['source_url']}"]
        L += ["- the upstream LICENSE is vendored alongside the source; keep it"]
        step = "5"
    else:
        step = "4"
    L += ["", f"## {step}. Definition of done", "", f"- {plan['definition_of_done']}", ""]
    return "\n".join(L)
