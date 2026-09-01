"""Static, shareable snapshot — every core signal bundled into one JSON blob
that a self-contained HTML viewer can render with zero backend, zero LLM key,
and zero Docker running. Mirrors Understand-Anything's "commit the graph,
view it anywhere" pattern, adapted to a hosted app: this is an explicit
export action rather than a git-committed artifact, and the tour is the
mechanical (non-LLM-curated) one, since a shared snapshot has no API key to
curate with on the viewer's end anyway.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from archaeologist.analysis.architecture import build_architecture
from archaeologist.analysis.communities import find_communities
from archaeologist.analysis.coupling import find_change_coupling
from archaeologist.analysis.dead_code import find_dead_code
from archaeologist.analysis.entrypoints import find_entrypoints
from archaeologist.analysis.wiki import build_wiki
from archaeologist.models.entities import Commit, File, Issue, Symbol, SymbolEdge
from archaeologist.viz.export import export_file_graph


def _count(session: Session, model, repo_id: int) -> int:
    return session.scalar(select(func.count()).select_from(model).where(model.repo_id == repo_id)) or 0


def build_snapshot(session: Session, repo_id: int, repo_name: str, user_id: int | None = None) -> dict:
    counts = {
        "files": _count(session, File, repo_id), "symbols": _count(session, Symbol, repo_id),
        "commits": _count(session, Commit, repo_id), "issues": _count(session, Issue, repo_id),
        "edges": _count(session, SymbolEdge, repo_id),
    }
    return {
        "repo": repo_name,
        "counts": counts,
        "architecture": build_architecture(session, repo_id, repo_name),
        "entrypoints": find_entrypoints(session, repo_id),
        "dead_code": find_dead_code(session, repo_id),
        "communities": find_communities(session, repo_id),
        "coupling": find_change_coupling(session, repo_id),
        "wiki": build_wiki(session, repo_id, repo_name, user_id),
        "graph": export_file_graph(session, repo_id, exclude_tests=True),
    }
