"""AST symbol extraction via tree-sitter (Python, JavaScript/TypeScript, Go).

Chunks code *by symbol* — each class / method / function / import becomes one
unit, carrying its qualified name, line range, signature, docstring, and source.
This is what makes retrieval code-aware instead of splitting on token windows.

Per-language walkers live here; the grammar registry lives in languages.py.
Qualified-name conventions (deliberately uniform so graph.py and every
downstream consumer stay language-agnostic):
- Python  Class.method          (class stack)
- JS/TS   Class.method          (class stack, same rule)
- Go      ReceiverType.Method   (from the method's receiver)
"""

from dataclasses import dataclass

from archaeologist.indexing.languages import new_parser


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


def extract_symbols(source: str, language: str = "python") -> list[Symbol]:
    """Extract top-level classes/functions, their methods, and imports."""
    src = source.encode("utf-8")
    parser = new_parser(language)
    if parser is None:
        return []
    tree = parser.parse(src)
    out: list[Symbol] = []
    if language == "python":
        _walk_py(tree.root_node, src, [], out)
    elif language in ("javascript", "typescript", "tsx"):
        _walk_js(tree.root_node, src, [], out)
    elif language == "go":
        _walk_go(tree.root_node, src, out)
    return out


def _text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


# ---------- shared emit ----------

def _emit(def_node, outer_node, src: bytes, class_stack: list[str], out: list[Symbol],
          name_node=None, body_node=None, is_class: bool = False,
          docstring: str | None = None) -> None:
    name = _text(name_node, src) if name_node is not None else "?"
    kind = "class" if is_class else ("method" if class_stack else "function")
    # Uniform convention: Class.method / ReceiverType.Method / bare Name.
    qualified = ".".join([*class_stack, name])

    signature = None
    if body_node is not None:
        signature = src[def_node.start_byte : body_node.start_byte].decode(
            "utf-8", "replace").strip().rstrip(":").strip()
    elif name_node is not None:
        signature = _text(def_node, src).splitlines()[0][:200] if def_node.type in (
            "method_declaration", "function_declaration") else None

    out.append(
        Symbol(
            kind=kind,
            name=name,
            qualified_name=qualified,
            start_line=outer_node.start_point[0] + 1,
            end_line=outer_node.end_point[0] + 1,
            signature=signature or None,
            docstring=docstring,
            code=_text(outer_node, src),
        )
    )


# ---------- Python ----------

def _walk_py(node, src: bytes, class_stack: list[str], out: list[Symbol]) -> None:
    for child in node.named_children:
        t = child.type
        if t == "decorated_definition":
            inner = child.child_by_field_name("definition") or child.named_children[-1]
            _emit_py(inner, child, src, class_stack, out)
        elif t in ("function_definition", "class_definition"):
            _emit_py(child, child, src, class_stack, out)
        elif t in ("import_statement", "import_from_statement"):
            text = _text(child, src)
            out.append(
                Symbol("import", text.splitlines()[0][:200], "",
                       child.start_point[0] + 1, child.end_point[0] + 1, None, None, text)
            )
        else:
            # Recurse into non-def blocks (if/try/with) to catch conditionally-defined symbols.
            _walk_py(child, src, class_stack, out)


def _emit_py(def_node, outer_node, src: bytes, class_stack: list[str], out: list[Symbol]) -> None:
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
        _walk_py(body, src, class_stack + [name], out)


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


# ---------- JavaScript / TypeScript / TSX ----------

_JS_DEF_TYPES = {"function_declaration", "generator_function_declaration"}
_JS_CLASS_TYPES = {"class_declaration", "abstract_class_declaration"}


def _walk_js(node, src: bytes, class_stack: list[str], out: list[Symbol]) -> None:
    for child in node.named_children:
        t = child.type
        if t in _JS_DEF_TYPES or t in _JS_CLASS_TYPES:
            _emit_js_named(child, src, class_stack, out)
        elif t in ("import_statement",):
            text = _text(child, src)
            out.append(
                Symbol("import", text.splitlines()[0][:200], "",
                       child.start_point[0] + 1, child.end_point[0] + 1, None, None, text)
            )
        elif t in ("lexical_declaration", "variable_declaration"):
            # const foo = () => {} / function bar() {} — module-level named fns.
            _emit_js_arrow(child, src, class_stack, out)
        elif t == "export_statement":
            # export function/class/const — recurse; the walker sees the inner def.
            _walk_js(child, src, class_stack, out)
        else:
            _walk_js(child, src, class_stack, out)


