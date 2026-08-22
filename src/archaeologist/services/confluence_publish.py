"""Convert the cached "Start here" wiki into Confluence storage-format XHTML
and publish it as one parent page + one child page per section.

Mirrors `ingestion/pipeline.py`'s graceful-degradation shape: a failure while
publishing one section is recorded on that section's result and never aborts
the batch (`publish_section` catches everything; only `ensure_parent_page` is
allowed to raise, because every child needs its id).

Confluence storage format is XHTML, so `markdown.markdown()` output drops in
directly — one dependency, one renderer, no hand-rolled conversion.
"""

import base64
import json
from xml.sax.saxutils import escape

import httpx
import markdown

from archaeologist.config import settings
from archaeologist.services import confluence_client
from archaeologist.services.confluence_client import ConfluencePage

# ---------- diagram rendering ----------

def render_diagram_image(mermaid_src: str) -> bytes | None:
    """Render a Mermaid flowchart to PNG via mermaid.ink (a public rendering
    service — chosen over running Playwright/Chromium in-process, which would
    blow this app's 512MB free-tier budget). Sends diagram SOURCE (symbol /
    module names, not secrets) to a third party; that tradeoff is why it's a
    settings toggle rather than hardwired on. Any failure returns None and the
    caller falls back to a raw-Mermaid code macro."""
    if not settings.confluence_render_diagrams:
        return None
    try:
        # mermaid.ink takes a base64url-encoded JSON state document.
        state = json.dumps({"code": mermaid_src, "mermaid": {"theme": "default"}})
        encoded = base64.urlsafe_b64encode(state.encode()).decode()
        resp = httpx.get(
            f"{settings.confluence_mermaid_ink_url.rstrip('/')}/img/{encoded}",
            timeout=30.0,
            follow_redirects=True,
        )
        if resp.status_code != 200 or not resp.content:
            return None
        return resp.content
    except Exception:  # noqa: BLE001 - diagrams are decorative; fall back
        return None


def _cdata(text: str) -> str:
    """Wrap text in a CDATA section safely (]]> cannot appear literally)."""
    return f"<![CDATA[{text.replace(']]>', ']]]]><![CDATA[>')}]]>"


def _inline_md(text: str) -> str:
    """Inline markdown (list/table cells) — drop markdown's block-level <p>."""
    html = markdown.markdown(text, extensions=["tables"]).strip()
    if html.startswith("<p>") and html.endswith("</p>"):
        return html[3:-4]
    return html


def _code_macro(lang: str, code: str) -> str:
    lang = escape(lang or "none")
    return (
        '<ac:structured-macro ac:name="code">'
        f'<ac:parameter ac:name="language">{lang}</ac:parameter>'
        f"<ac:plain-text-body>{_cdata(code)}</ac:plain-text-body>"
        "</ac:structured-macro>"
    )


# ---------- block -> XHTML ----------

