"""Unit tests for the Confluence publish feature — only the parts that can be
meaningfully tested without a live server: the block→XHTML conversion and the
request/response handling of the thin REST client (via httpx.MockTransport).

Live-server behaviors (version conflicts, title uniqueness, attachment/macro
rendering) are verified manually per the phase plan; see the Confluence
section of the implementation notes.
"""

import base64
import json

import httpx
import pytest

from archaeologist.config import settings
from archaeologist.services import confluence_client, confluence_publish

# ---------- fixtures / helpers ----------

@pytest.fixture(autouse=True)
def _no_diagram_render(monkeypatch):
    """Keep diagram blocks off the network: rendering disabled = code-macro
    fallback, which also exercises the fallback path in every section test."""
    monkeypatch.setattr(settings, "confluence_render_diagrams", False)


WIKI_SECTION = {
    "key": "architecture",
    "title": "Architecture",
    "subtitle": "How it fits together",
    "blocks": [
        {"kind": "md", "text": "Hello **world**"},
        {"kind": "h2", "text": "Layers <&>"},
        {"kind": "list", "items": ["plain item", "**bold** item"]},
        {"kind": "table", "columns": ["Name"], "rows": [["`create_app` — app.py:41"]]},
        {"kind": "chips", "chips": [
            {"kind": "file", "text": "Flask.wsgi_app", "path": "src/app.py", "line": 22},
        ]},
        {
            "kind": "code", "title": "App factory",
            "path": "src/app.py", "line": 41, "lang": "python", "code": "x = ']]>'",
        },
        {"kind": "diagram", "title": "Request flow", "mermaid": "flowchart TD\nA-->B"},
    ],
}


class FakeConfluence:
    """Minimal stateful Confluence stand-in: title-unique pages, versioned
    updates, attachment uploads."""

    def __init__(self):
        self.pages: dict[str, dict] = {}       # title -> {id, version, body}
        self.next_id = 100
        self.attachments: list[tuple[str, str]] = []  # (page_id, filename)

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and "/rest/api/content" in url and "/attachment" not in url:
            params = dict(httpx.QueryParams(request.url.query))
            page = self.pages.get(params.get("title", ""))
            results = []
            if page:
                results.append({
                    "id": page["id"], "title": params["title"],
                    "version": {"number": page["version"]},
                    "_links": {"webui": f"/spaces/DOC/pages/{page['id']}/t"},
                })
            return httpx.Response(200, json={"results": results})

        if request.method == "POST" and url.endswith("/rest/api/content"):
            payload = json.loads(request.content)
            assert payload["type"] == "page"
            assert payload["space"]["key"] == "DOC"
            parent_ids = [a["id"] for a in payload.get("ancestors", [])]
            if parent_ids and parent_ids[0] not in {p["id"] for p in self.pages.values()}:
                return httpx.Response(400, json={"message": "bad parent"})
            self.next_id += 1
            self.pages[payload["title"]] = {
                "id": str(self.next_id), "version": 1,
                "body": payload["body"]["storage"]["value"],
            }
            return httpx.Response(200, json={
                "id": str(self.next_id), "title": payload["title"],
                "_links": {"webui": f"/spaces/DOC/pages/{self.next_id}/t"},
            })

        if request.method == "PUT" and "/rest/api/content/" in url:
            payload = json.loads(request.content)
            page_id = url.rsplit("/", 1)[-1]
            found = next((t for t, p in self.pages.items() if p["id"] == page_id), None)
            assert found is not None
            stored = self.pages[found]
            assert payload["version"]["number"] == stored["version"] + 1, (
                "update must send current version + 1"
            )
            stored["version"] += 1
            stored["body"] = payload["body"]["storage"]["value"]
            return httpx.Response(200, json={
                "id": page_id, "title": payload["title"],
                "version": {"number": stored["version"]},
                "_links": {"webui": f"/spaces/DOC/pages/{page_id}/t"},
            })

        if request.method == "POST" and "/child/attachment" in url:
            page_id = url.split("/rest/api/content/")[1].split("/")[0]
            filename = request.read().split(b'"')[1].decode("latin-1")  # multipart name field
            self.attachments.append((page_id, filename))
            assert request.headers.get("X-Atlassian-Token") == "nocheck"
            return httpx.Response(200, json={})

        return httpx.Response(404, json={"message": f"unexpected {request.method} {url}"})


