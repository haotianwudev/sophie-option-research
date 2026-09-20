"use client";

import { useEffect, useState } from "react";

type Mode = "system" | "light" | "dark";
const KEY = "option-research-theme";

function apply(mode: Mode) {
  const el = document.documentElement;
  if (mode === "system") el.removeAttribute("data-theme");
  else el.setAttribute("data-theme", mode);
}

/** system -> light -> dark. Storage is a convenience only; every access is guarded. */
export function ThemeToggle() {
  const [mode, setMode] = useState<Mode>("system");
  useEffect(() => {
    try {
      const saved = localStorage.getItem(KEY) as Mode | null;
      if (saved === "light" || saved === "dark") {
        setMode(saved);
        apply(saved);
      }
    } catch {
      /* storage blocked */
    }
  }, []);
  const next = () => {
    const m: Mode = mode === "system" ? "light" : mode === "light" ? "dark" : "system";
    setMode(m);
    apply(m);
    try {
      if (m === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, m);
    } catch {
      /* storage blocked */
    }
  };
  return (
    <button type="button" className="btn" onClick={next} aria-label={`Theme: ${mode}. Click to change`}>
      Theme: {mode}
    </button>
  );
}
