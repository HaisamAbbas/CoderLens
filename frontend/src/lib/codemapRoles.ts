/** "Physical Code" role classifier — mirrors `classify_role` in
 *  analysis/codemap.py exactly (same rules, same order) so nodes added
 *  client-side (via the Expand-callers/callees feature, which reads from
 *  /api/symbol/{id} rather than the codemap builder) get the same
 *  mechanically-derived role/icon as server-built nodes. Keep both in sync
 *  if the rules ever change. */

// Identifier-boundary lookaround, NOT `\b`/`(^|_)`/`(_|$)` — see the matching
// comment in analysis/codemap.py::classify_role for why (dots, a trailing
// " <file_path>", etc. all defeat a plain word-boundary or underscore anchor).
const B = "(?<![A-Za-z0-9])", E = "(?![A-Za-z0-9])";
const ROLE_RULES: [string, string, string, RegExp][] = [
  ["test", "🧪", "Test", new RegExp(`(^|/)tests?/|${B}test_`, "i")],
  ["cache", "⚡", "Cache", new RegExp(`cach(e|ing)|redis|memoiz|${B}lru${E}`, "i")],
  ["queue", "📥", "Queue", new RegExp(`${B}queue${E}|celery|kafka|${B}mq${E}`, "i")],
  ["worker", "👷", "Worker", new RegExp(`${B}worker${E}|${B}consumer${E}|task_runner`, "i")],
  ["validator", "🔍", "Validator", new RegExp(`${B}(validate|verify|sanitiz)|validator`, "i")],
  ["parser", "🧹", "Parser", new RegExp(`${B}(parse|normali[sz]e|transform|extract)${E}|${B}parser${E}`, "i")],
  ["calculator", "🧮", "Calculator", new RegExp(`${B}(calculate|compute|predict|estimate|rank)${E}|${B}model${E}`, "i")],
  ["database", "💾", "Database", new RegExp(`${B}repositor|${B}backend${E}|${B}storage${E}|${B}(save|persist|fetch|query)${E}`, "i")],
  ["api", "🚪", "API", new RegExp(`${B}route${E}|${B}endpoint${E}|dispatch|${B}view${E}|${B}handler${E}`, "i")],
];

export function classifyRole(qualifiedName: string, filePath: string, kind: string):
  { role: string; icon: string; role_label: string } {
  const text = `${qualifiedName} ${filePath}`;
  for (const [role, icon, label, pat] of ROLE_RULES) {
    if (pat.test(text)) return { role, icon, role_label: label };
  }
  if (kind === "class") return { role: "object", icon: "📦", role_label: "Object" };
  if (kind === "method") return { role: "method", icon: "🔗", role_label: "Method" };
  return { role: "function", icon: "⚙️", role_label: "Function" };
}
