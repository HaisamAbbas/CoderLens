"""Structured, evidence-grounded architecture overview for the AI Explainer.

Derived from real data — top-level folders, the primary source package, its
submodules, each submodule's key files (by symbol count) and its module
docstring — so every claim carries a citation and nothing is invented. When an
LLM is available it can enrich the prose; the structure itself is always exact.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from archaeologist.ingestion.classify import classify
from archaeologist.models.entities import File, Symbol

TOP_LABELS = {
    "src": "Core library code", "lib": "Core library code", "app": "Application code",
    "tests": "Tests", "test": "Tests", "docs": "Documentation", "doc": "Documentation",
    "examples": "Examples", "example": "Examples", "benchmarks": "Benchmarks & performance",
    "bench": "Benchmarks", "scripts": "Scripts", "tools": "Tooling", "ci": "CI",
}

_DOCSTRING = re.compile(r'\s*[rubRUB]*("""|\'\'\')(.*?)\1', re.S)


def _module_doc(content: str | None) -> str:
    if not content:
        return ""
    m = _DOCSTRING.match(content)
    if not m:
        return ""
    text = " ".join(m.group(2).strip().split())
    sent = re.match(r"^.*?[.!?](\s|$)", text)
    return (sent.group(0) if sent else text).strip()[:130]


def shape_from_paths(paths: Iterable[str],
                     weight_of: Callable[[str], int] | None = None) -> dict:
    """The mechanical skeleton of an architecture, derived from file paths alone.

    Split out so the live view and the historical view can be compared like with
    like. The live caller weights by symbol count from Postgres; the historical
    caller (analysis/arch_delta.py) reads a bare `git ls-tree` listing of some
    past commit, where no symbols were ever extracted, and weights by file. Both
    reach the same code path, so a delta between them reflects the repository
    changing rather than the two sides being computed differently.

    Path-only is what makes that possible at all: `classify()` decides code vs
    doc vs test from the path, so any commit in history can be classified
    exactly as the working tree was, with no parsing and no re-ingest.
    """
    weight_of = weight_of or (lambda _p: 1)
    paths = list(paths)
    code_paths = [p for p in paths if classify(p) == "code"]

    # top-level folder overview
    top = Counter(p.split("/")[0] for p in paths if "/" in p)
    structure = [
        {"label": TOP_LABELS[d.lower()], "chips": [{"kind": "folder", "text": d, "path": d}]}
        for d, _ in top.most_common() if d.lower() in TOP_LABELS
    ]

    # primary package = the depth<=2 directory holding the most code
    pkg_count: Counter = Counter()
    for p in code_paths:
        parts = p.split("/")
        pkg_count["/".join(parts[:2]) if len(parts) > 2 else parts[0]] += 1
    # Break count ties by name rather than by Counter insertion order, which
    # follows whatever order the paths arrived in. A delta compares two shapes
    # built from two different trees, so an order-dependent winner here would
    # show up as the package "changing" between refs that share the same layout.
    package = min(pkg_count.items(), key=lambda kv: (-kv[1], kv[0]))[0] if pkg_count else ""

    # group the package into submodules (subdir, or 'core' for top-level files)
    subs: dict[str, list[str]] = defaultdict(list)
    for p in code_paths:
        if package and not p.startswith(package + "/"):
            continue
        rest = p[len(package) + 1:]
        subs["core" if "/" not in rest else rest.split("/")[0]].append(p)

    submodules = [
        {"submodule": sub,
         "files": sorted(sfiles, key=lambda x: (-weight_of(x), x)),
         "weight": sum(weight_of(x) for x in sfiles)}
        for sub, sfiles in sorted(
            subs.items(), key=lambda kv: (-sum(weight_of(x) for x in kv[1]), kv[0]))
    ]
    return {
        "package": package,
        "structure": structure,
        "submodules": submodules,
        "counts": {"code_files": len(code_paths), "submodules": len(subs)},
    }


def build_architecture(session: Session, repo_id: int, repo_name: str) -> dict:
    files = session.scalars(select(File).where(File.repo_id == repo_id)).all()
    content_by = {f.path: f.content for f in files}
    sym_count = dict(session.execute(
        select(Symbol.file_path, func.count()).where(
            Symbol.repo_id == repo_id, Symbol.kind != "import").group_by(Symbol.file_path)
    ).all())

    shape = shape_from_paths((f.path for f in files), lambda p: sym_count.get(p, 0))
    package, structure = shape["package"], shape["structure"]
    subs = {s["submodule"]: s["files"] for s in shape["submodules"]}
    code_files = [f for f in files if f.category == "code"]

    rows = []
    for sub in subs:
        init_path = f"{package}/__init__.py" if sub == "core" else f"{package}/{sub}/__init__.py"
        top_files = subs[sub][:3]
        resp = _module_doc(content_by.get(init_path)) or _module_doc(
            content_by.get(top_files[0]) if top_files else "")
        rows.append({
            "submodule": sub,
            "responsibility": resp or f"{len(subs[sub])} files",
            "evidence": [{"kind": "file", "text": x.split("/")[-1], "path": x}
                         for x in top_files],
        })

    style = ("layered (framework-agnostic base + concrete implementation)"
             if any(s in subs for s in ("sansio", "base", "core", "abc")) else
             f"modular package with {len(subs)} submodules")

    return {
        "repo": repo_name,
        "package": package,
        "summary": (f"{repo_name} is a {len(code_files)}-file Python package. Its core lives in "
                    f"{package}, organized into {len(subs)} submodules."),
        "structure": structure,
        "layers": rows,
        "style": style,
        "counts": {"code_files": len(code_files), "submodules": len(subs)},
    }
