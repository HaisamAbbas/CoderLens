"""DeepWiki-style "Start here" — page structure and count are decided by the
LLM per repo (DeepWiki's actual approach: 4-9 pages, topics chosen to fit this
specific codebase) instead of a fixed list of headings. The LLM can only pick
from a menu of REAL, already-computed focuses (architecture, API surface, data
model, getting started, core engine, pipeline, one per detected subsystem/
community) — it never invents a topic with no real data behind it. Several of
these only appear when the repo actually has that shape: API surface needs
real route-registration decorators, data model needs real ORM/schema base
classes — a CLI tool or a plain library simply won't get those pages, rather
than getting a wrong or invented one. This is what makes two differently-
shaped repos (a web API vs. a CLI tool vs. a library) produce genuinely
different wikis, not the same skeleton with different words filled in.

Each page is assembled as a sequence of SECTIONS (heading → prose → artifact),
mirroring how a real DeepWiki article reads. The hard facts — diagrams, tables,
real source snippets, evidence links — are 100% mechanical; only the connecting
narrative is LLM-authored, and it is fed only verified facts. Diagrams are
mechanically generated **Mermaid** flowcharts (laid out by Mermaid's own dagre
engine, rendered by the bundled `mermaid` library) — never LLM-authored, so
nothing here can produce an unrenderable diagram. Code snippets are the real
source sliced straight from the indexed file, so they're always correct and
copy-pasteable.

Every LLM step degrades gracefully: if structure-decision fails/unavailable,
falls back to a fixed, sensible order of all available focuses; if a section's
prose call fails, that section just renders its heading + mechanical artifact
with no intro paragraph. The wiki is never broken or empty because of the LLM.

Per-section prose calls are independent and stateless (they touch only
pre-computed facts, never the DB session), so they're fanned out concurrently
with a thread pool — a page with a dozen sections still generates in seconds.
"""

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from archaeologist.analysis.architecture import build_architecture
from archaeologist.analysis.communities import find_communities
from archaeologist.analysis.entrypoints import find_entrypoints
from archaeologist.models.entities import File, Symbol, SymbolEdge
from archaeologist.rag.llm import call_llm, llm_available, parse_llm_json
from archaeologist.retrieval.graph_queries import call_flow

_START_PREF = {"factory": 0, "route": 1, "main": 2, "cli": 3, "worker": 4, "module": 5}
_TOOLING = {
    "pyproject.toml": "Python (uv/pip) — see `pyproject.toml` for dependencies and entry points.",
    "requirements.txt": "Python (pip) — see `requirements.txt` for dependencies.",
    "package.json": "Node.js — see `package.json` for scripts and dependencies.",
    "Dockerfile": "Containerized — see `Dockerfile` for the runtime image.",
    "docker-compose.yml": "Multi-service via Docker Compose — see `docker-compose.yml`.",
    "go.mod": "Go module — see `go.mod` for dependencies.",
    "Cargo.toml": "Rust crate — see `Cargo.toml` for dependencies.",
}
_EXT_LANG = {
    "py": "python", "pyi": "python", "js": "javascript", "jsx": "javascript",
    "mjs": "javascript", "cjs": "javascript", "ts": "typescript", "tsx": "typescript",
    "go": "go", "rs": "rust", "java": "java", "rb": "ruby", "php": "php", "cs": "csharp",
    "cpp": "cpp", "cc": "cpp", "cxx": "cpp", "hpp": "cpp", "c": "c", "h": "c",
    "sh": "bash", "bash": "bash", "sql": "sql", "yaml": "yaml", "yml": "yaml",
    "json": "json", "html": "html", "css": "css", "scss": "css", "toml": "ini", "ini": "ini",
}
MAX_SUBSYSTEM_PAGES = 5  # bounded so the sidebar stays scannable even on a big repo
DEFAULT_ORDER = ["architecture", "api_surface", "data_model", "getting_started", "core_engine", "pipeline"]

# ---------- structural pattern detection (mechanical, never LLM-guessed) ----------
# Same discipline as analysis/codemap.py's classify_role: matches real, visible
# syntax (a decorator, a base class) — never infers from naming conventions or
# invents structure the source doesn't actually show.

_ROUTE_METHOD_DECORATOR_RE = re.compile(r'@\w+\.(get|post|put|delete|patch|head|options)\(\s*["\']([^"\']*)["\']')
_ROUTE_GENERIC_DECORATOR_RE = re.compile(r'@\w+\.route\(\s*["\']([^"\']*)["\']([^)]*)\)')
_ROUTE_METHODS_KW_RE = re.compile(r'methods\s*=\s*\[([^\]]*)\]')