@pytest.fixture()
def fake():
    return FakeConfluence()


def fake_client(fake: FakeConfluence) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(fake.handler),
        base_url="https://test.atlassian.net/wiki",
        auth=("user@example.com", "tok"),
    )


# ---------- block_to_html ----------

def test_md_block_renders_markdown():
    html = confluence_publish.block_to_html({"kind": "md", "text": "a **b** c"}, {})
    assert "<strong>b</strong>" in html


def test_h2_escapes_html_entities():
    html = confluence_publish.block_to_html({"kind": "h2", "text": "Layers <&>"}, {})
    assert "<h2>Layers &lt;&amp;&gt;</h2>" == html


def test_list_items_render_inline_markdown_without_paragraph_wrapper():
    html = confluence_publish.block_to_html(
        {"kind": "list", "items": ["one", "**two**"]}, {})
    assert html.startswith("<ul><li>one</li>")
    assert "<li><strong>two</strong></li></ul>" in html
    assert "<p>" not in html


def test_table_cells_render_inline_markdown():
    html = confluence_publish.block_to_html(
        {"kind": "table", "columns": ["Name"], "rows": [["`fn` — x:1"]]}, {})
    assert "<table>" in html and "<code>fn</code>" in html


def test_chips_render_as_plain_text_list():
    html = confluence_publish.block_to_html(
        {"kind": "chips", "chips": [
            {"kind": "file", "text": "F.run", "path": "a/b.py", "line": 7},
            {"kind": "file", "text": "G", "path": "c.py"},
        ]}, {})
    assert "a/b.py:7 &#8212; F.run" in html
    assert "c.py &#8212; G" in html
    assert "<ac:link" not in html  # no link target exists outside the app


def test_code_block_uses_confluence_code_macro_and_escapes_cdata():
    html = confluence_publish.block_to_html(WIKI_SECTION["blocks"][5], {})
    assert '<ac:structured-macro ac:name="code">' in html
    assert '<ac:parameter ac:name="language">python</ac:parameter>' in html
    # "]]>" inside the snippet must not terminate the CDATA section.
    assert "]]]]><![CDATA[>" in html
    assert "app.py:41" in html


def test_diagram_falls_back_to_mermaid_code_macro_when_not_rendered():
    html = confluence_publish.block_to_html(WIKI_SECTION["blocks"][6], {})
    assert '<ac:parameter ac:name="language">mermaid</ac:parameter>' in html


def test_diagram_uses_attachment_image_macro_when_rendered():
    block = {**WIKI_SECTION["blocks"][6], "_diagram_index": 0}
    html = confluence_publish.block_to_html(block, {0: "arch-diagram-0.png"})
    assert '<ri:attachment ri:filename="arch-diagram-0.png"/>' in html


def test_unknown_block_kind_renders_nothing():
    assert confluence_publish.block_to_html({"kind": "???", "text": "x"}, {}) == ""


# ---------- section_to_html ----------

def test_section_to_html_includes_subtitle_and_all_blocks():
    html, attachments = confluence_publish.section_to_html(WIKI_SECTION)
    assert "How it fits together" in html
    assert "<h2>Layers" in html
    assert attachments == []  # diagrams disabled by fixture


def test_section_to_html_collects_attachments_for_rendered_diagrams(monkeypatch):
    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    calls = []

    def fake_render(src: str) -> bytes | None:
        calls.append(src)
        return png if "flowchart" in src else None

    monkeypatch.setattr(confluence_publish, "render_diagram_image", fake_render)
    html, attachments = confluence_publish.section_to_html(WIKI_SECTION)
    # Only the rendered diagram becomes an attachment, named after its index.
    assert attachments == [("architecture-diagram-0.png", png)]
    assert '<ri:attachment ri:filename="architecture-diagram-0.png"/>' in html
    assert calls == [WIKI_SECTION["blocks"][6]["mermaid"]]


def test_render_diagram_image_disabled_returns_none():
    assert confluence_publish.render_diagram_image("flowchart TD\nA-->B") is None


