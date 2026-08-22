"""Codemap — a query-scoped, LLM-curated but graph-grounded map of the code.

Given a question ("how does X work"), we:
  1. retrieve candidate code symbols (hybrid search over the code index),
  2. pull their REAL call edges + 1-hop neighbors from the dependency graph,
  3. ask the LLM to *select, order and annotate* the relevant nodes + write a
     short narrative — but the nodes and edges themselves come from tree-sitter,
     not the model, so the map can't hallucinate structure.

If no LLM key is available it degrades to a mechanical map (candidates + their
neighbors, ordered by hop distance) with no notes/narrative.

Self-contained: nothing here is imported by the other features.
"""

import re
from collections import Counter, defaultdict, deque

from sqlalchemy import or_, select

from archaeologist.indexing import code_index
from archaeologist.indexing.opensearch_client import get_client
from archaeologist.models.db import session_scope
from archaeologist.models.entities import File, Symbol, SymbolEdge
from archaeologist.rag.llm import call_llm, llm_available, parse_llm_json
from archaeologist.retrieval.embeddings import get_embedder
from archaeologist.retrieval.hybrid import rrf

CURATE_SYS = """You curate a "codemap": a focused map of the code that answers a question.
You get the QUESTION and CANDIDATES (indexed real code symbols) plus their real call EDGES.
Select ONLY the symbols relevant to the question, order them by execution/logical step, add a
short note per symbol, and write a brief narrative. Do NOT invent symbols. Return ONLY JSON:
{"title": "<=6 words", "narrative": "2-4 sentences on how it works",
 "keep": [{"i": <candidate index>, "step": <int from 1>, "note": "<=12 words"}]}"""

EXPLAIN_EDGE_SYS = """You explain ONE real call edge in a dependency graph: why does the
caller call the callee? You are given both symbols' names/kinds and the caller's real
source code (which contains the actual call). Answer in 1-2 plain sentences, grounded
ONLY in what the code shows — never invent behavior. No JSON, no markdown, no citations."""

EXTEND_SYS = """A user is looking at a code map and asked a follow-up question. You are
given the newly found symbols (from the real dependency graph) that are being added to
their map. In 1-2 plain sentences, explain how these relate to the follow-up question.
Ground this ONLY in the symbol names/kinds/paths given — never invent behavior. No JSON."""

# "Physical Code" — the actual feature: not a name-pattern classifier, but the
# LLM looking at the REAL walkthrough (real names, real docstrings, real code)
# and naming the real domain concept each step represents (Ingestion, Chunking,
# Embedding, Vector Search, ...) so the map reads like a whiteboard explanation
# of what this specific codebase does — not a generic "Function" label. Still
# grounded: it may only describe what the given facts show, never invent
# counts/behavior, and the icon is constrained to a fixed vocabulary so the UI
# can never receive garbage. When the LLM is unavailable, `classify_role`'s
# mechanical guess is the fallback — degraded, but never broken or empty.
CONCEPT_ICONS = "📚📄🔪🧩🔢🧠🔍💾⚡📥👷🌐🚪🔧🧮✅❌📊💬🔗⚙️👤📨"
CONCEPT_SYS = f"""You explain a code walkthrough as a sequence of real domain concepts, the
way a whiteboard diagram would (e.g. for a RAG pipeline: Ingestion -> Chunking -> Embedding
-> Vector Search -> Answer). You are given the REAL, ordered symbols in this walkthrough —
name, kind, file, and (when available) a docstring or source excerpt. For EACH symbol, in
the SAME order, produce one concept card describing what it actually does in THIS codebase.

Rules:
- "concept": a short (1-3 word) domain concept name for what this step does — name the real
  thing (e.g. "Chunking", "Embedding", "Cache Lookup"), not a generic label like "Function".
- "icon": exactly ONE character from this set, whichever best fits the concept: {CONCEPT_ICONS}
- "explainer": ONE plain sentence (<=18 words) grounded ONLY in the given facts. Never invent
  counts, sample data, or behavior the facts don't show. If nothing domain-specific is
  knowable beyond the code's structure, describe it plainly rather than forcing a fit.
Return ONLY JSON: {{"cards": [{{"concept": "...", "icon": "...", "explainer": "..."}}, ...]}}
— "cards" MUST have exactly one entry per symbol, in the same order given."""