def _detect_route(code: str) -> tuple[str, str] | None:
    """(HTTP method(s), path) for a function/method whose decorator registers
    an HTTP route — FastAPI/Starlette (@x.get/post/...) and Flask
    (@x.route(path, methods=[...])) shapes. Scans only the decorator lines
    (everything before the def), never the body — a route detected this way
    is never a guess, it's the literal decorator the framework itself reads
    to register it. A repo using a different registration style (Express-like
    plain calls, Django urls.py) just won't be detected — no page rather than
    a wrong one."""
    head = code.split("def ", 1)[0]
    m = _ROUTE_METHOD_DECORATOR_RE.search(head)
    if m:
        return m.group(1).upper(), m.group(2)
    m = _ROUTE_GENERIC_DECORATOR_RE.search(head)
    if m:
        path = m.group(1)
        mm = _ROUTE_METHODS_KW_RE.search(m.group(2))
        methods = ", ".join(x.strip(" '\"") for x in mm.group(1).split(",")) if mm else "GET"
        return methods, path
    return None


_MODEL_BASE_RE = re.compile(r'class\s+\w+\s*\(\s*([^)]*)\)')


def _detect_model_base(code: str) -> str | None:
    """The real base-class text if this class looks like an ORM/schema model
    — SQLAlchemy (...Base), Pydantic (BaseModel), Django (models.Model / db.Model),
    or a plain @dataclass. Matched by the actual base-class name in the source,
    never by guessing from the class's own name."""
    if "@dataclass" in code.split("class ", 1)[0]:
        return "dataclass"
    m = _MODEL_BASE_RE.search(code[:400])
    if not m:
        return None
    for b in (x.strip() for x in m.group(1).split(",")):
        base_name = b.split(".")[-1]  # models.Model / db.Model -> Model
        if base_name in ("Base", "BaseModel", "Model") or base_name.endswith(("Base", "Model")):
            return b
    return None


# ---------- generic helpers ----------

def _first_sentence(doc: str | None, limit: int = 240) -> str:
    if not doc:
        return ""
    clean = " ".join(doc.split())
    dot = clean.find(". ")
    return (clean[: dot + 1] if dot != -1 else clean)[:limit]


def _sec(key: str, title: str, subtitle: str, blocks: list[dict]) -> dict:
    return {"key": key, "title": title, "subtitle": subtitle, "blocks": [b for b in blocks if b]}


def _md(text: str | None) -> dict | None:
    return {"kind": "md", "text": text} if text else None


def _list(items: list[str | None]) -> dict | None:
    clean = [i for i in items if i]
    return {"kind": "list", "items": clean} if clean else None


def _chips(evidence: list[dict]) -> dict | None:
    return {"kind": "chips", "chips": evidence} if evidence else None


def _h2(text: str) -> dict:
    return {"kind": "h2", "text": text}


def _table(columns: list[str], rows: list[list[str]]) -> dict | None:
    return {"kind": "table", "columns": columns, "rows": rows} if rows else None


def _diagram(title: str, mermaid: str | None) -> dict | None:
    return {"kind": "diagram", "title": title, "mermaid": mermaid} if mermaid else None


def _code(title: str, path: str, line: int, lang: str, code: str | None) -> dict | None:
    return {"kind": "code", "title": title, "path": path, "line": line, "lang": lang, "code": code} if code else None


def _mlabel(text: str, limit: int = 40) -> str:
    t = (text or "").strip()
    return (t[: limit - 1] + "…") if len(t) > limit else t


def _leaf(qn: str) -> str:
    """Shortest readable form of a symbol/label for a diagram box: the last one
    or two dotted segments (`WikiBuilder.build`, not the full module path)."""
    q = (qn or "").strip().replace("()", "")
    seg = [p for p in q.split(".") if p]
    if len(seg) >= 2:
        return ".".join(seg[-2:])
    leaf = (seg[-1] if seg else q).rsplit("/", 1)[-1]
    # A root path like "/" has no basename after the split ("" — an empty
    # Mermaid node label breaks the flowchart parser) — fall back to the
    # untouched original text, which for a bare "/" is itself non-empty.
    return leaf or q or "?"


def _dir_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else path


def _lang_of(path: str) -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    return _EXT_LANG.get(ext, "")


def _symbol_chip(s: Symbol) -> dict:
    return {"kind": "file", "text": s.qualified_name, "path": s.file_path, "line": s.start_line}


def _primary_package(code_files: list[File]) -> str:
    c: Counter = Counter()
    for f in code_files:
        parts = f.path.split("/")
        c["/".join(parts[:2]) if len(parts) > 2 else parts[0]] += 1
    return c.most_common(1)[0][0] if c else ""


def _code_snippet(files_by_path: dict[str, File], sym: Symbol | None, max_lines: int = 46) -> dict | None:
    """The REAL source of a symbol, sliced from the indexed file — always correct
    and copy-pasteable (never LLM-generated). Long bodies are cut with a marker."""
    if sym is None:
        return None
    f = files_by_path.get(sym.file_path)
    if not f or not f.content:
        return None
    lines = f.content.splitlines()
    start = max(1, sym.start_line or 1)
    end = min(len(lines), sym.end_line or start)
    if end < start:
        end = start
    snippet = lines[start - 1:end]
    if not snippet:
        return None
    truncated = len(snippet) > max_lines
    if truncated:
        snippet = snippet[:max_lines]
    code = "\n".join(snippet)
    if truncated:
        code += "\n    # … (truncated)"
    return _code(sym.qualified_name, sym.file_path, sym.start_line, _lang_of(sym.file_path), code)


