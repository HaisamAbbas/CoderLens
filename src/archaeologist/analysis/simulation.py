"""Codeflow simulation — the ▶ Play "data movement" engine.

Given the ORDERED walkthrough of real symbols that a codemap already produced
(analysis/codemap.py), this generates a coherent, illustrative EXECUTION TRACE:
for each step, the representative INPUT it receives, the TRANSFORMATION it
applies, and the OUTPUT it produces — with the state flowing between steps so
`output[i] ≈ input[i+1]`, exactly like a real run would look.

Design (mirrors the rest of this feature family):
- The STRUCTURE is real — we only ever simulate symbols that already exist in
  the codemap (real tree-sitter symbols with real source). The model never
  invents nodes, services, or architecture; it only fills in plausible DATA.
- ONE batched LLM call over the whole ordered chain, not N per-node calls. The
  model sees the entire sequence at once, so it can keep the data coherent
  across boundaries — and it costs one request, not N (which matters given the
  minutes-long rate-limit backoff in rag/llm.py). Same batching discipline as
  codemap._concept_cards.
- Honest degradation: no LLM key, or an unparseable/invalid response, falls
  back to a MECHANICAL trace built from each symbol's real signature — labelled
  "representative", never fabricated precision (spec §17).
- The trace shape is execution-source-agnostic (`source` field) so a future
  real-sandbox "Run" can populate the exact same shape without a UI rebuild.

This layer is self-contained: nothing else imports it.
"""

import json
import re

from sqlalchemy import select

from archaeologist.analysis.codemap import classify_role
from archaeologist.models.db import session_scope
from archaeologist.models.entities import File, Repo, Symbol, SymbolEdge
from archaeologist.rag.llm import call_llm, llm_available, parse_llm_json

MAX_SIM_NODES = 14   # bound the batched call; longer walkthroughs are truncated
MAX_SRC_CHARS = 900  # per-symbol source sent to the model — enough to ground it
_CACHE: dict[tuple, dict] = {}
_CACHE_MAX = 64

SIM_SYS = """You are a code execution SIMULATOR. You are given an ORDERED walkthrough of REAL
functions/methods from ONE repository — with their real signatures, docstrings and source. Produce
a plausible, COHERENT execution trace showing how DATA moves through the chain.

This is an ILLUSTRATION, never a real run. Generate REPRESENTATIVE input/output data appropriate to
each function's actual parameter names/types, return type and logic.

Rules:
- FLOWING STATE: each step's `input` should correspond to the previous step's `output`
  (output[i] ≈ input[i+1]) UNLESS the code clearly transforms or branches otherwise. Never invent a
  jump in scale (10 records -> 900,000) the code does not justify.
- GROUND every value in what the code shows: parameter names/types, return type, docstring,
  referenced classes, conditionals, comprehensions. If a User has name/age/email, produce a real
  user; never {"foo": "bar"}.
- NEVER invent components, services, endpoints, counts or behavior the code does not show. If a step
  calls a DB or external API, REPRESENT a plausible response — do not claim it was really called.
- REFLECT real branches: for `if user.is_admin: ...`, pick a representative path and set
  `branch_taken`. For `[x for x in items if x.active]`, show inactive items disappearing.
- If a step lacks enough info for specific values, set `confidence` to "representative" and use a
  labelled generic ("User-like object") instead of fabricating precise numbers.
- BE COMPACT: `fields` is a small PREVIEW — at most ~6 keys, every value SHORT (<=12 words).
  SUMMARIZE large data instead of pasting it (a 40-line schema -> "10 node labels + common props");
  never paste multi-line blobs, full source, or long strings. Keep the whole reply tight.
- MAKE CHANGE VISIBLE: prefer concrete, comparable fields — counts, ids, sizes, flags, metrics — and
  REUSE THE SAME FIELD KEY across steps for data that persists, so the reader can see it change
  (e.g. one step "records": 12430, the next "records": 11932). Keep a field's value type stable
  (a number stays a number). This makes the input->output delta legible.
- EXPLAIN THE CONTRIBUTION: for each step, write `contribution` — 1-2 plain-English sentences a
  newcomer to THIS codebase would value: what this component actually contributes to the project and
  how it fits the overall flow. Ground it in the real name, docstring, what it calls, and its
  position in the sequence (e.g. "The entry point for retrieval: it fans the query out to BM25 and
  vector search, then fuses them with RRF so later steps get a single ranked list."). Explain its
  ROLE, don't just restate the data movement, and never invent behavior the code doesn't show.

Return ONLY JSON, no markdown:
{"scenario": "<one sentence naming the concrete scenario being simulated>",
 "steps": [
   {"node_id": <the exact id given>,
    "contribution": "<1-2 sentences: what this component contributes to the project & how it fits>",
    "input":  {"summary": "<=10 words", "fields": { <representative key/values, may be nested> }},
    "transformation": "<=18 words: what this step does to the data",
    "output": {"summary": "<=10 words", "fields": { <representative key/values> }},
    "important_variables": { <optional representative vars, or omit> },
    "branch_taken": "<which branch was taken, or null>",
    "confidence": "high" | "representative",
    "notes": ["<=2 short, honest notes"]}
 ]}
"steps" MUST have EXACTLY one entry per given symbol, in the SAME order, echoing the given node_id."""


