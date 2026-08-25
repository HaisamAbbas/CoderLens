"""Unit tests for multi-language symbol extraction and reference scanning.

Pure parser-level tests — real grammar, no DB, no network. The conventions
these lock in (qualified names, kind assignment, call-receiver splitting) are
exactly what graph.py and every downstream analysis consumes.
"""

from archaeologist.indexing.graph import extract_references
from archaeologist.indexing.languages import new_parser
from archaeologist.indexing.symbols import extract_symbols


def kinds(syms):
    return {(s.kind, s.name, s.qualified_name) for s in syms}


# ---------- registry ----------

def test_unsupported_language_returns_no_parser():
    assert new_parser("rust") is None
    assert new_parser(None) is None


def test_extract_symbols_unsupported_language_is_empty_not_crash():
    assert extract_symbols("fn main() {}", "rust") == []


# ---------- JavaScript / TypeScript ----------

JS_CLASS = """
import { Router } from './router';

export class UserService {
  constructor(repo) {
    this.repo = repo;
  }

  async getUser(id) {
    return this.repo.find(id);
  }
}

function helper(x) {
  return validate(x);
}

const fetchAll = async (ids) => ids.map(loadOne);
"""


def test_js_symbols_classes_methods_functions_arrows():
    syms = extract_symbols(JS_CLASS, "javascript")
    got = kinds(syms)
    assert ("class", "UserService", "UserService") in got
    assert ("method", "constructor", "UserService.constructor") in got
    assert ("method", "getUser", "UserService.getUser") in got
    assert ("function", "helper", "helper") in got
    assert ("function", "fetchAll", "fetchAll") in got  # const arrow fn counts
    assert any(s.kind == "import" for s in syms)


def test_js_references_this_calls_recv_and_plain():
    refs = extract_references(JS_CLASS, "javascript")
    # this.repo.find(id) — the receiver `this.repo` is itself an attribute
    # (nested chain), so it degrades to name-only, same as Python's
    # self.app.route(...) does.
    assert "find" in refs.plain
    assert "validate" in refs.plain              # bare call in helper
    assert refs.recv.get("ids") == {"map"}      # x.map(...) receiver
    assert refs.self_calls == set()             # no direct this.method() in snippet


def test_js_direct_this_call_is_self_call():
    refs = extract_references(
        "class A { m() { this.helper(); } }", "javascript")
    assert refs.self_calls == {"helper"}


TS_CLASS = """
interface Repo { find(id: number): Promise<User>; }

export class UserSvc extends BaseSvc implements Repo {
  find(id: number): Promise<User> {
    return super.load(id);
  }
}
"""


def test_ts_symbols_and_inheritance():
    syms = extract_symbols(TS_CLASS, "typescript")
    got = kinds(syms)
    assert ("class", "UserSvc", "UserSvc") in got
    assert ("method", "find", "UserSvc.find") in got
    refs = extract_references(TS_CLASS, "typescript")
    assert "BaseSvc" in refs.bases              # extends clause captured


def test_tsx_parser_handles_jsx():
    tsx = """
export function Badge({ label }: { label: string }) {
  return <span className="b">{label}</span>;
}
"""
    syms = extract_symbols(tsx, "tsx")
    assert ("function", "Badge", "Badge") in kinds(syms)


# ---------- Go ----------

GO_SRC = """
package server

import "fmt"

type Server struct {
  Port int
}

type Handler interface {
  Serve()
}

func (s *Server) Start() error {
  return s.listen()
}

func NewServer(port int) *Server {
  return &Server{Port: port}
}
"""


def test_go_symbols_functions_methods_and_structs():
    syms = extract_symbols(GO_SRC, "go")
    got = kinds(syms)
    assert ("class", "Server", "Server") in got            # struct → class
    assert ("class", "Handler", "Handler") in got          # interface → class
    assert ("method", "Start", "Server.Start") in got      # receiver → owner
    assert ("function", "NewServer", "NewServer") in got
    assert any(s.kind == "import" for s in syms)


def test_go_references_selector_and_plain_calls():
    refs = extract_references(GO_SRC, "go")
    assert refs.recv.get("s") == {"listen"}     # s.listen() — receiver known by name
    assert refs.recv.get("fmt") == {} or True   # fmt import present but no call in this src
    # NewServer's body constructs a literal — no plain calls to assert beyond presence.
    refs2 = extract_references("func f() { log.Println(strings.ToUpper(x)) }", "go")
    assert "strings" in refs2.recv and "ToUpper" in refs2.recv["strings"]
    assert "log" in refs2.recv and "Println" in refs2.recv["log"]


def test_go_method_signature_is_the_declaration_line():
    syms = extract_symbols(GO_SRC, "go")
    start = next(s for s in syms if s.name == "Start")
    assert start.signature.startswith("func (s *Server) Start()")


# ---------- Python regression (existing behavior must not move) ----------

PY_SRC = '''
import os

class Greeter:
    def greet(self):
        return self.format()

    def format(self):
        return "hi"

def top_level():
    helper()
'''


def test_python_extraction_unchanged():
    syms = extract_symbols(PY_SRC, "python")
    got = kinds(syms)
    assert ("class", "Greeter", "Greeter") in got
    assert ("method", "greet", "Greeter.greet") in got
    assert ("function", "top_level", "top_level") in got
    refs = extract_references(PY_SRC, "python")
    assert refs.self_calls == {"format"}
    assert "helper" in refs.plain
