"""AST symbol extraction via tree-sitter (Python).

Chunks code *by symbol* — each class / method / function / import becomes one
unit, carrying its qualified name, line range, signature, docstring, and source.
This is what makes retrieval code-aware instead of splitting on token windows.
"""

from dataclasses import dataclass

import tree_sitter_python
from tree_sitter import Language, Parser

_PY_LANGUAGE = Language(tree_sitter_python.language())


def _new_parser() -> Parser:
    try:
        return Parser(_PY_LANGUAGE)  # tree-sitter >= 0.22
    except TypeError:  # older API
        parser = Parser()
        parser.set_language(_PY_LANGUAGE)
        return parser


@dataclass
class Symbol:
    kind: str  # class | method | function | import
    name: str
    qualified_name: str
    start_line: int
    end_line: int
    signature: str | None
    docstring: str | None
    code: str


def extract_symbols(source: str) -> list[Symbol]:
    """Extract top-level classes/functions, their methods, and imports."""
    src = source.encode("utf-8")
    tree = _new_parser().parse(src)
    out: list[Symbol] = []
    _walk(tree.root_node, src, [], out)
    return out


def _text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _walk(node, src: bytes, class_stack: list[str], out: list[Symbol]) -> None:
    for child in node.named_children:
        t = child.type
        if t == "decorated_definition":
            inner = child.child_by_field_name("definition") or child.named_children[-1]
            _emit(inner, child, src, class_stack, out)
        elif t in ("function_definition", "class_definition"):
            _emit(child, child, src, class_stack, out)
        elif t in ("import_statement", "import_from_statement"):
            text = _text(child, src)
            out.append(
                Symbol("import", text.splitlines()[0][:200], "",
                       child.start_point[0] + 1, child.end_point[0] + 1, None, None, text)
            )
        else:
            # Recurse into non-def blocks (if/try/with) to catch conditionally-defined symbols.
            _walk(child, src, class_stack, out)


def _emit(def_node, outer_node, src: bytes, class_stack: list[str], out: list[Symbol]) -> None:
    name_node = def_node.child_by_field_name("name")
    name = _text(name_node, src) if name_node else "?"
    is_class = def_node.type == "class_definition"
    kind = "class" if is_class else ("method" if class_stack else "function")
    qualified = ".".join(class_stack + [name])

    body = def_node.child_by_field_name("body")
    signature = None
    if body is not None:
        signature = src[def_node.start_byte : body.start_byte].decode("utf-8", "replace").strip()
        signature = signature.rstrip(":").strip()

    out.append(
        Symbol(
            kind=kind,
            name=name,
            qualified_name=qualified,
            start_line=outer_node.start_point[0] + 1,
            end_line=outer_node.end_point[0] + 1,
            signature=signature,
            docstring=_docstring(body, src),
            code=_text(outer_node, src),
        )
    )

    # Recurse into class bodies to capture methods; skip function bodies (nested helpers).
    if is_class and body is not None:
        _walk(body, src, class_stack + [name], out)


def _docstring(body, src: bytes) -> str | None:
    if body is None:
        return None
    for stmt in body.named_children:
        if stmt.type == "expression_statement" and stmt.named_children:
            inner = stmt.named_children[0]
            if inner.type == "string":
                for part in inner.named_children:
                    if part.type == "string_content":
                        return _text(part, src).strip()
                return _text(inner, src).strip("\"'").strip()
        return None  # only the first statement can be a docstring
    return None
