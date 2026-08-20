import { useEffect, useState } from "react";

export function useTheme() {
  const [theme, setTheme] = useState<string | null>(() => localStorage.getItem("theme"));
  useEffect(() => {
    const el = document.documentElement;
    if (theme) el.setAttribute("data-theme", theme);
    else el.removeAttribute("data-theme");
  }, [theme]);
  const isDark = theme
    ? theme === "dark"
    : window.matchMedia("(prefers-color-scheme: dark)").matches;
  const toggle = () => {
    const next = isDark ? "light" : "dark";
    setTheme(next);
    localStorage.setItem("theme", next);
  };
  return { isDark, toggle };
}