def _sig_params(signature: str | None) -> list[str]:
    """Best-effort parameter names from a signature string, skipping self/cls —
    used to build a grounded (not fabricated) fallback input shape."""
    if not signature:
        return []
    m = re.search(r"\((.*)\)", signature, re.S)
    if not m:
        return []
    out: list[str] = []
    for raw in m.group(1).split(","):
        name = raw.strip().split(":")[0].split("=")[0].strip().lstrip("*")
        if name and name not in ("self", "cls") and name.isidentifier():
            out.append(name)
    return out


def _return_type(signature: str | None) -> str:
    if not signature:
        return ""
    m = re.search(r"->\s*([^:]+):?\s*$", signature.strip())
    return m.group(1).strip() if m else ""


def _mechanical_step(sym: Symbol) -> dict:
    """A grounded-but-generic step for when the LLM is unavailable or its output
    can't be trusted. Honest: representative shapes from the real signature,
    never invented values."""
    params = _sig_params(sym.signature)
    _role, _icon, role_label = classify_role(sym.qualified_name, sym.file_path, sym.kind)
    ret = _return_type(sym.signature)
    return {
        "node_id": sym.id,
        "contribution": f"A {role_label.lower()} in {sym.file_path.split('/')[-1]}"
                        + (f" — {sym.docstring.strip().splitlines()[0][:160]}" if sym.docstring else
                           f"; its exact role is inferred from its name/signature (no LLM available)."),
        "input": {
            "summary": (", ".join(params) if params else "no explicit arguments"),
            "fields": {p: "<value>" for p in params},
        },
        "transformation": f"{role_label} — {sym.name}() processes its input.",
        "output": {
            "summary": (f"{ret}" if ret else f"result of {sym.name}()"),
            "fields": ({ret: "<value>"} if ret else {}),
        },
        "important_variables": {},
        "branch_taken": None,
        "confidence": "representative",
        "notes": ["Representative shape from the signature — no LLM available to generate values."],
    }


def _load_context(node_ids: list[int]):
    """Load the ordered symbols + a little grounding (source, callees) inside one
    short-lived session. Returns (ordered_syms, head_sha, callees_by_id)."""
    with session_scope() as session:
        repo = session.scalar(select(Repo).order_by(Repo.id.desc()))
        head_sha = repo.head_sha if repo else None

        found = {s.id: s for s in session.scalars(select(Symbol).where(Symbol.id.in_(node_ids)))}
        ordered = [found[i] for i in node_ids if i in found]
        ordered = ordered[:MAX_SIM_NODES]
        ids = [s.id for s in ordered]

        # Real callee names per symbol — extra grounding for the model (what each
        # step hands off to), never invented.
        callees: dict[int, list[str]] = {i: [] for i in ids}
        if ids:
            rows = session.execute(
                select(SymbolEdge.src_symbol_id, SymbolEdge.dst_name).where(
                    SymbolEdge.repo_id == (repo.id if repo else -1),
                    SymbolEdge.src_symbol_id.in_(ids),
                    SymbolEdge.edge_type == "call",
                )
            ).all()
            for src, dst_name in rows:
                if dst_name and len(callees.get(src, [])) < 6 and dst_name not in callees[src]:
                    callees[src].append(dst_name)

        # Detach the fields we need so we can use them after the session closes
        # (the LLM call below must not hold a DB connection — see codemap.py).
        detached = []
        for s in ordered:
            src = s.code
            if not src:  # symbols may not carry inline code — slice the file
                f = session.scalar(
                    select(File).where(File.repo_id == s.repo_id, File.path == s.file_path))
                if f and f.content:
                    lines = f.content.splitlines()
                    src = "\n".join(lines[max(0, (s.start_line or 1) - 1): s.end_line or s.start_line])
            detached.append({
                "id": s.id, "qualified_name": s.qualified_name, "name": s.name, "kind": s.kind,
                "file": s.file_path, "line": s.start_line, "signature": s.signature,
                "docstring": (s.docstring or "").strip(),
                "source": (src or "")[:MAX_SRC_CHARS],
                "callees": callees.get(s.id, []),
                "_sym": s,  # kept for the mechanical fallback (already loaded)
            })
    return detached, head_sha