def _concept_cards(ordered_syms: list[Symbol]) -> list[dict] | None:
    """One concept card per symbol, in order, or None if the LLM is unavailable
    or its response can't be trusted — callers fall back to `classify_role`."""
    if not llm_available() or not ordered_syms:
        return None
    lines = []
    for i, s in enumerate(ordered_syms):
        doc = (s.docstring or "").strip().splitlines()[0][:140] if s.docstring else ""
        lines.append(f"[{i}] {s.qualified_name} ({s.kind}) — {s.file_path}:{s.start_line}"
                     + (f" — docstring: {doc}" if doc else ""))
    user = "Ordered walkthrough symbols:\n" + "\n".join(lines)
    try:
        data = parse_llm_json(call_llm(CONCEPT_SYS, user, max_tokens=900, temperature=0.3, label="codemap-concepts"))
    except Exception:
        return None
    cards = data.get("cards")
    if not isinstance(cards, list) or len(cards) != len(ordered_syms):
        return None
    out = []
    for c in cards:
        if not isinstance(c, dict):
            return None
        icon = str(c.get("icon", "")).strip()
        if icon not in CONCEPT_ICONS:
            icon = "⚙️"
        out.append({"concept": str(c.get("concept") or "Step").strip()[:30],
                    "icon": icon, "explainer": str(c.get("explainer", "")).strip()[:160]})
    return out


def _candidate_ids(session, question, client, embedder, k=10) -> list[int]:
    lists = [code_index.bm25_hits(client, question, 15)]
    if embedder is not None:
        lists.append(code_index.knn_hits(client, embedder.embed_query(question), 15))
    ids: list[int] = []
    for _id, _score, _src in rrf(lists):
        try:
            sid = int(_id)
        except (TypeError, ValueError):
            continue
        if sid not in ids:
            ids.append(sid)
        if len(ids) >= k:
            break
    return ids


# "Physical Code" — a mechanical, rule-based classifier that gives each symbol
# a real-world role (validator, database, cache, ...) instead of a generic
# box, purely from name/path patterns already visible in the qualified name
# and file path. Never LLM-guessed, so it can't invent a role a symbol
# doesn't actually play — same discipline as the rest of this file. Order
# matters: earlier, more specific patterns are checked first.
# Identifier-boundary lookaround, NOT `\b`/`(^|_)`/`(_|$)`: qualified names mix
# dots ("Class.method"), underscores, and (here) a trailing " <file_path>" —
# none of which a plain `\b` or start/underscore anchor reliably catches,
# since `_` counts as a word character to `\b` and a literal `(^|_)` misses a
# preceding "." entirely. These lookarounds treat any non-alphanumeric
# (., _, /, space, string start/end) as a valid boundary instead.
_B, _E = r"(?<![A-Za-z0-9])", r"(?![A-Za-z0-9])"
_ROLE_RULES: list[tuple[str, str, str, "re.Pattern"]] = [
    ("test", "🧪", "Test", re.compile(rf"(^|/)tests?/|{_B}test_", re.I)),
    ("cache", "⚡", "Cache", re.compile(rf"cach(e|ing)|redis|memoiz|{_B}lru{_E}", re.I)),
    ("queue", "📥", "Queue", re.compile(rf"{_B}queue{_E}|celery|kafka|{_B}mq{_E}", re.I)),
    ("worker", "👷", "Worker", re.compile(rf"{_B}worker{_E}|{_B}consumer{_E}|task_runner", re.I)),
    ("validator", "🔍", "Validator", re.compile(rf"{_B}(validate|verify|sanitiz)|validator", re.I)),
    ("parser", "🧹", "Parser", re.compile(rf"{_B}(parse|normali[sz]e|transform|extract){_E}|{_B}parser{_E}", re.I)),
    ("calculator", "🧮", "Calculator", re.compile(rf"{_B}(calculate|compute|predict|estimate|rank){_E}|{_B}model{_E}", re.I)),
    ("database", "💾", "Database", re.compile(rf"{_B}repositor|{_B}backend{_E}|{_B}storage{_E}|{_B}(save|persist|fetch|query){_E}", re.I)),
    ("api", "🚪", "API", re.compile(rf"{_B}route{_E}|{_B}endpoint{_E}|dispatch|{_B}view{_E}|{_B}handler{_E}", re.I)),
]


def classify_role(qualified_name: str, file_path: str, kind: str) -> tuple[str, str, str]:
    """Returns (role, icon, label) for a symbol — a real-world archetype
    inferred mechanically from its name and location, never invented."""
    text = f"{qualified_name} {file_path}"
    for role, icon, label, pat in _ROLE_RULES:
        if pat.search(text):
            return role, icon, label
    if kind == "class":
        return "object", "📦", "Object"
    if kind == "method":
        return "method", "🔗", "Method"
    return "function", "⚙️", "Function"


