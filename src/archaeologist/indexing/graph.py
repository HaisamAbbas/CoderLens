"""Build the dependency graph: `call` and `inherit` edges between symbols.

Resolution is name-based (Python is dynamic), so it is deliberately conservative:
- builtins / very common names are ignored,
- a name that matches too many symbols is treated as too ambiguous and skipped,
- only edges that resolve to a symbol *in this repo* are stored.

The result is an internal-dependency graph good enough to answer
"what breaks if I remove X", coupling, and execution-path questions.
"""

from collections import defaultdict

from sqlalchemy import delete, insert, select

from archaeologist.indexing.symbols import _new_parser
from archaeologist.models.db import init_db, session_scope
from archaeologist.models.entities import Repo, Symbol, SymbolEdge

# Names too common to yield meaningful internal edges.
STOPLIST = {
    "len", "str", "int", "float", "bool", "list", "dict", "set", "tuple", "type",
    "isinstance", "issubclass", "getattr", "setattr", "hasattr", "delattr",
    "super", "print", "repr", "format", "open", "range", "enumerate", "zip",
    "map", "filter", "sorted", "reversed", "iter", "next", "vars", "dir",
    "min", "max", "sum", "any", "all", "abs", "id", "hash", "callable",
    "property", "staticmethod", "classmethod", "object", "Exception",
}
MAX_NAME_FANOUT = 8  # a name resolving to more symbols than this is too ambiguous


def _iter_nodes(node):
    yield node
    for child in node.children:
        yield from _iter_nodes(child)