def block_to_html(block: dict, image_filenames: dict[int, str]) -> str:
    kind = block.get("kind")
    if kind in ("md", "p"):
        return markdown.markdown(block["text"], extensions=["tables"])
    if kind == "h2":
        return f"<h2>{escape(block['text'])}</h2>"
    if kind == "list":
        items = "".join(f"<li>{_inline_md(i)}</li>" for i in block["items"])
        return f"<ul>{items}</ul>"
    if kind == "table":
        head = "".join(
            f"<th>{_inline_md(c)}</th>" for c in block.get("columns", [])
        )
        rows = "".join(
            "<tr>"
            + "".join(f"<td>{_inline_md(cell)}</td>" for cell in row)
            + "</tr>"
            for row in block.get("rows", [])
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>"
    if kind == "chips":
        # No public URL exists to link to from outside the app, so chips are
        # plain "path:line — text" entries — deliberate v1 simplification.
        items = "".join(
            f"<li>{escape(c['path'])}"
            + (f":{c['line']}" if c.get("line") else "")
            + f" &#8212; {escape(c['text'])}</li>"
            for c in block["chips"]
        )
        return f"<ul>{items}</ul>"
    if kind == "code":
        title = escape(block.get("title") or "")
        path = escape(block.get("path") or "")
        line = block.get("line")
        loc = f"{path}:{line}" if path else ""
        head = (
            f'<p><strong>{title}</strong>'
            + (f' &#8212; <code>{loc}</code>' if loc else "")
            + "</p>"
        ) if (title or loc) else ""
        return head + _code_macro(block.get("lang", ""), block.get("code", ""))
    if kind == "diagram":
        filename = image_filenames.get(block.get("_diagram_index", -1))
        caption = f'<p><strong>{escape(block.get("title") or "")}</strong></p>'
        if filename:
            return (
                caption
                + f'<ac:image><ri:attachment ri:filename="{escape(filename)}"/></ac:image>'
            )
        return caption + _code_macro("mermaid", block.get("mermaid", ""))
    # Unknown kind — render nothing rather than corrupt the page.
    return ""


def section_to_html(section: dict) -> tuple[str, list[tuple[str, bytes]]]:
    """Render every diagram up front so each gets a stable attachment filename;
    Confluence resolves ri:attachment by filename at view time, so attachments
    upload AFTER the page create/update — no create-then-patch round trip."""
    key = section.get("key", "")
    parts: list[str] = [f"<p>{escape(section.get('subtitle') or '')}</p>"]
    attachments: list[tuple[str, bytes]] = []
    image_filenames: dict[int, str] = {}

    di = -1
    numbered: list[dict] = []
    for raw in section.get("blocks", []):
        block = raw
        if raw.get("kind") == "diagram":
            di += 1
            block = {**raw, "_diagram_index": di}
            png = render_diagram_image(raw.get("mermaid", ""))
            if png is not None:
                name = f"{key}-diagram-{di}.png"
                image_filenames[di] = name
                attachments.append((name, png))
        numbered.append(block)

    parts.extend(block_to_html(b, image_filenames) for b in numbered)
    return "\n".join(parts), attachments


# ---------- publishing ----------

def ensure_parent_page(
    client: httpx.Client, space_key: str, repo_label: str, wiki: dict
) -> ConfluencePage:
    """NOT wrapped in graceful degradation — every child page needs this id."""
    title = f"{repo_label} — Wiki"
    counts = wiki.get("counts") or {}
    stats = " ".join(f"{k}: {v}" for k, v in counts.items())
    children = "".join(
        f"<li>{escape(repo_label)} — {escape(s['title'])}</li>"
        for s in wiki.get("sections", [])
    )
    body = (
        (f"<p>{escape(stats)}</p>" if stats else "")
        + f"<p>Sections:</p><ul>{children}</ul>"
    )

    existing = confluence_client.find_page_by_title(client, space_key, title)
    if existing is not None:
        return confluence_client.update_page(client, existing, body)
    return confluence_client.create_page(client, space_key, title, body)


def publish_section(
    client: httpx.Client, space_key: str, parent_id: str,
    repo_label: str, section: dict,
) -> dict:
    """Never raises. Attachment-upload failures are caught per-diagram, not
    per-section — one broken image shouldn't sink an otherwise-published page."""
    key = section.get("key", "")
    title = f"{repo_label} — {section.get('title', key)}"
    try:
        body_html, attachments = section_to_html(section)

        existing = confluence_client.find_page_by_title(client, space_key, title)
        if existing is not None:
            page = confluence_client.update_page(client, existing, body_html)
        else:
            page = confluence_client.create_page(
                client, space_key, title, body_html, parent_id=parent_id
            )

        errors: list[str] = []
        for filename, content in attachments:
            try:
                confluence_client.upload_attachment(client, page.id, filename, content)
            except Exception as exc:  # noqa: BLE001 - degrade per diagram
                errors.append(f"{filename}: {exc}")

        result = {"key": key, "title": title, "status": "ok", "url": page.url}
        if errors:
            result["status"] = "ok"
            result["error"] = "; ".join(errors)  # page published, images missing
        return result
    except Exception as exc:  # noqa: BLE001 - record and move on
        return {"key": key, "title": title, "status": "error", "error": str(exc)}


def publish_wiki(
    repo_label: str,
    wiki: dict,
    section_keys: list[str],
    on_progress=None,
) -> dict:
    """Publish the requested sections (in the wiki's own order) under one
    parent page. Returns {"parent_url", "results"} with one entry per section."""
    wanted = set(section_keys)
    sections = [s for s in wiki.get("sections", []) if s.get("key") in wanted]

    with confluence_client.open_client() as client:
        parent = ensure_parent_page(client, settings.confluence_space_key, repo_label, wiki)

        results: list[dict] = []
        for section in sections:
            result = publish_section(
                client, settings.confluence_space_key, parent.id, repo_label, section
            )
            results.append(result)
            if on_progress is not None:
                on_progress(list(results))

    return {"parent_url": parent.url, "results": results}