def _node(s: Symbol, step: int, note: str, card: dict | None = None) -> dict:
    role, icon, role_label = classify_role(s.qualified_name, s.file_path, s.kind)
    n = {"id": s.id, "qualified_name": s.qualified_name, "name": s.name, "kind": s.kind,
         "file": s.file_path, "line": s.start_line, "step": step, "note": note,
         "role": role, "icon": icon, "role_label": role_label}
    if card:  # LLM concept card, when available — overrides the mechanical icon/label
        n["icon"] = card["icon"]
        n["concept"] = card["concept"]
        n["explainer"] = card["explainer"]
    return n


def build_codemap(question: str, max_nodes: int = 22) -> dict:
    client = get_client()
    embedder = get_embedder()
    empty = {"question": question, "title": "", "narrative": "", "nodes": [], "edges": [],
             "curated": False}

    # All DB reads happen here, inside one short-lived session — then it closes
    # *before* the LLM call below. Gemini's rate-limit backoff can retry for
    # minutes (see rag/llm.py); holding a transaction open across that would
    # pin a connection from the pool for the whole wait, and a couple of
    # rate-limited requests would be enough to starve every other endpoint
    # waiting on the same pool. Nothing after this block issues a new query —
    # it's pure Python over already-loaded Symbol objects.
    with session_scope() as session:
        cand_ids = _candidate_ids(session, question, client, embedder)
        if not cand_ids:
            return {**empty, "title": "No matching code"}

        cands = {s.id: s for s in session.scalars(select(Symbol).where(Symbol.id.in_(cand_ids)))}
        ordered = [cands[i] for i in cand_ids if i in cands]
        repo_id = ordered[0].repo_id

        # real edges touching the candidates (+ discover 1-hop neighbors)
        rows = session.execute(
            select(SymbolEdge.src_symbol_id, SymbolEdge.dst_symbol_id, SymbolEdge.confidence).where(
                SymbolEdge.repo_id == repo_id, SymbolEdge.dst_symbol_id.is_not(None),
                or_(SymbolEdge.src_symbol_id.in_(list(cands)), SymbolEdge.dst_symbol_id.in_(list(cands))))
        ).all()
        neighbors = [n for n in {x for s, d, _c in rows for x in (s, d)} if n not in cands]
        keep_ids = set(cands) | set(neighbors[: max(0, max_nodes - len(cands))])
        syms = {s.id: s for s in session.scalars(select(Symbol).where(Symbol.id.in_(keep_ids)))}

        edges = _dedup([{"source": s, "target": d, "confidence": c}
                        for s, d, c in rows if s in syms and d in syms and s != d])

    note_by: dict[int, str] = {}
    step_by: dict[int, int] = {}
    title, narrative, curated = "", "", False

    if llm_available():
        id2idx = {s.id: i for i, s in enumerate(ordered)}
        lines = [
            f"[{i}] {s.qualified_name} ({s.kind}) {s.file_path}:{s.start_line}"
            f" — {(s.docstring or '').strip().splitlines()[0][:80] if s.docstring else ''}"
            for i, s in enumerate(ordered)
        ]
        edge_txt = [f"{id2idx[e['source']]}->{id2idx[e['target']]}"
                    for e in edges if e["source"] in id2idx and e["target"] in id2idx]
        user = f"QUESTION: {question}\n\nCANDIDATES:\n" + "\n".join(lines) + "\n\nEDGES: " + ", ".join(edge_txt)
        try:
            data = parse_llm_json(call_llm(CURATE_SYS, user, max_tokens=700, label="codemap"))
            title, narrative = data.get("title", ""), data.get("narrative", "")
            selected: set[int] = set()
            for k in data.get("keep", []):
                i = k.get("i")
                if isinstance(i, int) and 0 <= i < len(ordered):
                    sid = ordered[i].id
                    selected.add(sid)
                    step_by[sid] = int(k.get("step", 1))
                    note_by[sid] = str(k.get("note", ""))
            if selected:
                syms = {i: syms[i] for i in selected if i in syms}
                edges = [e for e in edges if e["source"] in syms and e["target"] in syms]
                curated = True
        except Exception:
            curated = False

    if not step_by:  # mechanical fallback: hop distance from the top candidate
        step_by = _bfs_steps(ordered[0].id, edges, syms)

    # Final walkthrough order, decided first — concept cards are generated for
    # THIS exact order so the LLM sees (and narrates) the real step sequence.
    final_syms = sorted(syms.values(), key=lambda s: (step_by.get(s.id, 1), s.qualified_name))
    cards = _concept_cards(final_syms)
    card_by_id = {s.id: c for s, c in zip(final_syms, cards)} if cards else {}

    nodes = [_node(s, step_by.get(s.id, 1), note_by.get(s.id, ""), card_by_id.get(s.id)) for s in final_syms]
    return {"question": question, "title": title or question[:60],
            "narrative": narrative, "nodes": nodes, "edges": edges, "curated": curated}


