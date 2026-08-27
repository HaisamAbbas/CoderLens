"""Architecture Delta — how a repository's structure changed between two commits.

The rest of the app explains what a codebase looks like *now*. This answers the
archaeology question instead: what moved, and when. Point it at two refs — two
tags, a tag and HEAD, a commit from a year ago and today — and it reports the
exact structural facts that differ between them.

It needs no re-ingest and no LLM. `architecture.shape_from_paths` derives the
whole skeleton from file paths, and `classify()` decides code-vs-doc-vs-test
from the path too, so any commit reachable in the local clone can be shaped
exactly the way the working tree was. A delta is therefore two `git ls-tree`
listings and set arithmetic — fast enough to run on request, and deterministic:
the same two refs always produce the same receipt.

Scope, deliberately: this compares *structure* — packages, submodules, and the
files inside them. It does not diff dependency edges, because symbol edges only
exist for the ingested commit; producing them for an arbitrary historical commit
would mean running tree-sitter over two full trees. "auth moved out of core into
its own submodule" is answerable here; "module A stopped importing module B" is
not, and is the natural next step if this proves useful.
"""

from pathlib import Path

import git

from archaeologist.analysis.architecture import shape_from_paths

# Cap on facts of any one kind carried in a receipt. A rename of a large package
# can move hundreds of files; the counts stay exact (they are computed before
# truncation) but the itemised list stays readable, and `truncated` says so.
MAX_FACTS = 60


def open_repo(repo_path: str | Path) -> git.Repo:
    path = Path(repo_path)
    if not (path / ".git").exists():
        raise RuntimeError(
            f"No git clone at {path} — the repository was ingested but its local "
            "clone is gone (ephemeral disk?). Re-ingest it to restore history.")
    return git.Repo(path)