def _emit_js_named(def_node, src: bytes, class_stack: list[str], out: list[Symbol]) -> None:
    name_node = def_node.child_by_field_name("name")
    is_class = def_node.type in _JS_CLASS_TYPES
    body = def_node.child_by_field_name("body")
    _emit(def_node, def_node, src, class_stack, out,
          name_node=name_node, body_node=body, is_class=is_class)
    if is_class and body is not None:
        for member in body.named_children:
            if member.type == "method_definition":
                m_name = member.child_by_field_name("name")
                m_body = member.child_by_field_name("body")
                _emit(member, member, src, class_stack + [_text(name_node, src)], out,
                      name_node=m_name, body_node=m_body)
            elif member.type in _JS_CLASS_TYPES:
                # Nested class definitions are rare; keep the stack accurate.
                _emit_js_named(member, src, class_stack + [_text(name_node, src)], out)


def _emit_js_arrow(decl_node, src: bytes, class_stack: list[str], out: list[Symbol]) -> None:
    for declarator in decl_node.named_children:
        if declarator.type != "variable_declarator":
            continue
        value = declarator.child_by_field_name("value")
        if value is None or value.type not in (
                "arrow_function", "function_expression", "generator_function"):
            continue
        name_node = declarator.child_by_field_name("name")
        _emit(declarator, decl_node, src, class_stack, out,
              name_node=name_node, body_node=value)


# ---------- Go ----------

def _walk_go(node, src: bytes, out: list[Symbol]) -> None:
    for child in node.named_children:
        t = child.type
        if t == "function_declaration":
            _emit_go_func(child, src, out)
        elif t == "method_declaration":
            _emit_go_method(child, src, out)
        elif t in ("type_declaration",):
            for spec in _go_type_specs(child):
                _emit_go_type(spec, src, out)
        elif t in ("import_declaration",):
            text = _text(child, src)
            out.append(
                Symbol("import", text.splitlines()[0][:200], "",
                       child.start_point[0] + 1, child.end_point[0] + 1, None, None, text)
            )
        else:
            _walk_go(child, src, out)


def _go_type_specs(type_decl):
    for child in type_decl.named_children:
        if child.type == "type_spec":
            yield child
        elif child.type == "type_spec_list":
            yield from [s for s in child.named_children if s.type == "type_spec"]


def _emit_go_func(node, src: bytes, out: list[Symbol]) -> None:
    name_node = node.child_by_field_name("name")
    body = node.child_by_field_name("body")
    _emit(node, node, src, [], out, name_node=name_node, body_node=body)


def _emit_go_method(node, src: bytes, out: list[Symbol]) -> None:
    name_node = node.child_by_field_name("name")
    body = node.child_by_field_name("body")
    receiver = node.child_by_field_name("receiver")
    owner = "?"
    if receiver is not None:
        for p in receiver.named_children:
            ptype = p.child_by_field_name("type")
            if ptype is None:
                continue
            # pointer_type wraps the base type — unwrap to the bare name.
            if ptype.type == "pointer_type":
                ptype = ptype.named_children[0] if ptype.named_children else ptype
            owner = _text(ptype, src).lstrip("*")
            break
    _emit(node, node, src, [owner], out, name_node=name_node, body_node=body)


def _emit_go_type(spec, src: bytes, out: list[Symbol]) -> None:
    name_node = spec.child_by_field_name("name")
    stype = spec.child_by_field_name("type")
    if stype is None or stype.type not in ("struct_type", "interface_type"):
        return  # aliases / named primitives aren't classes for our purposes
    _emit(spec, spec, src, [], out, name_node=name_node, body_node=stype, is_class=True)