# ---------- Mermaid diagram builders (mechanical, never LLM-authored) ----------
# We emit `flowchart` source that Mermaid's own dagre engine lays out — so the
# layout is always clean and we never hand-place a single coordinate. Colours are
# applied with classDef per group. Nothing the LLM writes ever reaches Mermaid.

_MM_PALETTE = [
    ("#e7f5ff", "#1971c2"), ("#fff3bf", "#e8590c"), ("#ebfbee", "#2f9e44"),
    ("#f3f0ff", "#7048e8"), ("#fff0f6", "#c2255c"), ("#e3fafc", "#0c8599"),
    ("#fff9db", "#f08c00"), ("#eef2ff", "#4263eb"),
]


def _mm_txt(text: str, limit: int = 30) -> str:
    """A safe, short Mermaid node label — quotes/brackets/newlines removed so the
    quoted-string label can never break the parser."""
    t = _mlabel(_leaf(text), limit)
    return t.replace('"', "'").replace("\n", " ").replace("[", "(").replace("]", ")").replace("`", "")


def _mm_graph(nodes: list[dict], edges: list[tuple[str, str]],
              direction: str = "TD", subgraphs: bool = True) -> str | None:
    """nodes: [{id,label,group}], edges: [(src,dst)]. Isolated nodes are dropped
    (a call graph only shows connected structure). When more than one group is
    present and `subgraphs` is on, each group becomes a labelled subgraph box
    (DeepWiki's module-grouping look); otherwise nodes are drawn flat but still
    coloured by group. Returns Mermaid source or None when there's no structure."""
    ids = {n["id"] for n in nodes}
    seen: set[tuple[str, str]] = set()
    E: list[tuple[str, str]] = []
    for s, d in edges:
        if s in ids and d in ids and s != d and (s, d) not in seen:
            seen.add((s, d))
            E.append((s, d))
    if E:
        touched = {x for e in E for x in e}
        nodes = [n for n in nodes if n["id"] in touched]
    if not nodes or (len(nodes) > 1 and not E):
        return None

    groups: dict[str, list[dict]] = {}
    for n in nodes:
        groups.setdefault(n["group"], []).append(n)
    use_sub = subgraphs and len(groups) > 1

    lines = [f"flowchart {direction}"]
    gi_map: dict[str, int] = {}
    for gi, (g, gn) in enumerate(groups.items()):
        gi_map[g] = gi
        if use_sub:
            lines.append(f'  subgraph sg{gi}["{_mm_txt(g, 26)}"]')
            lines.append("    direction TB")
            for n in gn:
                lines.append(f'    {n["id"]}["{_mm_txt(n["label"])}"]')
            lines.append("  end")
        else:
            for n in gn:
                lines.append(f'  {n["id"]}["{_mm_txt(n["label"])}"]')
    for s, d in E:
        lines.append(f"  {s} --> {d}")
    for g, gi in gi_map.items():
        fill, stroke = _MM_PALETTE[gi % len(_MM_PALETTE)]
        members = ",".join(n["id"] for n in groups[g])
        lines.append(f"  classDef c{gi} fill:{fill},stroke:{stroke},color:#0b1c2c,stroke-width:1px;")
        lines.append(f"  class {members} c{gi};")
    return "\n".join(lines)


def _mermaid_architecture(repo_name: str, arch: dict, entrypoints: list[dict]) -> str | None:
    """Entry points (grouped) → package hub → submodules (grouped), top-down."""
    eps = [e for e in entrypoints if e.get("kind") in ("factory", "main", "route", "cli")][:6]
    layers = arch["layers"][:8]
    if not layers:
        return None
    lines = ["flowchart TD", f'  pkg(["{_mm_txt(arch.get("package") or repo_name, 26)}"])']
    if eps:
        lines.append('  subgraph sgin["Entry points"]')
        lines.append("    direction LR")
        for i, e in enumerate(eps):
            lines.append(f'    ep{i}["{_mm_txt(e["label"])}"]')
        lines.append("  end")
        for i in range(len(eps)):
            lines.append(f"  ep{i} --> pkg")
    lines.append('  subgraph sgsub["Submodules"]')
    lines.append("    direction LR")
    for i, layer in enumerate(layers):
        lines.append(f'    s{i}["{_mm_txt(layer["submodule"])}"]')
    lines.append("  end")
    for i in range(len(layers)):
        lines.append(f"  pkg --> s{i}")
    lines.append("  classDef pkg fill:#4263eb,stroke:#364fc7,color:#fff,stroke-width:1.5px;")
    lines.append("  class pkg pkg;")
    if eps:
        lines.append("  classDef ep fill:#e7f5ff,stroke:#1971c2,color:#0b3d63;")
        lines.append("  class " + ",".join(f"ep{i}" for i in range(len(eps))) + " ep;")
    lines.append("  classDef sub fill:#ebfbee,stroke:#2f9e44,color:#12401f;")
    lines.append("  class " + ",".join(f"s{i}" for i in range(len(layers))) + " sub;")
    return "\n".join(lines)