def list_refs(repo: git.Repo, limit: int = 40) -> dict:
    """Refs worth offering in a picker: every tag, then recent commits.

    Tags come first because they are what people actually want to compare — "v1
    versus v2" is the question; "3f2a1b versus 9c8d7e" is almost never it.
    """
    tags = []
    for tag in repo.tags:
        try:
            commit = tag.commit
        except Exception:  # noqa: BLE001 - a broken/annotated-object tag is not fatal
            continue
        tags.append({
            "ref": tag.name, "kind": "tag", "sha": commit.hexsha[:12],
            "date": commit.committed_datetime.isoformat(),
            "subject": (commit.message or "").strip().splitlines()[0][:120],
        })
    tags.sort(key=lambda t: t["date"], reverse=True)

    # Sampled across the repo's whole life, not just the newest `limit` commits.
    # A dense recent window is the wrong list for this feature: any two adjacent
    # commits are almost always structurally identical, so a picker offering
    # only the last few days can only produce "nothing changed". Taking every
    # nth commit spans the full history at the same list length, so the default
    # pair is far enough apart for structure to have actually moved.
    all_commits = list(repo.iter_commits())
    step = max(1, len(all_commits) // limit) if limit else 1
    sampled = all_commits[::step][:limit]
    if all_commits and all_commits[-1] not in sampled:
        sampled.append(all_commits[-1])   # always offer the repo's first commit
    commits = [{
        "ref": c.hexsha, "kind": "commit", "sha": c.hexsha[:12],
        "date": c.committed_datetime.isoformat(),
        "subject": (c.message or "").strip().splitlines()[0][:120],
    } for c in sampled]

    return {"tags": tags, "commits": commits, "head": repo.head.commit.hexsha[:12]}


def paths_at(repo: git.Repo, ref: str) -> list[str]:
    """Every tracked file path at `ref`, as posix paths.

    -z keeps raw bytes rather than git's default quoting of non-ASCII names,
    which would otherwise turn "src/café.py" into an escaped literal that no
    longer matches the same file on the other side of the diff.
    """
    try:
        commit = repo.commit(ref)
    except Exception as exc:  # noqa: BLE001 - bad ref is user input, not a crash
        raise RuntimeError(f"Unknown ref {ref!r}: {exc}") from exc
    raw = repo.git.ls_tree("-r", "--name-only", "-z", commit.hexsha)
    return [p for p in raw.split("\0") if p]


def _resolve(repo: git.Repo, ref: str) -> dict:
    commit = repo.commit(ref)
    return {
        "ref": ref, "sha": commit.hexsha[:12],
        "date": commit.committed_datetime.isoformat(),
        "subject": (commit.message or "").strip().splitlines()[0][:120],
    }


def _is_relocation(before_pkg: str, after_pkg: str) -> bool:
    """True when two package paths are the same package that merely moved.

    `flask` -> `src/flask` is one project adopting a src/ layout: same package,
    new path. `p` -> `p/sub` is a different package winning the "most code"
    heuristic, which is not a relocation at all. The last path segment tells
    them apart, and the distinction decides whether file paths can be compared
    package-relative — doing that across two genuinely different packages would
    align unrelated files by coincidence of name.
    """
    if before_pkg == after_pkg:
        return True
    if not before_pkg or not after_pkg:
        return False
    return before_pkg.split("/")[-1] == after_pkg.split("/")[-1]


def _rel_map(shape: dict, strip_package: bool) -> dict[str, dict]:
    """key -> {path, submodule} for every code file in the package.

    The key is package-relative when the package merely moved. When a project
    adopts a src/ layout, `flask/app.py` becomes `src/flask/app.py` and every
    single file looks relocated — one top-level rename swamping the receipt with
    hundreds of moves that say nothing about the architecture. Relative keys
    keep that rename a single reported fact (`package: before/after`) and leave
    the file-level facts describing movement *within* the package.

    When the package changed identity rather than location, the key stays the
    full path: there is no shared frame of reference to be relative to.
    """
    pkg = shape["package"]
    out: dict[str, dict] = {}
    for s in shape["submodules"]:
        for p in s["files"]:
            key = p[len(pkg) + 1:] if (strip_package and pkg and p.startswith(pkg + "/")) else p
            out[key] = {"path": p, "submodule": s["submodule"]}
    return out


def _cap(items: list) -> tuple[list, bool]:
    return items[:MAX_FACTS], len(items) > MAX_FACTS


def diff_shapes(before: dict, after: dict) -> dict:
    """The receipt: exactly what differs between two architecture shapes.

    Every entry is a fact read off the two trees, never an inference about
    whether the change was good, risky, or intentional.
    """
    b_subs = {s["submodule"]: s for s in before["submodules"]}
    a_subs = {s["submodule"]: s for s in after["submodules"]}

    added_subs = sorted(set(a_subs) - set(b_subs))
    removed_subs = sorted(set(b_subs) - set(a_subs))
    kept_subs = sorted(set(a_subs) & set(b_subs))

    relocated = _is_relocation(before["package"], after["package"])
    b_of, a_of = _rel_map(before, relocated), _rel_map(after, relocated)
    gone = set(b_of) - set(a_of)
    fresh = set(a_of) - set(b_of)

    # A file with the same basename that vanished from one submodule and appeared
    # in another is a move, not an unrelated delete plus add.
    #
    # Only claim it when that basename is unambiguous — exactly one gone and one
    # fresh. Every Python package carries an __init__.py, so a loose match
    # happily reports "__init__.py moved from core to json", which is noise
    # dressed up as a fact. Requiring uniqueness costs a few genuine moves in a
    # mass rename and keeps every move it does report defensible.
    gone_by_name: dict[str, list[str]] = {}
    fresh_by_name: dict[str, list[str]] = {}
    for p in gone:
        gone_by_name.setdefault(p.split("/")[-1], []).append(p)
    for p in fresh:
        fresh_by_name.setdefault(p.split("/")[-1], []).append(p)

    moved, moved_from, moved_to = [], set(), set()
    for name, olds in sorted(gone_by_name.items()):
        news = fresh_by_name.get(name, [])
        if len(olds) != 1 or len(news) != 1:
            continue
        old_rel, new_rel = olds[0], news[0]
        moved.append({
            "file": name,
            "from": b_of[old_rel]["path"], "to": a_of[new_rel]["path"],
            "from_submodule": b_of[old_rel]["submodule"],
            "to_submodule": a_of[new_rel]["submodule"],
        })
        moved_from.add(old_rel)
        moved_to.add(new_rel)
    moved.sort(key=lambda m: m["from"])

    added_files = sorted(a_of[r]["path"] for r in fresh - moved_to)
    removed_files = sorted(b_of[r]["path"] for r in gone - moved_from)

    # Package-relative here too, for the same reason as _rel_map: a src/ layout
    # migration must not read as every submodule losing and regaining its files.
    b_by_sub: dict[str, set[str]] = {}
    a_by_sub: dict[str, set[str]] = {}
    for rel, info in b_of.items():
        b_by_sub.setdefault(info["submodule"], set()).add(rel)
    for rel, info in a_of.items():
        a_by_sub.setdefault(info["submodule"], set()).add(rel)

    changed = []
    for sub in kept_subs:
        b_files, a_files = b_by_sub.get(sub, set()), a_by_sub.get(sub, set())
        gained, lost = a_files - b_files, b_files - a_files
        if gained or lost:
            changed.append({
                "submodule": sub, "files_added": len(gained), "files_removed": len(lost),
                "file_count_before": len(b_files), "file_count_after": len(a_files),
            })
    changed.sort(key=lambda c: (-(c["files_added"] + c["files_removed"]), c["submodule"]))

    b_top = {c["chips"][0]["text"] for c in before["structure"]}
    a_top = {c["chips"][0]["text"] for c in after["structure"]}

    added_list, added_trunc = _cap(added_files)
    removed_list, removed_trunc = _cap(removed_files)
    moved_list, moved_trunc = _cap(moved)

    facts = {
        "package": ({"before": before["package"], "after": after["package"]}
                    if before["package"] != after["package"] else None),
        # Whether file facts below were compared package-relative (the package
        # moved) or by full path (a different package took over). It changes how
        # the paths should be read, so it is stated rather than left implicit.
        "package_relocated": relocated,
        "submodules_added": added_subs,
        "submodules_removed": removed_subs,
        "submodules_changed": changed,
        "files_added": added_list,
        "files_removed": removed_list,
        "files_moved": moved_list,
        "top_level_added": sorted(a_top - b_top),
        "top_level_removed": sorted(b_top - a_top),
        "counts": {
            "before": before["counts"], "after": after["counts"],
            "files_added": len(added_files), "files_removed": len(removed_files),
            "files_moved": len(moved),
        },
        "truncated": added_trunc or removed_trunc or moved_trunc,
    }
    facts["unchanged"] = not any([
        facts["package"], added_subs, removed_subs, changed,
        added_files, removed_files, moved, facts["top_level_added"],
        facts["top_level_removed"],
    ])
    return facts


def _node_id(name: str) -> str:
    return "s_" + "".join(ch if ch.isalnum() else "_" for ch in name)[:40]


def mermaid_delta(delta: dict, before: dict, after: dict) -> str | None:
    """Mermaid source for the delta, generated mechanically — never LLM-authored,
    the same rule the wiki diagrams follow, so it always parses."""
    b_subs = {s["submodule"] for s in before["submodules"]}
    a_subs = {s["submodule"] for s in after["submodules"]}
    if not (b_subs or a_subs):
        return None

    changed_by = {c["submodule"]: c for c in delta["submodules_changed"]}
    lines = ["flowchart LR"]
    pkg = after["package"] or before["package"] or "repository"
    lines.append(f'  root["{pkg}"]')

    for sub in sorted(a_subs | b_subs):
        nid = _node_id(sub)
        if sub in a_subs and sub not in b_subs:
            label, cls = f"{sub}<br/>added", "added"
        elif sub in b_subs and sub not in a_subs:
            label, cls = f"{sub}<br/>removed", "removed"
        elif sub in changed_by:
            c = changed_by[sub]
            label = f"{sub}<br/>+{c['files_added']} / -{c['files_removed']}"
            cls = "changed"
        else:
            label, cls = sub, "kept"
        lines.append(f'  {nid}["{label}"]')
        lines.append(f"  root --> {nid}")
        lines.append(f"  class {nid} {cls};")

    lines += [
        "  classDef added fill:#dcfce7,stroke:#16a34a,color:#14532d;",
        "  classDef removed fill:#fee2e2,stroke:#dc2626,color:#7f1d1d;",
        "  classDef changed fill:#fef3c7,stroke:#d97706,color:#78350f;",
        "  classDef kept fill:#f1f5f9,stroke:#94a3b8,color:#334155;",
    ]
    return "\n".join(lines)


def module_edges(session, repo_id: int, shape: dict) -> list[dict]:
    """Module -> module dependencies, aggregated up from the symbol graph.

    Submodules on their own are a bag of folders; what makes an architecture
    diagram an architecture diagram is the arrows. Those exist already, one
    level down: SymbolEdge resolves call/inherit edges between symbols, and
    every symbol has a file, and every file belongs to a submodule.

    Caveat the caller must surface: these edges describe the *ingested* commit,
    since symbol extraction only ever ran there. They are the architecture's
    current wiring drawn under whatever the delta says changed — not the wiring
    as it stood at some historical ref. Rebuilding them for an arbitrary commit
    would mean re-parsing two whole trees.
    """
    from sqlalchemy import select as _select

    from archaeologist.models.entities import Symbol, SymbolEdge

    sub_of: dict[str, str] = {}
    for s in shape["submodules"]:
        for path in s["files"]:
            sub_of[path] = s["submodule"]
    if not sub_of:
        return []

    file_of = dict(session.execute(
        _select(Symbol.id, Symbol.file_path).where(Symbol.repo_id == repo_id)
    ).all())

    weights: dict[tuple[str, str], int] = {}
    rows = session.execute(
        _select(SymbolEdge.src_symbol_id, SymbolEdge.dst_symbol_id)
        .where(SymbolEdge.repo_id == repo_id, SymbolEdge.dst_symbol_id.is_not(None))
    ).all()
    for src_id, dst_id in rows:
        a = sub_of.get(file_of.get(src_id, ""))
        b = sub_of.get(file_of.get(dst_id, ""))
        if a and b and a != b:            # self-edges say nothing at this zoom
            weights[(a, b)] = weights.get((a, b), 0) + 1

    edges = [{"source": a, "target": b, "weight": w} for (a, b), w in weights.items()]
    edges.sort(key=lambda e: (-e["weight"], e["source"], e["target"]))
    return edges


def build_delta(repo_path: str | Path, base: str, head: str) -> dict:
    """Full comparison of two refs: both shapes, the receipt, and a diagram."""
    repo = open_repo(repo_path)
    before_paths, after_paths = paths_at(repo, base), paths_at(repo, head)
    before = shape_from_paths(before_paths)
    after = shape_from_paths(after_paths)
    delta = diff_shapes(before, after)
    return {
        "base": _resolve(repo, base),
        "head": _resolve(repo, head),
        "before": before,
        "after": after,
        "delta": delta,
        "mermaid": mermaid_delta(delta, before, after),
    }
