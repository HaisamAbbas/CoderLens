import type { SymbolIndex, SymbolIndexEntry } from "./types";

/** Build the client-side resolution maps (name→defs, id→def) used for hover-peek
 *  and go-to-definition. Ambiguous names are ordered most-referenced first. */
export function buildSymbolIndex(list: SymbolIndexEntry[]): SymbolIndex {
  const byName = new Map<string, SymbolIndexEntry[]>();
  const byId = new Map<number, SymbolIndexEntry>();
  for (const e of list) {
    byId.set(e.id, e);
    (byName.get(e.name) ?? byName.set(e.name, []).get(e.name)!).push(e);
  }
  for (const arr of byName.values()) arr.sort((a, b) => b.callers - a.callers);
  return { list, byName, byId };
}