def _mermaid_flow(flow: dict) -> str | None:
    """A call flow reads best as one clean top-down chain (no subgraph boxes),
    coloured by the directory each symbol lives in."""
    by_id = {n["id"]: n for n in flow["nodes"]}
    nodes = [{"id": f"n{n['id']}", "label": _leaf(n["qualified_name"]), "group": _dir_of(n["file"])}
             for n in by_id.values()]
    edges = [(f"n{e['source']}", f"n{e['target']}") for e in flow["edges"]
             if e["source"] in by_id and e["target"] in by_id]
    return _mm_graph(nodes, edges, direction="TD", subgraphs=False)


def _mermaid_community(session: Session, repo_id: int, cluster: dict) -> str | None:
    """A subsystem's internal call graph, with directory subgraph boxes so the
    module boundaries inside the cluster are visible."""
    members = cluster["members"]
    ids = [m["id"] for m in members]
    rows = session.execute(
        select(SymbolEdge.src_symbol_id, SymbolEdge.dst_symbol_id)
        .where(SymbolEdge.repo_id == repo_id, SymbolEdge.edge_type == "call",
               SymbolEdge.src_symbol_id.in_(ids), SymbolEdge.dst_symbol_id.in_(ids))
    ).all()
    edges = [(f"c{src}", f"c{dst}") for src, dst in rows if src != dst]
    nodes = [{"id": f"c{m['id']}", "label": _leaf(m["qualified_name"]), "group": _dir_of(m["path"])}
             for m in members]
    return _mm_graph(nodes, edges, direction="TD", subgraphs=True)


# ---------- per-focus section builders ----------
# Each returns (sections, one_line_menu_summary) or (None, None) when there's
# nothing real to show. A "section" is {heading, facts, blocks}: `heading`
# becomes an H2 (or None for the page intro), `facts` (if present) drives ONE
# grounded LLM prose paragraph placed under the heading, and `blocks` are the
# mechanical artifacts (diagram / table / code / chips) shown after the prose.

def _focus_architecture(repo_name: str, arch: dict, eps: list[dict]):
    if not arch["layers"]:
        return None, None
    sub_facts = "; ".join(f"{l['submodule']} — {l['responsibility']}" for l in arch["layers"])
    ep_list = [e for e in eps if e.get("kind") in ("factory", "main", "route", "cli")][:10]
    sections = [
        {"heading": None,
         "facts": (f"{repo_name} is organised into {arch['counts']['submodules']} submodules across "
                   f"{arch['counts']['code_files']} code files. The submodules and what each is responsible "
                   f"for: {sub_facts}."),
         "blocks": [_diagram("System architecture", _mermaid_architecture(repo_name, arch, eps))]},
        {"heading": "Submodules",
         "facts": f"Each top-level submodule and its responsibility: {sub_facts}.",
         "blocks": [_table(["Submodule", "Responsibility"],
                           [[l["submodule"], l["responsibility"]] for l in arch["layers"]])]},
    ]
    if ep_list:
        sections.append({
            "heading": "Entry points",
            "facts": ("Execution enters the system through these entry points: "
                      + "; ".join(f"{e['label']} ({e['kind']}) at {e['path']}:{e['line']}" for e in ep_list)),
            "blocks": [_chips([{"kind": "file", "text": e["label"], "path": e["path"], "line": e["line"]}
                               for e in ep_list])],
        })
    summary = (f"{arch['counts']['submodules']} submodules across {arch['counts']['code_files']} code files: {sub_facts}")
    return sections, summary


def _focus_getting_started(session: Session, repo_id: int, entrypoints: list[dict]):
    readme = session.scalars(
        select(File).where(File.repo_id == repo_id, File.category == "doc",
                            func.lower(File.path).like("%readme%"))
    ).first()
    readme_text = ""
    if readme and readme.content:
        rlines = [ln for ln in readme.content.strip().splitlines()
                  if not ln.strip().startswith(("[![", "![", "<p align", "<img"))]
        readme_text = "\n".join(rlines).strip()[:1100]

    cfg_files = session.scalars(select(File).where(File.repo_id == repo_id, File.category == "config")).all()
    seen: set[str] = set()
    tooling: list[str] = []
    for f in cfg_files:
        base = f.path.split("/")[-1]
        if base in _TOOLING and base not in seen:
            tooling.append(_TOOLING[base])
            seen.add(base)

    runnable = sorted((e for e in entrypoints if e["kind"] in ("main", "cli", "module", "factory", "route")),
                      key=lambda e: _START_PREF.get(e["kind"], 9))[:6]

    intro_facts = (f"README excerpt (verbatim): {readme_text[:800]}" if readme_text
                   else "No README was found; orient the reader from the project's tooling and entry points below.")
    intro_blocks = []
    if readme:
        intro_blocks.append(_chips([{"kind": "file", "text": readme.path.split("/")[-1], "path": readme.path}]))
    sections = [{"heading": None, "facts": intro_facts, "blocks": intro_blocks}]

    if tooling:
        sections.append({
            "heading": "Requirements & tooling",
            "facts": "Build/dependency tooling detected in the repo: " + "; ".join(tooling),
            "blocks": [_list(tooling)],
        })
    if runnable:
        sections.append({
            "heading": "Running the project",
            "facts": ("Runnable entry points, in the order most useful to a newcomer: "
                      + "; ".join(f"{e['label']} ({e['kind']}) at {e['path']}:{e['line']}" for e in runnable)),
            "blocks": [
                _list([f"`{e['label']}` — {e['path']}:{e['line']}" for e in runnable]),
                _chips([{"kind": "file", "text": e["label"], "path": e["path"], "line": e["line"]} for e in runnable]),
            ],
        })

    summary = (f"README excerpt: {readme_text[:400]}" if readme_text else "No README found.")
    summary += " Tooling: " + ("; ".join(tooling) if tooling else "none detected.")
    summary += " Entrypoints: " + ("; ".join(e["label"] for e in runnable) if runnable else "none detected.")
    return sections, summary


