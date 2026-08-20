import { useEffect, useState } from "react";

export type Persona = "new" | "junior" | "senior";
const KEY = "archaeologist.persona";
const listeners = new Set<(p: Persona) => void>();

function read(): Persona {
  const v = localStorage.getItem(KEY);
  return v === "new" || v === "junior" || v === "senior" ? v : "junior";
}

function write(p: Persona) {
  localStorage.setItem(KEY, p);
  listeners.forEach((l) => l(p));
}

/** One persona setting shared across the whole app (persisted, not per-page
 *  state) — same underlying data everywhere, just a different slice of it
 *  surfaced depending on how deep the reader wants to go. */
export function usePersona(): [Persona, (p: Persona) => void] {
  const [persona, setPersona] = useState<Persona>(read);
  useEffect(() => {
    const l = (p: Persona) => setPersona(p);
    listeners.add(l);
    return () => { listeners.delete(l); };
  }, []);
  return [persona, write];
}
