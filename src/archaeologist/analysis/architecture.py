"""Structured, evidence-grounded architecture overview for the AI Explainer.

Derived from real data — top-level folders, the primary source package, its
submodules, each submodule's key files (by symbol count) and its module
docstring — so every claim carries a citation and nothing is invented. When an
LLM is available it can enrich the prose; the structure itself is always exact.
"""

import re
from collections import Counter, defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

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


def build_architecture(session: Session, repo_id: int, repo_name: str) -> dict:
    files = session.scalars(select(File).where(File.repo_id == repo_id)).all()
    content_by = {f.path: f.content for f in files}
    code_files = [f for f in files if f.category == "code"]
    sym_count = dict(session.execute(
        select(Symbol.file_path, func.count()).where(
            Symbol.repo_id == repo_id, Symbol.kind != "import").group_by(Symbol.file_path)
    ).all())

    # top-level folder overview
    top = Counter(f.path.split("/")[0] for f in files if "/" in f.path)
    structure = [
        {"label": TOP_LABELS[d.lower()], "chips": [{"kind": "folder", "text": d, "path": d}]}
        for d, _ in top.most_common() if d.lower() in TOP_LABELS
    ]

    # primary package = the depth<=2 directory holding the most code
    pkg_count: Counter = Counter()
    for f in code_files:
        parts = f.path.split("/")
        pkg_count["/".join(parts[:2]) if len(parts) > 2 else parts[0]] += 1
    package = pkg_count.most_common(1)[0][0] if pkg_count else ""

    # group the package into submodules (subdir, or 'core' for top-level files)
    subs: dict[str, list[File]] = defaultdict(list)
    for f in code_files:
        if package and not f.path.startswith(package + "/"):
            continue
        rest = f.path[len(package) + 1:]
        subs["core" if "/" not in rest else rest.split("/")[0]].append(f)

    rows = []
    for sub, sfiles in sorted(subs.items(), key=lambda kv: -sum(sym_count.get(x.path, 0) for x in kv[1])):
        init_path = f"{package}/__init__.py" if sub == "core" else f"{package}/{sub}/__init__.py"
        top_files = sorted(sfiles, key=lambda x: -sym_count.get(x.path, 0))[:3]
        resp = _module_doc(content_by.get(init_path)) or _module_doc(
            content_by.get(top_files[0].path) if top_files else "")
        rows.append({
            "submodule": sub,
            "responsibility": resp or f"{len(sfiles)} files",
            "evidence": [{"kind": "file", "text": x.path.split("/")[-1], "path": x.path}
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