def _focus_core_engine(files_by_path: dict[str, File], start_sym: Symbol | None, start_why: str,
                       core_models: list[Symbol], fan_in: dict[int, float]):
    if not start_sym:
        return None, None
    intro_facts = f"The central type/entry of the package is {start_sym.qualified_name}. {start_why}"
    if start_sym.docstring:
        intro_facts += f" Its own docstring says: {_first_sentence(start_sym.docstring)}"
    intro_facts += f" It is defined at {start_sym.file_path}:{start_sym.start_line}."
    intro_blocks = [_chips([_symbol_chip(start_sym)]), _code_snippet(files_by_path, start_sym)]
    sections = [{"heading": None, "facts": intro_facts, "blocks": intro_blocks}]

    if core_models:
        cm_facts = ("The other abstractions the rest of the system leans on most (by how many places depend "
                    "on them): "
                    + "; ".join(f"{s.qualified_name}, used in {round(fan_in.get(s.id, 0))} places"
                                + (f" — {_first_sentence(s.docstring)}" if s.docstring else "")
                                for s in core_models))
        cm_blocks = [
            _list([f"**{s.qualified_name}** — depended on in {round(fan_in.get(s.id, 0))} places."
                   + (f" {_first_sentence(s.docstring)}" if s.docstring else "") for s in core_models]),
            _chips([_symbol_chip(s) for s in core_models]),
            _code_snippet(files_by_path, core_models[0]),
        ]
        sections.append({"heading": "Central abstractions", "facts": cm_facts, "blocks": cm_blocks})

    summary = f"{start_sym.qualified_name} — {start_why}"
    if core_models:
        summary += " Other central abstractions: " + "; ".join(
            f"{s.qualified_name} (depended on in {round(fan_in.get(s.id, 0))} places)" for s in core_models)
    return sections, summary


def _focus_api_surface(syms: dict[int, Symbol], files_by_path: dict[str, File]):
    """Real HTTP routes, detected from the actual route-registration decorator
    each handler carries (see _detect_route) — never inferred from naming.
    A repo with no such decorators anywhere (a library, a CLI tool, a repo
    that registers routes some other way) simply gets no page at all."""
    routes: list[tuple[str, str, Symbol]] = []
    for s in syms.values():
        if s.kind not in ("function", "method") or not s.code:
            continue
        detected = _detect_route(s.code)
        if detected is None:
            continue
        routes.append((*detected, s))
    if not routes:
        return None, None
    routes.sort(key=lambda r: (r[1], r[0]))
    routes = routes[:40]  # bounded so a huge API doesn't blow the page up

    facts = ("Real HTTP routes, detected from the actual route-registration decorator each handler "
             "carries: " + "; ".join(f"{m} {p} -> {s.qualified_name}" for m, p, s in routes[:20]))
    rows = [[m, p, s.qualified_name] for m, p, s in routes]
    sections = [{"heading": None, "facts": facts,
                "blocks": [_table(["Method", "Path", "Handler"], rows)]}]

    top = routes[0][2]
    sections.append({
        "heading": "Example handler",
        "facts": f"One representative handler: {top.qualified_name} at {top.file_path}:{top.start_line}.",
        "blocks": [_chips([_symbol_chip(top)]), _code_snippet(files_by_path, top)],
    })
    summary = f"{len(routes)} HTTP route(s) detected: " + "; ".join(f"{m} {p}" for m, p, _ in routes[:10])
    return sections, summary


