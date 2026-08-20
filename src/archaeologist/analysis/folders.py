"""Folder-level heat — which directories are the 'hot core' (the rest of the
system leans on them) versus leaf utilities (they lean on others).

Signal is cross-directory symbol edges: fan-in = edges arriving from other
directories, fan-out = edges leaving to other directories. High fan-in / low
fan-out ≈ foundational; the reverse ≈ orchestration or leaf code.
"""

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from archaeologist.models.entities import File, Symbol, SymbolEdge


def _dir_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else "(root)"


def folder_heat(session: Session, repo_id: int, limit: int = 12) -> dict:
    sym_dir = {s.id: _dir_of(s.file_path)
               for s in session.scalars(select(Symbol).where(Symbol.repo_id == repo_id))}

    files = dict(session.execute(
        select(Symbol.file_path, func.count()).where(
            Symbol.repo_id == repo_id, Symbol.kind != "import"
        ).group_by(Symbol.file_path)
    ).all())
    dir_files: dict[str, set[str]] = defaultdict(set)
    dir_symbols: dict[str, int] = defaultdict(int)
    for path, n in files.items():
        dir_files[_dir_of(path)].add(path)
        dir_symbols[_dir_of(path)] += n

    # Weighted by edge confidence — a directory that's only "depended on" via
    # ambiguous name matches shouldn't rank as hot core; a real, well-resolved
    # dependency should count in full. See indexing/graph.py for the tiers.
    fan_in: dict[str, float] = defaultdict(float)
    fan_out: dict[str, float] = defaultdict(float)
    for src, dst, conf in session.execute(
        select(SymbolEdge.src_symbol_id, SymbolEdge.dst_symbol_id, SymbolEdge.confidence)
        .where(SymbolEdge.repo_id == repo_id, SymbolEdge.dst_symbol_id.is_not(None))
    ):
        ds, dd = sym_dir.get(src), sym_dir.get(dst)
        if ds and dd and ds != dd:
            fan_out[ds] += conf
            fan_in[dd] += conf

    dirs = set(dir_symbols) | set(fan_in) | set(fan_out)
    rows = []
    for d in dirs:
        if d.startswith("tests") or d == "(root)":
            continue
        fi, fo = round(fan_in.get(d, 0)), round(fan_out.get(d, 0))
        total = fi + fo
        ratio = fi / total if total else 0.0
        role = ("hot core" if fi >= 8 and ratio >= 0.6 else
                "foundational" if ratio >= 0.6 and fi >= 3 else
                "orchestration" if fo >= 8 and ratio <= 0.4 else
                "leaf / utility")
        rows.append({
            "dir": d, "files": len(dir_files.get(d, ())), "symbols": dir_symbols.get(d, 0),
            "fan_in": fi, "fan_out": fo, "role": role,
        })

    rows.sort(key=lambda r: (-r["fan_in"], -r["symbols"]))
    mx = max((r["fan_in"] for r in rows), default=1) or 1
    for r in rows:
        r["heat"] = round(r["fan_in"] / mx, 3)
    return {"folders": rows[:limit], "max_fan_in": mx}