def test_render_diagram_enabled_survives_network_failure(monkeypatch):
    monkeypatch.setattr(settings, "confluence_render_diagrams", True)

    def boom(*a, **kw):
        raise OSError("down")

    monkeypatch.setattr(confluence_publish.httpx, "get", boom)
    assert confluence_publish.render_diagram_image("flowchart TD\nA-->B") is None


# ---------- REST client against the fake ----------

def test_find_page_by_title_miss_returns_none(fake):
    with fake_client(fake) as client:
        assert confluence_client.find_page_by_title(client, "DOC", "nope") is None


def test_create_then_update_is_version_incremented_not_duplicate(fake):
    label = "pallets/flask"
    wiki = {
        "counts": {"files": 3},
        "sections": [{"key": "k", "title": "S", "subtitle": "", "blocks": []}],
    }
    with fake_client(fake) as client:
        parent = confluence_publish.ensure_parent_page(client, "DOC", label, wiki)
        assert parent.version == 1

        # Re-run with no changes — finds existing, PUTs version+1.
        again = confluence_publish.ensure_parent_page(client, "DOC", label, wiki)
        assert again.id == parent.id
        assert again.version == 2

        # Parent lists child titles so Confluence auto-links them.
        assert "pallets/flask — S" in fake.pages[f"{label} — Wiki"]["body"]


def test_publish_section_creates_child_under_parent_and_never_raises(fake):
    with fake_client(fake) as client:
        parent = confluence_client.create_page(client, "DOC", "parent", "<p>p</p>")
        result = confluence_publish.publish_section(
            client, "DOC", parent.id, "o/r", WIKI_SECTION
        )
    assert result["status"] == "ok"
    assert result["url"].startswith("https://test.atlassian.net/wiki/")
    assert fake.pages["o/r — Architecture"]["body"].count("<h2>") == 1


def test_publish_section_reports_error_instead_of_raising(fake):
    def failer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": "boom"})

    with httpx.Client(transport=httpx.MockTransport(failer),
                      base_url="https://t.atlassian.net/wiki") as client:
        result = confluence_publish.publish_section(client, "DOC", "9", "o/r", WIKI_SECTION)
    assert result["status"] == "error"
    assert result["error"]  # surfaced, never raised


def test_client_raises_clear_error_on_bad_credentials():
    def unauth(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Unauthorized"})

    with httpx.Client(transport=httpx.MockTransport(unauth),
                      base_url="https://t.atlassian.net/wiki") as client:
        with pytest.raises(RuntimeError, match="credentials"):
            confluence_client.find_page_by_title(client, "DOC", "any")


def test_full_publish_wiki_flow_with_progress_callback(fake, monkeypatch):
    # publish_wiki builds its own client from the credentials dict — point
    # the client factory at the fake for this test.
    monkeypatch.setattr(
        confluence_publish.confluence_client, "open_client",
        lambda base_url, email, api_token: fake_client(fake),
    )

    sec2 = {**WIKI_SECTION, "key": "getting_started", "title": "Getting Started"}
    wiki = {
        "repo": "flask", "counts": {"files": 5},
        "sections": [WIKI_SECTION, sec2],
    }
    credentials = {"base_url": "https://t.atlassian.net/wiki", "email": "a@b.com",
                   "api_token": "tok", "space_key": "DOC"}

    progress: list[list[dict]] = []
    outcome = confluence_publish.publish_wiki(
        "o/r", wiki, ["architecture", "getting_started"], credentials,
        on_progress=lambda results: progress.append(list(results)),
    )

    assert len(outcome["results"]) == 2
    assert all(r["status"] == "ok" for r in outcome["results"])
    # Sections publish in the wiki's own order; progress accumulates 1 then 2.
    assert [len(p) for p in progress] == [1, 2]
    titles = list(fake.pages)
    assert any(t.endswith("— Wiki") for t in titles)
    assert "o/r — Architecture" in titles
    assert "o/r — Getting Started" in titles


def test_upload_attachment_sends_multipart_with_xsrf_header(fake, monkeypatch):
    captured = {}

    def ok(request: httpx.Request) -> httpx.Response:
        captured["xsrf"] = request.headers.get("X-Atlassian-Token")
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(ok),
                      base_url="https://t.atlassian.net/wiki") as client:
        confluence_client.upload_attachment(client, "42", "d.png", b"\x89PNG")
    assert captured["xsrf"] == "nocheck"