def _build_user_prompt(ctx: list[dict], question: str) -> str:
    parts: list[str] = []
    if question:
        parts.append(f"WHAT THE WALKTHROUGH ANSWERS: {question}\n")
    parts.append("ORDERED WALKTHROUGH (simulate the data flowing through these, in order):\n")
    for i, c in enumerate(ctx, 1):
        block = [f"[{i}] node_id={c['id']}  {c['qualified_name']} ({c['kind']})  {c['file']}:{c['line']}"]
        if c["signature"]:
            block.append(f"    signature: {c['signature']}")
        if c["docstring"]:
            block.append(f"    docstring: {c['docstring'].splitlines()[0][:160]}")
        if c["callees"]:
            block.append(f"    calls: {', '.join(c['callees'])}")
        if c["source"]:
            block.append("    source:\n" + "\n".join("      " + ln for ln in c["source"].splitlines()))
        parts.append("\n".join(block))
    return "\n".join(parts)


def _coerce_steps(data: dict, ctx: list[dict]) -> tuple[list[dict], int] | None:
    """Validate + align the model's steps to our real nodes, POSITIONALLY (the
    walkthrough order is authoritative; the model's echoed node_id is only a
    sanity check). Tolerant by design: a shorter list (e.g. a truncated reply)
    is padded at the tail with honest mechanical steps rather than discarding
    the whole trace; a longer list is truncated. Returns (steps, llm_count) —
    llm_count is how many steps actually came from the model — or None if the
    reply has no usable steps at all.

    `important_variables` and nested `fields` are clamped in size so one
    runaway value (the model occasionally dumps a whole schema) can't bloat the
    payload the UI has to render."""
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        return None
    out: list[dict] = []
    llm_count = 0
    for idx, c in enumerate(ctx):
        raw = steps[idx] if idx < len(steps) else None
        if not isinstance(raw, dict):  # missing/garbage at this position → honest fallback
            out.append(_mechanical_step(c["_sym"]))
            continue
        inp = raw.get("input") if isinstance(raw.get("input"), dict) else {}
        out_ = raw.get("output") if isinstance(raw.get("output"), dict) else {}
        conf = raw.get("confidence")
        out.append({
            "node_id": c["id"],
            "contribution": str(raw.get("contribution", ""))[:400],
            "input": {"summary": str(inp.get("summary", ""))[:120], "fields": _clamp_fields(inp.get("fields"))},
            "transformation": str(raw.get("transformation", ""))[:240],
            "output": {"summary": str(out_.get("summary", ""))[:120], "fields": _clamp_fields(out_.get("fields"))},
            "important_variables": _clamp_fields(raw.get("important_variables")),
            "branch_taken": (str(raw["branch_taken"])[:120] if raw.get("branch_taken") else None),
            "confidence": conf if conf in ("high", "representative") else "representative",
            "notes": [str(n)[:200] for n in raw.get("notes", []) if n][:3],
        })
        llm_count += 1
    return out, llm_count


