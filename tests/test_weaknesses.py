"""Unit tests for the weakness-scan feature's pure functions only — coercion,
shared JSON parsing, ADF description building, cap disclosures, and the
request-building side of the Jira client (via httpx.MockTransport).

Live Jira behaviors (issue-create response shapes, project/issue-type
validation) are verified against a real sandbox per the phase plan; detection
quality itself is eyeball territory, not unit-test territory.
"""

import json

import httpx
import pytest

from archaeologist.analysis import weaknesses
from archaeologist.models.entities import File
from archaeologist.rag.llm import parse_llm_json
from archaeologist.routers.api import _snippet
from archaeologist.services import jira_client

# ---------- parse_llm_json (the newly shared parser) ----------

def test_parse_llm_json_fenced_with_language():
    raw = 'noise\n```json\n{"a": 1, "b": [2, 3]}\n```\ntrailing prose'
    assert parse_llm_json(raw) == {"a": 1, "b": [2, 3]}


def test_parse_llm_json_fenced_plain_and_prose_wrapped():
    assert parse_llm_json('```\n{"a": 1}\n```') == {"a": 1}
    assert parse_llm_json('Here you go: {"findings": []} — done.') == {"findings": []}


def test_parse_llm_json_slices_first_brace_to_last():
    # Nested braces must survive the slice.
    assert parse_llm_json('x {"outer": {"inner": 1}} y') == {"outer": {"inner": 1}}


def test_parse_llm_json_garbage_returns_default_or_empty():
    fallback = {"fallback": True}
    assert parse_llm_json("no json at all") == {}
    assert parse_llm_json("no json at all", fallback) == fallback
    assert parse_llm_json("", fallback) == fallback


def test_parse_llm_json_never_returns_a_bare_array():
    # Structured prompts ask for {"key": [...]} objects precisely so this
    # parser never has to handle top-level arrays.
    assert parse_llm_json('[1, 2, 3]') == {}


# ---------- _coerce_finding ----------

VALID = {
    "title": "Off-by-one in loop bound",
    "description": "Uses <= where < is meant.",
    "category": "logic",
    "severity": "high",
    "start_line": 4,
    "end_line": 6,
    "suggested_fix": "Use < len(items).",
}


def test_coerce_valid_finding_passes_through():
    f = weaknesses._coerce_finding(dict(VALID), "app.py", 100)
    assert f == {
        "file_path": "app.py", "category": "logic", "severity": "high",
        "title": VALID["title"], "description": VALID["description"],
        "start_line": 4, "end_line": 6, "suggested_fix": "Use < len(items).",
    }


def test_coerce_drops_missing_or_unknown_category():
    no_cat = {k: v for k, v in VALID.items() if k != "category"}
    assert weaknesses._coerce_finding({**VALID, "category": ""}, "a.py", 10) is None
    assert weaknesses._coerce_finding({**VALID, "category": "performance"}, "a.py", 10) is None
    assert weaknesses._coerce_finding(no_cat, "a.py", 10) is None


def test_coerce_coerces_bad_severity_instead_of_dropping():
    f = weaknesses._coerce_finding({**VALID, "severity": "CRITICAL!!"}, "a.py", 10)
    assert f is not None and f["severity"] == "medium"
    f2 = weaknesses._coerce_finding({k: v for k, v in VALID.items() if k != "severity"}, "a.py", 10)
    assert f2 is not None and f2["severity"] == "medium"


def test_coerce_requires_title_and_description():
    no_desc = {k: v for k, v in VALID.items() if k != "description"}
    assert weaknesses._coerce_finding({**VALID, "title": "  "}, "a.py", 10) is None
    assert weaknesses._coerce_finding(no_desc, "a.py", 10) is None


@pytest.mark.parametrize("bad_start", [0, -3, "7", 2.5, None, True])
def test_coerce_rejects_non_integer_or_nonpositive_start_lines(bad_start):
    assert weaknesses._coerce_finding({**VALID, "start_line": bad_start}, "a.py", 10) is None


def test_coerce_clamps_lines_into_the_real_file():
    f = weaknesses._coerce_finding({**VALID, "start_line": 50, "end_line": 500}, "a.py", 60)
    assert f is not None
    assert f["start_line"] == 50 and f["end_line"] == 60  # both clamped to max_line


def test_coerce_end_before_start_collapses_to_start():
    f = weaknesses._coerce_finding({**VALID, "start_line": 8, "end_line": 3}, "a.py", 100)
    assert f is not None and f["start_line"] == 8 and f["end_line"] == 8


def test_coerce_truncates_oversized_strings():
    f = weaknesses._coerce_finding(
        {**VALID, "title": "t" * 400, "description": "d" * 9000,
         "suggested_fix": "f" * 5000}, "a.py", 10)
    assert f is not None
    assert len(f["title"]) == 300 and len(f["description"]) == 4000
    assert len(f["suggested_fix"]) == 2000