def _focus_data_model(syms: dict[int, Symbol], files_by_path: dict[str, File]):
    """Real ORM/schema classes, detected from their actual base class (see
    _detect_model_base) — SQLAlchemy, Pydantic, Django, or plain dataclasses.
    "Mentions" is a real textual signal (another detected model's class name
    appears in this one's source), not a parsed foreign key — honestly
    labeled as such rather than overclaiming a relationship that wasn't
    actually parsed out of the field definitions."""
    models: list[tuple[Symbol, str]] = []
    for s in syms.values():
        if s.kind != "class" or not s.code:
            continue
        base = _detect_model_base(s.code)
        if base:
            models.append((s, base))
    if not models:
        return None, None
    models.sort(key=lambda m: m[0].qualified_name)
    models = models[:30]

    model_names = {s.name for s, _ in models}

    def mentions(s: Symbol) -> str:
        found = sorted({n for n in model_names if n != s.name and n in s.code})
        return ", ".join(found) or "—"

    facts = ("Real data-model classes, detected from their actual base class (SQLAlchemy/Pydantic/"
             "Django/dataclass patterns): "
             + "; ".join(f"{s.qualified_name} ({base})" for s, base in models[:15]))
    rows = [[s.name, base, mentions(s)] for s, base in models]
    sections = [{"heading": None, "facts": facts,
                "blocks": [_table(["Model", "Base", "Mentions"], rows)]}]

    top = models[0][0]
    sections.append({
        "heading": "Example model",
        "facts": f"One representative model: {top.qualified_name} at {top.file_path}:{top.start_line}.",
        "blocks": [_chips([_symbol_chip(top)]), _code_snippet(files_by_path, top)],
    })
    summary = f"{len(models)} data-model class(es) detected: " + "; ".join(s.qualified_name for s, _ in models[:10])
    return sections, summary


def _focus_pipeline(session: Session, files_by_path: dict[str, File], root_sym: Symbol | None,
                    package: str, repo_name: str, in_pkg, used: set[int]):
    if root_sym is None:
        return None, None
    flow = call_flow(session, root_sym.id, depth=3, fanout=4, max_nodes=12)
    steps = [n for n in sorted(flow["nodes"], key=lambda x: x["depth"])
             if n["id"] not in used and n["id"] != root_sym.id and in_pkg(n["file"])][:6]
    if not steps:
        return None, None
    intro_facts = (f"{root_sym.qualified_name} is the busiest orchestrator in {package or repo_name} — it fans "
                   "out to the most downstream calls. Following its calls (in call order): "
                   + "; ".join(f"{s['qualified_name']} (depth {s['depth']})" for s in steps) + ".")
    sections = [{
        "heading": None,
        "facts": intro_facts,
        "blocks": [
            _chips([_symbol_chip(root_sym)]),
            _diagram(f"{root_sym.name} — execution flow", _mermaid_flow(flow)),
            _code_snippet(files_by_path, root_sym),
        ],
    }, {
        "heading": "Execution steps",
        "facts": ("The downstream steps this path drives, in order of call depth: "
                  + "; ".join(f"{s['qualified_name']} at depth {s['depth']}" for s in steps)),
        "blocks": [
            _list([f"**{s['qualified_name']}** (depth {s['depth']})" for s in steps]),
            _chips([{"kind": "file", "text": s["qualified_name"], "path": s["file"], "line": s["line"]}
                    for s in steps]),
        ],
    }]
    summary = (f"{root_sym.qualified_name} is the busiest orchestrator in {package or repo_name}. "
               "Downstream steps: " + "; ".join(s["qualified_name"] for s in steps))
    return sections, summary


def _focus_community(session: Session, files_by_path: dict[str, File], syms: dict[int, Symbol],
                     repo_id: int, cluster: dict):
    mermaid = _mermaid_community(session, repo_id, cluster)
    if mermaid is None:
        return None, None  # no internal edges to draw — not a useful page
    top_members = cluster["members"][:6]
    top_sym = syms.get(top_members[0]["id"]) if top_members else None
    ies = "y" if cluster["dir_spread"] == 1 else "ies"
    intro_facts = (f"{cluster['label']} is a {cluster['size']}-symbol subsystem centered in "
                   f"{cluster['primary_dir']}, spanning {cluster['dir_spread']} director{ies}. It was found as a "
                   "densely-connected cluster in the call graph (its members call each other far more than they "
                   "call the rest of the codebase). Its key symbols: "
                   + "; ".join(m["qualified_name"] for m in top_members) + ".")
    sections = [{
        "heading": None,
        "facts": intro_facts,
        "blocks": [_diagram(f"{cluster['label']} — internal call graph", mermaid)],
    }, {
        "heading": "Key components",
        "facts": ("The most important symbols in this subsystem: "
                  + "; ".join(m["qualified_name"] for m in top_members)),
        "blocks": [
            _list([f"**{m['qualified_name']}**" for m in top_members]),
            _chips([{"kind": "file", "text": m["qualified_name"], "path": m["path"], "line": m["line"]}
                    for m in top_members]),
            _code_snippet(files_by_path, top_sym),
        ],
    }]
    summary = (f"{cluster['label']} is a {cluster['size']}-symbol subsystem centered in "
               f"{cluster['primary_dir']}, spanning {cluster['dir_spread']} director{ies}. Key symbols: "
               + "; ".join(m["qualified_name"] for m in top_members))
    return sections, summary


# ---------- LLM: structure decision + per-section prose ----------

STRUCTURE_SYS = """You are deciding the page structure for a generated wiki ("Start here") for a
codebase, in the style of DeepWiki. You are given a menu of REAL, already-computed
topics ("focuses") with real facts behind each — you may ONLY choose from this menu,
never invent a topic that isn't listed. Pick 4 to 9 focuses (fewer if the menu is
smaller), in the best reading order for someone new to this codebase, and write a
short, specific title for each page. Return ONLY JSON:
{"pages": [{"focus": "<exact focus key from the menu>", "title": "<short specific title>"}]}"""