def build_file_codemap(file_path: str, repo_id: int, max_nodes: int = 30) -> dict:
    """A Codemap-shaped walkthrough of the functions/methods/classes actually
    DEFINED in one file — not a free-text question, so there's nothing to
    curate: every symbol the file defines is in scope by construction, plus
    its most strongly-connected neighbors for cross-file context. Reuses the
    exact same node/edge shape as `build_codemap`, so CodemapView/PhysicalFlow
    render it with zero new frontend code. Skips the LLM concept-card pipeline
    by default (drilling into a file should be instant) — nodes still get the
    free, instant mechanical role/icon from `classify_role`."""
    empty = {"question": file_path, "title": file_path.split("/")[-1], "narrative": "",
             "nodes": [], "edges": [], "curated": False}
    with session_scope() as session:
        own = list(session.scalars(
            select(Symbol).where(Symbol.repo_id == repo_id, Symbol.file_path == file_path,
                                 Symbol.kind.in_(["class", "method", "function"]))
        ))
        if not own:
            return empty
        own_ids = {s.id for s in own}

        rows = session.execute(
            select(SymbolEdge.src_symbol_id, SymbolEdge.dst_symbol_id, SymbolEdge.confidence).where(
                SymbolEdge.repo_id == repo_id, SymbolEdge.dst_symbol_id.is_not(None),
                or_(SymbolEdge.src_symbol_id.in_(own_ids), SymbolEdge.dst_symbol_id.in_(own_ids)))
        ).all()

        # Cross-file context, same "keep the most strongly connected"
        # neighbor-budget pattern as build_codemap/extend_codemap.
        strength: Counter = Counter()
        for s, d, _c in rows:
            if s in own_ids and d not in own_ids:
                strength[d] += 1
            elif d in own_ids and s not in own_ids:
                strength[s] += 1
        budget = max(0, max_nodes - len(own_ids))
        keep_neighbors = {nid for nid, _ in strength.most_common(budget)}
        keep_ids = own_ids | keep_neighbors
        syms = {s.id: s for s in session.scalars(select(Symbol).where(Symbol.id.in_(keep_ids)))}
        edges = _dedup([{"source": s, "target": d, "confidence": c}
                        for s, d, c in rows if s in syms and d in syms and s != d])

    # Step order: BFS from the file's busiest own symbol (real caller->callee
    # chains read as sequential steps — an honest execution-order signal, not
    # a guess); anything the BFS never reaches (no internal calls at all —
    # independent helpers) is grouped after, in real source order by line
    # number, rather than implying a flow relationship that isn't there.
    # (Not `_bfs_steps` — it defaults every unreached symbol to step 1, which
    # would scatter unrelated helpers into the root's own column instead of a
    # clean trailing group.)
    fan_out: Counter = Counter(e["source"] for e in edges if e["source"] in own_ids)
    root = fan_out.most_common(1)[0][0] if fan_out else None
    step_by: dict[int, int] = {}
    if root is not None:
        adj: dict[int, list[int]] = defaultdict(list)
        for e in edges:
            adj[e["source"]].append(e["target"])
        step_by[root] = 1
        q = deque([root])
        while q:
            u = q.popleft()
            for v in adj[u]:
                if v in syms and v not in step_by:
                    step_by[v] = step_by[u] + 1
                    q.append(v)
    trailing = max(step_by.values(), default=0) + 1
    for sid in sorted((i for i in syms if i not in step_by), key=lambda i: syms[i].start_line):
        step_by[sid] = trailing

    final_syms = sorted(syms.values(), key=lambda s: (step_by.get(s.id, 1), s.start_line))
    nodes = [_node(s, step_by.get(s.id, 1), "") for s in final_syms]
    return {"question": file_path, "title": file_path.split("/")[-1], "narrative": "",
            "nodes": nodes, "edges": edges, "curated": False}


def _dedup(edges: list[dict]) -> list[dict]:
    seen, out = set(), []
    for e in edges:
        key = (e["source"], e["target"])
        if key not in seen:
            seen.add(key)
            out.append(e)
    return out