def test_coerce_non_dict_items_are_dropped():
    assert weaknesses._coerce_finding("not a dict", "a.py", 10) is None  # type: ignore[arg-type]
    assert weaknesses._coerce_finding(None, "a.py", 10) is None  # type: ignore[arg-type]


def test_coerce_drops_self_rated_low_confidence():
    # The model's own "I'm not sure" is the cheapest false-positive filter.
    assert weaknesses._coerce_finding({**VALID, "confidence": "low"}, "a.py", 100) is None
    # Anything else (incl. missing) is kept — confidence is not persisted.
    assert weaknesses._coerce_finding({**VALID, "confidence": "high"}, "a.py", 100) is not None
    assert weaknesses._coerce_finding(dict(VALID), "a.py", 100) is not None


# ---------- _scan_file (LLM mocked at the module boundary) ----------

def _file(content: str, path: str = "src/app.py") -> File:
    return File(path=path, content=content, loc=content.count("\n") + 1, category="code")


def test_scan_file_offline_returns_empty(monkeypatch):
    monkeypatch.setattr(weaknesses, "llm_available", lambda: False)
    assert weaknesses._scan_file(_file("x = 1\n")) == ([], 0)


def test_scan_file_parses_and_attaches_file_path(monkeypatch):
    monkeypatch.setattr(weaknesses, "llm_available", lambda: True)
    monkeypatch.setattr(weaknesses, "call_llm", lambda *a, **kw: '{"findings": [' + repr({
        **VALID, "start_line": 1, "end_line": 1}).replace("'", '"') + ']}')
    found, dropped = weaknesses._scan_file(_file("line1\nline2\n"))
    assert len(found) == 1 and dropped == 0
    assert found[0]["file_path"] == "src/app.py"


def test_scan_file_survives_garbage_llm_output(monkeypatch):
    monkeypatch.setattr(weaknesses, "llm_available", lambda: True)
    monkeypatch.setattr(weaknesses, "call_llm", lambda *a, **kw: "total nonsense")
    assert weaknesses._scan_file(_file("x = 1\n")) == ([], 0)


def test_scan_file_long_content_flags_truncation_out_of_band(monkeypatch):
    captured = {}

    def fake_call(system, user, **kw):
        captured["user"] = user
        return '{"findings": []}'

    monkeypatch.setattr(weaknesses, "llm_available", lambda: True)
    monkeypatch.setattr(weaknesses, "call_llm", fake_call)
    big = _file("//" + "x" * 20000 + "\n")
    assert weaknesses._scan_file(big) == ([], 0)
    # The truncation is disclosed OUT of band and forbidden as a finding — the
    # old "# … (truncated)" marker inside the code was quoted back as a "bug".
    assert "# … (truncated)" not in captured["user"]
    assert "cut off at the end" in captured["user"]
    # Never mutate File.content itself — only what was sent.
    assert len(big.content) == 20003


def test_scan_file_drops_truncation_artifacts_only_when_truncated(monkeypatch):
    # A truncated file where the model reports a real bug AND a truncation
    # artifact: the artifact is dropped, the real finding survives, drop counted.
    monkeypatch.setattr(weaknesses, "llm_available", lambda: True)
    real = {**VALID, "title": "Off-by-one", "description": "Uses <= where < is meant.",
            "start_line": 2, "end_line": 2}
    artifact = {**VALID, "title": "Incomplete function implementation",
                "description": "The function is truncated mid-body and never returns.",
                "start_line": 40, "end_line": 40}   # far from the cut; caught by language
    payload = json.dumps({"findings": [real, artifact]})
    monkeypatch.setattr(weaknesses, "call_llm", lambda *a, **kw: payload)
    # A realistic many-line file (so the boundary window is a small tail, not
    # the whole file) that exceeds MAX_FILE_CHARS → truncated.
    big = _file("row = 1\n" * 6000)   # 48000 chars, 6000 lines
    found, dropped = weaknesses._scan_file(big)
    assert dropped == 1
    assert [f["title"] for f in found] == ["Off-by-one"]


# ---------- cap disclosures ----------

@pytest.fixture()
def llm_on(monkeypatch):
    monkeypatch.setattr(weaknesses, "llm_available", lambda: True)


def test_cap_notes_disclose_cap_and_truncation_but_not_silently(llm_on):
    small = [_file("x = 1\n"), _file("y = 2\n")]
    notes = weaknesses._cap_notes(small[:1], total_code=340, scan_all=False)
    assert any("Capped to 1 of 340" in n and "scan_all=true" in n for n in notes)

    big = [_file("z" * 21000 + "\n")]   # over the 20000-char per-file budget
    notes2 = weaknesses._cap_notes(big, total_code=1, scan_all=False)
    assert any("truncated" in n for n in notes2)


