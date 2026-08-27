"""Unit tests for Architecture Delta's pure functions.

Everything here works off synthetic path lists, which is the whole point of the
design: the shape of an architecture is derived from paths alone, so the diff
logic is testable without a git clone, a database, or an LLM. The git-backed
half (list_refs / paths_at) is a thin wrapper over `git ls-tree` and is
exercised against real clones by hand.
"""

from archaeologist.analysis.arch_delta import diff_shapes, mermaid_delta
from archaeologist.analysis.architecture import shape_from_paths

BEFORE = [
    "src/pkg/__init__.py", "src/pkg/app.py", "src/pkg/config.py",
    "src/pkg/json/__init__.py", "src/pkg/json/tag.py",
    "README.md", "docs/index.md",
]
AFTER = [
    "src/pkg/__init__.py", "src/pkg/app.py", "src/pkg/config.py",
    "src/pkg/json/__init__.py", "src/pkg/json/tag.py",
    "src/pkg/sansio/__init__.py", "src/pkg/sansio/base.py",
    "README.md", "docs/index.md",
]


def _shape(paths):
    return shape_from_paths(paths)


def test_shape_is_derived_from_paths_alone():
    shape = _shape(BEFORE)
    assert shape["package"] == "src/pkg"
    assert [s["submodule"] for s in shape["submodules"]] == ["core", "json"]
    # Only code counts as code — the markdown files are structure, not modules.
    assert shape["counts"]["code_files"] == 5


def test_submodule_order_is_deterministic_on_ties():
    # Equal weights must not fall back on dict insertion order: a re-ingest that
    # happened to walk files differently would otherwise read as a reordering.
    a = _shape(["p/x/one.py", "p/y/two.py"])
    b = _shape(["p/y/two.py", "p/x/one.py"])
    assert [s["submodule"] for s in a["submodules"]] == [s["submodule"] for s in b["submodules"]]


def test_added_submodule_and_files_are_reported():
    d = diff_shapes(_shape(BEFORE), _shape(AFTER))
    assert d["submodules_added"] == ["sansio"]
    assert d["submodules_removed"] == []
    assert d["files_added"] == ["src/pkg/sansio/__init__.py", "src/pkg/sansio/base.py"]
    assert d["files_removed"] == []
    assert d["unchanged"] is False


def test_identical_shapes_report_unchanged():
    d = diff_shapes(_shape(AFTER), _shape(AFTER))
    assert d["unchanged"] is True
    assert d["counts"]["files_added"] == 0 and d["counts"]["files_removed"] == 0
    assert d["files_moved"] == []


def test_src_layout_migration_is_one_fact_not_hundreds_of_moves():
    # Every file's absolute path changes when a project adopts src/. Comparing
    # package-relative paths keeps that a single `package` fact instead of
    # swamping the receipt.
    flat = ["pkg/__init__.py", "pkg/app.py", "pkg/config.py"]
    nested = ["src/pkg/__init__.py", "src/pkg/app.py", "src/pkg/config.py"]
    d = diff_shapes(_shape(flat), _shape(nested))
    assert d["package"] == {"before": "pkg", "after": "src/pkg"}
    assert d["package_relocated"] is True
    assert d["files_added"] == [] and d["files_removed"] == []
    assert d["files_moved"] == []
    assert d["submodules_changed"] == []


def test_a_different_package_taking_over_is_not_treated_as_a_relocation():
    # `p` -> `p/sub` is not one package moving, it is another package winning
    # the most-code heuristic. Comparing those package-relative would align
    # unrelated files that merely share a name, so full paths are used instead.
    before = ["p/one.py", "p/two.py", "p/sub/only.py"]
    after = ["p/one.py", "p/sub/only.py", "p/sub/b.py", "p/sub/c.py"]
    d = diff_shapes(_shape(before), _shape(after))
    assert d["package"] == {"before": "p", "after": "p/sub"}
    assert d["package_relocated"] is False


def test_package_choice_does_not_depend_on_path_order():
    # A tie for "most code" must resolve the same way regardless of the order
    # git happened to list the tree, or two identical layouts would diff.
    one = _shape(["p/a/x.py", "p/b/y.py", "p/keep.py"])
    two = _shape(["p/keep.py", "p/b/y.py", "p/a/x.py"])
    assert one["package"] == two["package"]
    assert diff_shapes(one, two)["unchanged"] is True


def test_a_file_moving_between_submodules_is_a_move_not_add_plus_remove():
    # Enough files at the package root that `p` stays the primary package on
    # both sides — otherwise the move being tested is masked by the package
    # itself changing, which is a different fact (see the test below).
    before = ["p/__init__.py", "p/a1.py", "p/a2.py", "p/a3.py",
              "p/helpers.py", "p/sub/__init__.py"]
    after = ["p/__init__.py", "p/a1.py", "p/a2.py", "p/a3.py",
             "p/sub/__init__.py", "p/sub/helpers.py"]
    d = diff_shapes(_shape(before), _shape(after))
    assert len(d["files_moved"]) == 1
    move = d["files_moved"][0]
    assert move["file"] == "helpers.py"
    assert move["from"] == "p/helpers.py" and move["to"] == "p/sub/helpers.py"
    assert move["from_submodule"] == "core" and move["to_submodule"] == "sub"
    # Counted once, as a move — never double-reported as an add and a remove.
    assert d["files_added"] == [] and d["files_removed"] == []


def test_ambiguous_basenames_are_never_claimed_as_moves():
    # Two __init__.py files vanish and two appear; nothing here justifies
    # pairing any particular one with another, so none are called moves.
    before = ["p/a/__init__.py", "p/b/__init__.py", "p/keep.py"]
    after = ["p/c/__init__.py", "p/d/__init__.py", "p/keep.py"]
    d = diff_shapes(_shape(before), _shape(after))
    assert d["files_moved"] == []
    assert len(d["files_added"]) == 2 and len(d["files_removed"]) == 2


def test_top_level_structure_changes_are_reported():
    d = diff_shapes(_shape(["scripts/go.py", "pkg/a.py"]),
                    _shape(["docs/x.md", "pkg/a.py"]))
    assert d["top_level_added"] == ["docs"] and d["top_level_removed"] == ["scripts"]


def test_receipt_counts_stay_exact_when_the_item_lists_truncate():
    from archaeologist.analysis import arch_delta

    n = arch_delta.MAX_FACTS + 25
    after = ["p/keep.py"] + [f"p/gen/f{i}.py" for i in range(n)]
    d = diff_shapes(_shape(["p/keep.py"]), _shape(after))
    assert d["truncated"] is True
    assert len(d["files_added"]) == arch_delta.MAX_FACTS   # list is capped
    assert d["counts"]["files_added"] == n                 # count is not


def test_mermaid_is_generated_mechanically_and_classifies_each_submodule():
    before, after = _shape(BEFORE), _shape(AFTER)
    src = mermaid_delta(diff_shapes(before, after), before, after)
    assert src.startswith("flowchart LR")
    assert "class s_sansio added;" in src
    assert "class s_json kept;" in src
    # Labels are quoted and free of the characters that break Mermaid's parser.
    assert "classDef added" in src and "classDef removed" in src


def test_mermaid_returns_none_when_there_is_no_structure():
    empty = _shape(["README.md"])
    assert mermaid_delta(diff_shapes(empty, empty), empty, empty) is None