def _snippet(session, sym: Symbol, max_lines: int = 40) -> str:
    """The real source of a symbol, sliced from the indexed file — used to
    ground the edge explanation in actual code, never invented."""
    f = session.scalar(select(File).where(File.repo_id == sym.repo_id, File.path == sym.file_path))
    if not f or not f.content:
        return ""
    lines = f.content.splitlines()
    start = max(1, sym.start_line or 1)
    end = min(len(lines), sym.end_line or start)
    body = lines[start - 1:end][:max_lines]
    return "\n".join(body)


def explain_edge(source_id: int, target_id: int, question: str = "") -> dict:
    """Why does `source` call `target`? Grounded in the caller's real source
    (which contains the actual call) — degrades to a plain factual sentence
    (still 100% true, just not elaborated) when no LLM is available."""
    with session_scope() as session:
        src = session.get(Symbol, source_id)
        dst = session.get(Symbol, target_id)
        if src is None or dst is None:
            return {"text": "", "error": "Symbol not found."}
        fallback = f"`{src.qualified_name}` calls `{dst.qualified_name}` directly, per the indexed call graph."
        if not llm_available():
            return {"text": fallback}
        snippet = _snippet(session, src)
        user = (
            f"Caller: {src.qualified_name} ({src.kind}) at {src.file_path}:{src.start_line}\n"
            f"Callee: {dst.qualified_name} ({dst.kind}) at {dst.file_path}:{dst.start_line}\n"
            + (f"Follow-up context (the original question this map answers): {question}\n" if question else "")
            + f"\nCaller's source code:\n{snippet}"
        )
    try:
        text = call_llm(EXPLAIN_EDGE_SYS, user, max_tokens=200, label="codemap-explain-edge").strip()
        return {"text": text or fallback}
    except Exception:
        return {"text": fallback}


def extend_codemap(question: str, existing_ids: list[int], max_new: int = 10) -> dict:
    """Grow an existing codemap with a follow-up question — finds new, real
    symbols relevant to the follow-up and any real edges connecting them to
    what's already on the map, WITHOUT touching the existing nodes/edges."""
    client = get_client()
    embedder = get_embedder()
    empty: dict = {"question": question, "note": "", "nodes": [], "edges": []}
    existing = set(existing_ids)

    with session_scope() as session:
        cand_ids = [i for i in _candidate_ids(session, question, client, embedder, k=max_new + 4) if i not in existing]
        cand_ids = cand_ids[:max_new]
        if not cand_ids:
            return empty
        cands = {s.id: s for s in session.scalars(select(Symbol).where(Symbol.id.in_(cand_ids)))}
        if not cands:
            return empty
        repo_id = next(iter(cands.values())).repo_id

        universe_seed = list(cands) + list(existing)
        rows = session.execute(
            select(SymbolEdge.src_symbol_id, SymbolEdge.dst_symbol_id, SymbolEdge.confidence).where(
                SymbolEdge.repo_id == repo_id, SymbolEdge.dst_symbol_id.is_not(None),
                or_(SymbolEdge.src_symbol_id.in_(universe_seed), SymbolEdge.dst_symbol_id.in_(universe_seed)))
        ).all()
        touched_new = {x for s, d, _c in rows for x in (s, d) if x not in existing}
        keep_new = set(cands) | set(list(touched_new)[: max(0, max_new - len(cands))])
        keep_new = set(list(keep_new)[:max_new])
        syms = {s.id: s for s in session.scalars(select(Symbol).where(Symbol.id.in_(keep_new)))}

        universe = existing | set(syms)
        edges = _dedup([{"source": s, "target": d, "confidence": c}
                        for s, d, c in rows if s in universe and d in universe and s != d
                        and (s in syms or d in syms)])

    note = ""
    if llm_available() and syms:
        lines = [f"{s.qualified_name} ({s.kind}) {s.file_path}:{s.start_line}" for s in syms.values()]
        user = f"Follow-up question: {question}\n\nNewly found symbols:\n" + "\n".join(lines)
        try:
            note = call_llm(EXTEND_SYS, user, max_tokens=150, label="codemap-extend").strip()
        except Exception:
            note = ""

    nodes = [_node(s, 0, "") for s in syms.values()]  # client assigns display step relative to the focus node
    return {"question": question, "note": note, "nodes": nodes, "edges": edges}


def _bfs_steps(root: int, edges: list[dict], syms: dict) -> dict[int, int]:
    adj: dict[int, list[int]] = defaultdict(list)
    for e in edges:
        adj[e["source"]].append(e["target"])
    steps = {root: 1}
    q = deque([root])
    while q:
        u = q.popleft()
        for v in adj[u]:
            if v in syms and v not in steps:
                steps[v] = steps[u] + 1
                q.append(v)
    for sid in syms:  # anything unreached sits at the first step
        steps.setdefault(sid, 1)
    return steps