def _decide_structure(repo_name: str, menu: dict[str, str], user_id: int | None = None) -> list[tuple[str, str]] | None:
    if not llm_available() or not menu:
        return None
    listing = "\n".join(f"- {key}: {desc}" for key, desc in menu.items())
    user = f"Repo: {repo_name}\n\nAvailable focuses:\n{listing}"
    try:
        raw = call_llm(STRUCTURE_SYS, user, max_tokens=800, temperature=0.2,
                       label="wiki-structure", user_id=user_id)
        data = parse_llm_json(raw)
    except Exception:
        return None
    pages = data.get("pages")
    if not isinstance(pages, list) or not pages:
        return None
    out, seen = [], set()
    for p in pages:
        key = str(p.get("focus", ""))
        if key not in menu or key in seen:
            continue
        seen.add(key)
        title = str(p.get("title") or key).strip()
        out.append((key, title))
    return out or None


PAGE_SYS = """You are a senior engineer writing developer documentation for ONE specific codebase,
in the clear, professional voice of DeepWiki. You are given a page title, a section
heading, and REAL, verified facts about that section. Write flowing technical prose a
new contributor can read to understand this part of the system.

Rules:
- Use ONLY the facts provided. Never invent names, paths, counts, APIs, or behavior
  not stated. If a detail is not given, do not claim it.
- Write 2 to 4 substantial, specific paragraphs. Reference the real symbol, module,
  and file names from the facts, and wrap code identifiers in `backticks`.
- Explain the role each piece plays and how the parts relate — not just a list of names.
- Do NOT output headings, bullet lists, tables, code fences, or diagrams; those are
  added around your text automatically. Return ONLY prose paragraphs."""


def _write_prose(page_title: str, heading: str | None, facts: str, user_id: int | None = None) -> str | None:
    if not llm_available() or not facts:
        return None
    sec_line = f"Section: {heading}\n" if heading else "Section: (page introduction)\n"
    user = f"Page: {page_title}\n{sec_line}\nReal facts (use ONLY these):\n{facts}"
    try:
        text = call_llm(PAGE_SYS, user, max_tokens=900, temperature=0.35,
                        label="wiki-prose", user_id=user_id).strip()
        return text or None
    except Exception:
        return None


# ---------- main entry point ----------

