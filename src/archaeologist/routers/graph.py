"""/graph endpoint — serves the interactive dependency graph as a live HTML page.

    GET /graph                          file-level atlas
    GET /graph?tests=true               include test files
    GET /graph?symbols=src/flask/sansio symbol-level zoom into a module
"""

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from archaeologist.models.db import session_scope
from archaeologist.models.entities import Repo
from archaeologist.viz.export import export_combined, export_symbol_graph
from archaeologist.viz.render import render_linked_page, render_page

router = APIRouter(tags=["graph"])


@router.get("/graph", response_class=HTMLResponse)
def graph(symbols: str | None = Query(default=None), tests: bool = Query(default=False)) -> str:
    with session_scope() as session:
        repo = session.scalar(select(Repo))
        if repo is None:
            return "<p>No repository ingested yet — run the ingestion first.</p>"
        name = repo.name.capitalize()
        if symbols is not None:
            data = export_symbol_graph(session, repo.id, path_prefix=symbols or None)
            return render_page(data, f"{name} — Symbol Graph")
        combined = export_combined(session, repo.id, exclude_tests=not tests)
    return render_linked_page(combined, f"{name} Dependency Atlas")
