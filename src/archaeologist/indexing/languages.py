"""Tree-sitter parser registry — one place that maps a File.language value to
a parsed grammar. Extraction (symbols.py) and reference-scanning (graph.py)
dispatch off this, so adding a language means: add the grammar package, add a
line here, then teach the two walkers its node-type names.
"""

import tree_sitter_go
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript
from tree_sitter import Language, Parser

# tsx shares the typescript grammar package (language_tsx).
_LANGUAGES: dict[str, Language] = {
    "python": Language(tree_sitter_python.language()),
    "javascript": Language(tree_sitter_javascript.language()),
    "typescript": Language(tree_sitter_typescript.language_typescript()),
    "tsx": Language(tree_sitter_typescript.language_tsx()),
    "go": Language(tree_sitter_go.language()),
}

# Languages with a full extraction walker (symbols.py + graph.py). Files in
# OTHER detected languages (rust, java, ...) still ingest as raw text and are
# searchable via the evidence index — they just don't get symbol-level
# analysis yet. Kept explicit so "supported" never silently drifts from what
# the walkers actually handle.
SUPPORTED = frozenset(_LANGUAGES)


def get_language(name: str | None) -> Language | None:
    return _LANGUAGES.get(name or "")


def new_parser(name: str | None) -> Parser | None:
    """A parser for `name`, or None when the language isn't supported —
    callers skip the file rather than guess with the wrong grammar."""
    lang = get_language(name)
    if lang is None:
        return None
    try:
        return Parser(lang)  # tree-sitter >= 0.22
    except TypeError:  # older API
        parser = Parser()
        parser.set_language(lang)
        return parser