def test_cap_notes_disclose_offline_mode(monkeypatch):
    monkeypatch.setattr(weaknesses, "llm_available", lambda: False)
    notes = weaknesses._cap_notes([_file("x=1\n")], total_code=1, scan_all=True)
    assert notes == ["No LLM provider available — scan ran in offline mode and found nothing."]


# ---------- scan_files fan-out ----------

def test_scan_files_fans_out_reports_progress_and_sorts(llm_on, monkeypatch):
    def fake_scan(f: File):
        titles = {"b.py": "B finding", "a.py": "A finding"}
        return ([{"file_path": f.path, "start_line": 1, "title": titles[f.path],
                  "category": "style", "severity": "low"}], 0)

    monkeypatch.setattr(weaknesses, "_scan_file", fake_scan)
    files = [_file("x\n", "b.py"), _file("y\n", "a.py")]
    progress: list[tuple[int, int]] = []
    findings, notes = weaknesses.scan_files(
        files, 2, False, on_progress=lambda d, t: progress.append((d, t)))
    # Wiki-style stable ordering regardless of completion order.
    assert [f["file_path"] for f in findings] == ["a.py", "b.py"]
    assert progress[-1] == (2, 2)


# ---------- ADF description + create_issue request building ----------

def test_adf_description_shape_with_fix():
    adf = jira_client._adf_description("Bad thing", "src/a.py", 4, 6, "Do X instead.")
    assert adf["type"] == "doc" and adf["version"] == 1
    para, code = adf["content"]
    assert para["type"] == "paragraph"
    assert para["content"][0]["text"] == "Bad thing"
    assert code["type"] == "codeBlock"
    assert "src/a.py:4-6" in code["content"][0]["text"]
    assert "Suggested fix:\nDo X instead." in code["content"][0]["text"]


def test_adf_description_single_line_without_fix():
    adf = jira_client._adf_description("Bad", "a.py", 9, 9, None)
    text = adf["content"][1]["content"][0]["text"]
    assert text == "a.py:9"  # no range suffix, no fix block


def test_create_issue_payload_and_returned_url():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "10001", "key": "DOC-42"})

    with httpx.Client(transport=httpx.MockTransport(handler),
                      base_url="https://test.atlassian.net") as client:
        result = jira_client.create_issue(client, "DOC", "Task", {
            "severity": "high", "title": "Leak", "description": "It leaks.",
            "file_path": "src/a.py", "start_line": 1, "end_line": 2,
            "suggested_fix": None, "category": "security",
        })

    fields = captured["payload"]["fields"]
    assert captured["url"].endswith("/rest/api/3/issue")
    assert fields["project"] == {"key": "DOC"}
    assert fields["issuetype"] == {"name": "Task"}
    assert fields["summary"].startswith("[HIGH] Leak")
    assert fields["labels"] == ["coderlens-security"]
    assert fields["description"]["type"] == "doc"
    assert result == {"id": "10001", "key": "DOC-42",
                      "url": "https://test.atlassian.net/browse/DOC-42"}


def test_create_issue_summary_is_capped_at_255_chars():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(201, json={"id": "1", "key": "X-1"})

    with httpx.Client(transport=httpx.MockTransport(handler),
                      base_url="https://t.atlassian.net") as client:
        jira_client.create_issue(client, "X", "Task", {
            "severity": "low", "title": "t" * 600, "description": "d",
            "file_path": "a.py", "start_line": 1, "end_line": 1,
            "suggested_fix": None, "category": "style",
        })
    assert len(captured["payload"]["fields"]["summary"]) == 255


def test_jira_client_raises_clear_error_on_bad_credentials():
    def unauth(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Unauthorized"})

    with httpx.Client(transport=httpx.MockTransport(unauth),
                      base_url="https://t.atlassian.net") as client:
        with pytest.raises(RuntimeError, match="credentials"):
            jira_client.create_issue(client, "X", "Task", {
                "severity": "low", "title": "t", "description": "d",
                "file_path": "a.py", "start_line": 1, "end_line": 1,
                "suggested_fix": None, "category": "style",
            })


# ---------- read-time snippet slicing ----------

CONTENT = "\n".join(f"line{i}" for i in range(1, 101))  # 100 lines


def test_snippet_slices_inclusive_range():
    assert _snippet(CONTENT, 2, 4) == "line2\nline3\nline4"


def test_snippet_caps_at_max_lines_with_marker():
    out = _snippet(CONTENT, 1, 100, max_lines=46)
    assert out.count("\n") == 46
    assert out.endswith("# … (truncated)")


def test_snippet_handles_missing_content_and_out_of_range_lines():
    assert _snippet(None, 1, 2) == ""
    assert _snippet("", 1, 2) == ""
    assert _snippet("only", 50, 60) == ""