def _clamp_fields(v, max_keys: int = 8, max_val: int = 300) -> dict:
    """Keep a `fields`/`important_variables` object small: at most `max_keys`
    entries, each value stringified-and-truncated if it's a big blob (the model
    occasionally pastes an entire schema despite being told not to)."""
    if not isinstance(v, dict):
        return {}
    out: dict = {}
    for k, val in list(v.items())[:max_keys]:
        if isinstance(val, (dict, list)):
            s = str(val)
            out[str(k)] = val if len(s) <= max_val else s[:max_val] + "…"
        elif isinstance(val, str) and len(val) > max_val:
            out[str(k)] = val[:max_val] + "…"
        else:
            out[str(k)] = val
    return out


def _salvage_json(raw: str) -> dict | None:
    """Recover a usable object from a TRUNCATED reply: pull the scenario and any
    COMPLETE step objects out of the (possibly unterminated) steps array via a
    string-aware brace scan. Lets a reply cut off mid-object still yield every
    step that finished, instead of the whole trace collapsing to mechanical."""
    scen = ""
    m = re.search(r'"scenario"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    if m:
        scen = m.group(1)
    i = raw.find('"steps"')
    lb = raw.find("[", i) if i >= 0 else -1
    if lb < 0:
        return None
    steps: list = []
    depth = 0
    start: int | None = None
    in_str = False
    esc = False
    for j in range(lb + 1, len(raw)):
        ch = raw[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = j
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    steps.append(json.loads(raw[start:j + 1]))
                except Exception:
                    pass
                start = None
        elif ch == "]" and depth == 0:
            break
    return {"scenario": scen, "steps": steps} if steps else None


def simulate_flow(node_ids: list[int], question: str = "", user_id: int | None = None) -> dict:
    """Generate an illustrative execution trace for an ordered walkthrough.

    Returns:
      {"scenario": str, "simulated": True, "source": "llm-simulated"|"mechanical",
       "truncated": bool, "steps": [ <one per node, in order> ]}
    """
    node_ids = [int(i) for i in node_ids][: MAX_SIM_NODES + 8]
    if not node_ids:
        return {"scenario": "", "simulated": True, "source": "mechanical",
                "truncated": False, "steps": []}

    ctx, head_sha = _load_context(node_ids)
    if not ctx:
        return {"scenario": "", "simulated": True, "source": "mechanical",
                "truncated": False, "steps": []}
    truncated = len([i for i in node_ids if i]) > len(ctx)

    key = (head_sha, tuple(c["id"] for c in ctx), question.strip())
    if key in _CACHE:
        return _CACHE[key]

    steps: list[dict] | None = None
    scenario, source = "", "mechanical"
    # Scale the token budget to the chain length + leave headroom, so the JSON
    # doesn't get cut off mid-object (which was collapsing whole traces to the
    # mechanical fallback). Compact-fields instructions in SIM_SYS keep it in range.
    max_tokens = min(5600, 1300 + 380 * len(ctx))

    if llm_available():
        user = _build_user_prompt(ctx, question)
        # one generous batched call; a stricter retry if the first is malformed
        for attempt in range(2):
            try:
                sys = SIM_SYS + ("\n\nIMPORTANT: your previous reply was cut off or invalid — be MORE "
                                 "concise so the whole JSON fits, one step per symbol." if attempt else "")
                raw = call_llm(sys, user, max_tokens=max_tokens, temperature=0.4,
                               label="codemap-simulate", user_id=user_id)
                data = parse_llm_json(raw)
                if not data:
                    data = _salvage_json(raw)  # truncated reply → keep the complete steps
                coerced = _coerce_steps(data, ctx) if data else None
                if coerced:
                    steps, llm_count = coerced
                    scenario = str(data.get("scenario", ""))[:200]
                    source = "llm-simulated" if llm_count else "mechanical"
                    # good enough if the model covered (most of) the chain; else retry once
                    if llm_count >= max(1, len(ctx) - 1):
                        break
            except Exception:
                steps = None

    if not steps:  # honest mechanical fallback
        steps = [_mechanical_step(c["_sym"]) for c in ctx]
        if not scenario:
            scenario = "Representative data flow (generated from signatures — no LLM available)." \
                if not llm_available() else "Representative data flow through the walkthrough."

    result = {"scenario": scenario, "simulated": True, "source": source,
              "truncated": truncated, "steps": steps}

    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = result
    return result