def _text(node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


class References:
    """Calls found inside a symbol's source, split by how confidently the
    receiver is known — this is what drives edge confidence in `build_graph`.

    - `plain`: a bare `foo()` call — no receiver, resolved purely by name.
    - `self_calls`: `self.foo()` — receiver is *this* method's own class.
    - `recv`: `x.foo()` where `x` is a plain identifier other than `self` —
      resolved only when `x` happens to name a class in this repo (e.g. a
      direct `Scaffold.get(...)`); otherwise treated as unresolvable-by-receiver.
    - `bases`: base-class names from a `class` definition (inheritance).
    """

    def __init__(self) -> None:
        self.plain: set[str] = set()
        self.self_calls: set[str] = set()
        self.recv: dict[str, set[str]] = defaultdict(set)
        self.bases: set[str] = set()


def extract_references(code: str) -> References:
    """Extract calls (split by receiver confidence) and base classes referenced
    inside a symbol's source."""
    src = code.encode("utf-8")
    tree = _new_parser().parse(src)
    refs = References()

    for node in _iter_nodes(tree.root_node):
        if node.type == "call":
            fn = node.child_by_field_name("function")
            if fn is None:
                continue
            if fn.type == "identifier":
                name = _text(fn, src)
                if not name.startswith("__"):
                    refs.plain.add(name)
            elif fn.type == "attribute":
                obj = fn.child_by_field_name("object")
                attr = fn.child_by_field_name("attribute")
                if attr is None:
                    continue
                attr_name = _text(attr, src)
                if attr_name.startswith("__"):
                    continue
                if obj is not None and obj.type == "identifier":
                    obj_name = _text(obj, src)
                    if obj_name == "self":
                        refs.self_calls.add(attr_name)
                    else:
                        refs.recv[obj_name].add(attr_name)
                else:
                    # Nested attribute chain (e.g. `self.app.route(...)`) — the
                    # receiver isn't a simple name, so fall back to name-only.
                    refs.plain.add(attr_name)
        elif node.type == "class_definition":
            supers = node.child_by_field_name("superclasses")
            if supers is not None:
                for arg in supers.named_children:
                    if arg.type == "identifier":
                        refs.bases.add(_text(arg, src))
                    elif arg.type == "attribute":
                        attr = arg.child_by_field_name("attribute")
                        if attr is not None:
                            refs.bases.add(_text(attr, src))
    return refs


# Confidence tiers (mirrors the 1.0 / ~0.9 / 0.5 scheme common to name-based
# resolvers): exact = only one symbol in the repo has this name; receiver =
# the call's receiver (self, or a directly-named class) is known, so it
# resolves against that specific class's method-resolution order; fuzzy =
# the name matches multiple unrelated symbols and there's no receiver to
# disambiguate with, so every candidate is kept but discounted.
CONF_EXACT = 1.0
CONF_RECEIVER = 0.9
CONF_FUZZY = 0.5
CONF_INHERIT_AMBIGUOUS = 0.6


def _resolve_via_mro(owner: str, name: str, method_by_owner: dict[str, dict[str, int]],
                     class_bases: dict[str, set[str]], visited: set[str]) -> int | None:
    """Walk the (name-based, approximate) method-resolution order starting at
    `owner` looking for a method called `name` — used for `self.foo()` and
    `ClassName.foo()` calls, where the receiver's type is known."""
    if owner in visited:
        return None
    visited.add(owner)
    hit = method_by_owner.get(owner, {}).get(name)
    if hit is not None:
        return hit
    for base in class_bases.get(owner, ()):
        found = _resolve_via_mro(base, name, method_by_owner, class_bases, visited)
        if found is not None:
            return found
    return None


def build_graph(repo_id: int | None = None) -> dict:
    """Build the dependency graph. `repo_id` targets a specific repo (used by
    the web ingestion job); when omitted, the most recently ingested one."""
    init_db()
    stats = {"call": 0, "inherit": 0, "unresolved_skipped": 0,
              "call_exact": 0, "call_receiver": 0, "call_fuzzy": 0}

    with session_scope() as session:
        repo = (session.get(Repo, repo_id) if repo_id is not None
                else session.scalar(select(Repo).order_by(Repo.id.desc())))
        if repo is None:
            raise SystemExit("No repo ingested — run Phase 1 + Phase 2 first.")
        symbols = session.scalars(select(Symbol).where(Symbol.repo_id == repo.id)).all()
        if not symbols:
            raise SystemExit("No symbols — run the Phase 2 indexer first.")

        callable_by_name: dict[str, list[int]] = defaultdict(list)
        class_by_name: dict[str, list[int]] = defaultdict(list)
        method_by_owner: dict[str, dict[str, int]] = defaultdict(dict)
        for s in symbols:
            if s.kind in ("function", "method"):
                callable_by_name[s.name].append(s.id)
            elif s.kind == "class":
                class_by_name[s.name].append(s.id)
            if s.kind == "method" and "." in s.qualified_name:
                owner = s.qualified_name.rsplit(".", 1)[0]
                method_by_owner[owner].setdefault(s.name, s.id)

        # Base-class names per class (by name — Python has no unique class ids
        # at this level of analysis), used to walk the MRO for self/receiver calls.
        class_bases: dict[str, set[str]] = defaultdict(set)
        for s in symbols:
            if s.kind == "class" and s.code:
                class_bases[s.name] |= extract_references(s.code).bases

        session.execute(delete(SymbolEdge).where(SymbolEdge.repo_id == repo.id))

        # (src, dst, type) -> confidence; a pair can be reached via more than one
        # resolution path, so keep the most confident one rather than duplicating.
        best: dict[tuple[int, int, str], tuple[float, str]] = {}

        def add(src: int, dst: int, name: str, edge_type: str, confidence: float) -> None:
            if dst == src:
                return
            key = (src, dst, edge_type)
            if key not in best or confidence > best[key][0]:
                best[key] = (confidence, name)

        for s in symbols:
            if s.kind == "import" or not s.code:
                continue
            refs = extract_references(s.code)
            owner = s.qualified_name.rsplit(".", 1)[0] if (
                s.kind == "method" and "." in s.qualified_name) else None

            # self.foo() — receiver is this method's own class; walk its MRO.
            for name in refs.self_calls:
                dst = _resolve_via_mro(owner, name, method_by_owner, class_bases, set()) if owner else None
                if dst is not None:
                    add(s.id, dst, name, "call", CONF_RECEIVER)
                    stats["call_receiver"] += 1
                else:
                    stats["unresolved_skipped"] += 1

            # x.foo() where x isn't self — only resolvable when x directly names
            # a class in this repo (e.g. `Scaffold.get(...)`); otherwise the
            # method name alone is all we have, so it joins the fuzzy pool.
            for recv_name, methods in refs.recv.items():
                known_class = recv_name in class_by_name
                for name in methods:
                    if name in STOPLIST:
                        continue
                    if known_class:
                        dst = _resolve_via_mro(recv_name, name, method_by_owner, class_bases, set())
                        if dst is not None:
                            add(s.id, dst, name, "call", CONF_RECEIVER)
                            stats["call_receiver"] += 1
                        else:
                            stats["unresolved_skipped"] += 1
                        continue
                    targets = callable_by_name.get(name, [])
                    if not targets or len(targets) > MAX_NAME_FANOUT:
                        stats["unresolved_skipped"] += 1
                        continue
                    for dst in targets:
                        add(s.id, dst, name, "call", CONF_FUZZY)
                    stats["call_fuzzy"] += 1

            # bare foo() — no receiver at all; confident only if the name is
            # unique across the repo, otherwise every candidate is kept, discounted.
            for name in refs.plain:
                if name in STOPLIST:
                    continue
                targets = callable_by_name.get(name, [])
                if not targets or len(targets) > MAX_NAME_FANOUT:
                    stats["unresolved_skipped"] += 1
                    continue
                conf = CONF_EXACT if len(targets) == 1 else CONF_FUZZY
                for dst in targets:
                    add(s.id, dst, name, "call", conf)
                stats["call_exact" if conf == CONF_EXACT else "call_fuzzy"] += 1

            for name in refs.bases:
                targets = class_by_name.get(name, [])
                if not targets:
                    continue
                conf = CONF_EXACT if len(targets) == 1 else CONF_INHERIT_AMBIGUOUS
                for dst in targets:
                    add(s.id, dst, name, "inherit", conf)

        edges = [
            {"repo_id": repo.id, "src_symbol_id": src, "dst_symbol_id": dst,
             "dst_name": name, "edge_type": etype, "confidence": conf}
            for (src, dst, etype), (conf, name) in best.items()
        ]
        stats["call"] = sum(1 for k in best if k[2] == "call")
        stats["inherit"] = sum(1 for k in best if k[2] == "inherit")

        if edges:
            session.execute(insert(SymbolEdge), edges)

    return stats


def main() -> None:
    print("Building dependency graph ...")
    stats = build_graph()
    print(f"  call edges     : {stats['call']}  "
          f"(exact {stats['call_exact']} · receiver {stats['call_receiver']} · fuzzy {stats['call_fuzzy']})")
    print(f"  inherit edges  : {stats['inherit']}")
    print(f"  skipped (unresolved/ambiguous name refs): {stats['unresolved_skipped']}")


if __name__ == "__main__":
    main()