def build_wiki(session: Session, repo_id: int, repo_name: str, user_id: int | None = None) -> dict:
    syms = {s.id: s for s in session.scalars(select(Symbol).where(Symbol.repo_id == repo_id)).all()}
    code_files = session.scalars(
        select(File).where(File.repo_id == repo_id, File.category == "code")).all()
    files_by_path = {f.path: f for f in code_files}
    package = _primary_package(code_files)
    in_pkg = lambda p: bool(package) and (p.startswith(package + "/") or p == package)

    src_sym, dst_sym = aliased(Symbol), aliased(Symbol)
    fan_in: dict[int, float] = dict(session.execute(
        select(SymbolEdge.dst_symbol_id, func.sum(SymbolEdge.confidence))
        .join(src_sym, src_sym.id == SymbolEdge.src_symbol_id)
        .where(SymbolEdge.repo_id == repo_id, SymbolEdge.dst_symbol_id.is_not(None),
               ~src_sym.file_path.like("tests/%"))
        .group_by(SymbolEdge.dst_symbol_id)
    ).all())
    fan_out: dict[int, float] = dict(session.execute(
        select(SymbolEdge.src_symbol_id, func.sum(SymbolEdge.confidence))
        .join(dst_sym, dst_sym.id == SymbolEdge.dst_symbol_id)
        .where(SymbolEdge.repo_id == repo_id, SymbolEdge.edge_type == "call",
               SymbolEdge.dst_symbol_id.is_not(None), ~dst_sym.file_path.like("tests/%"))
        .group_by(SymbolEdge.src_symbol_id)
    ).all())

    arch = build_architecture(session, repo_id, repo_name)
    counts = {"files": len(session.scalars(select(File).where(File.repo_id == repo_id)).all()),
              "symbols": len(syms), "edges": sum(1 for _ in fan_in.items())}
    eps = find_entrypoints(session, repo_id)

    # ---- resolve the "start" symbol (shared by core_engine + pipeline) ----
    pkg_classes = [s for s in syms.values() if s.kind == "class" and in_pkg(s.file_path)]
    want = repo_name.split("/")[-1].lower()
    named = next((s for s in pkg_classes if s.name.lower() == want), None)

    start_sym, start_why = None, ""
    if named:
        start_sym = named
        start_why = (f"It is the primary type of {repo_name} — the main object users construct and the "
                     "hub the package is organized around.")
    else:
        eps_pkg = sorted((e for e in eps if in_pkg(e["path"]) and e["symbol_id"]),
                         key=lambda e: _START_PREF.get(e["kind"], 9))
        if eps_pkg:
            start_sym = syms.get(eps_pkg[0]["symbol_id"])
            start_why = {
                "factory": "It is the application factory — this wires the whole app together.",
                "route": "It is a request entrypoint — where the framework hands control to app code.",
                "main": "It is the program's main() — execution starts here when run directly.",
                "cli": "It is a command entrypoint — invoked from the command line.",
            }.get(eps_pkg[0]["kind"], "It is where execution begins.")
        elif pkg_classes:
            start_sym = max(pkg_classes, key=lambda s: fan_in.get(s.id, 0))
            start_why = "It is the highest fan-in type in the package — the abstraction most other code depends on."

    used: set[int] = {start_sym.id} if start_sym else set()
    ranked = sorted(
        (s for s in syms.values() if s.kind == "class" and in_pkg(s.file_path) and s.id not in used),
        key=lambda s: -fan_in.get(s.id, 0),
    )
    core_models = [s for s in ranked if fan_in.get(s.id, 0) >= 2][:3]
    used.update(s.id for s in core_models)

    def busiest(candidates):
        c = [s for s in candidates if fan_out.get(s.id, 0) >= 2]
        return max(c, key=lambda s: fan_out.get(s.id, 0)) if c else None

    flow_root = None
    if start_sym and start_sym.kind in ("function", "method") and fan_out.get(start_sym.id, 0) >= 2:
        flow_root = start_sym
    elif start_sym and start_sym.kind == "class":
        flow_root = busiest([s for s in syms.values() if s.kind == "method" and in_pkg(s.file_path)
                             and s.qualified_name.startswith(start_sym.name + ".")])
    if flow_root is None:
        flow_root = busiest([s for s in syms.values() if s.kind in ("function", "method") and in_pkg(s.file_path)])

    # ---- build the menu of real, available focuses ----
    menu_desc: dict[str, str] = {}
    sections_by_focus: dict[str, list[dict]] = {}
    titles_by_focus: dict[str, str] = {}
    subtitles_by_focus: dict[str, str] = {}

    def register(key: str, title: str, subtitle: str, result):
        sections, summary = result
        if not sections:
            return
        sections_by_focus[key] = sections
        menu_desc[key] = summary
        titles_by_focus[key] = title
        subtitles_by_focus[key] = subtitle

    register("architecture", "System Architecture", "How the codebase is organized into submodules",
             _focus_architecture(repo_name, arch, eps))
    register("api_surface", "API Surface", "Real HTTP routes, detected from their registration decorators",
             _focus_api_surface(syms, files_by_path))
    register("data_model", "Data Model", "Real ORM/schema classes, detected from their base class",
             _focus_data_model(syms, files_by_path))
    register("getting_started", "Getting Started", "How to set up and run this project",
             _focus_getting_started(session, repo_id, eps))
    register("core_engine", "Core Engine", "The abstractions the rest of the system is built on",
             _focus_core_engine(files_by_path, start_sym, start_why, core_models, fan_in))
    register("pipeline", "Pipeline", "The most-exercised execution path through the code",
             _focus_pipeline(session, files_by_path, flow_root, package, repo_name, in_pkg, used))

    communities = find_communities(session, repo_id).get("clusters", [])
    for i, cluster in enumerate(communities[:MAX_SUBSYSTEM_PAGES]):
        if cluster["size"] < 4:
            continue
        register(f"subsystem_{i}", f"{cluster['label']} Subsystem",
                 "A real, densely-connected cluster in the dependency graph",
                 _focus_community(session, files_by_path, syms, repo_id, cluster))

    # ---- decide page order/titles: LLM first, deterministic fallback always available ----
    decided = _decide_structure(repo_name, menu_desc, user_id)
    if decided is None:
        decided = [(k, titles_by_focus[k]) for k in DEFAULT_ORDER if k in sections_by_focus]
        decided += [(k, titles_by_focus[k]) for k in sections_by_focus if k not in dict(decided)]

    # ---- fan out one grounded prose call per section, concurrently ----
    tasks: list[tuple[str, str | None, str]] = []
    task_keys: list[tuple[str, int]] = []
    for key, title in decided:
        for si, sec in enumerate(sections_by_focus[key]):
            if sec.get("facts"):
                tasks.append((title, sec.get("heading"), sec["facts"]))
                task_keys.append((key, si))

    prose_map: dict[tuple[str, int], str | None] = {}
    if tasks and llm_available():
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(_write_prose, t[0], t[1], t[2], user_id): i for i, t in enumerate(tasks)}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    prose_map[task_keys[i]] = fut.result()
                except Exception:
                    prose_map[task_keys[i]] = None

    # ---- assemble the pages: heading → prose → mechanical artifacts ----
    sections = []
    for key, title in decided:
        blocks: list[dict] = []
        for si, sec in enumerate(sections_by_focus[key]):
            if sec.get("heading"):
                blocks.append(_h2(sec["heading"]))
            prose = prose_map.get((key, si))
            if prose:
                blocks.append(_md(prose))
            blocks += [b for b in sec["blocks"] if b]
        sections.append(_sec(key, title, subtitles_by_focus[key], blocks))

    return {"repo": repo_name, "counts": counts, "sections": sections}
