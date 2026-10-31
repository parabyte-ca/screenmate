import { createContext, useContext, useEffect, useState } from "react";

export type Theme = "light" | "dark" | "system";

/** Colour themes (src/themes.css). Each works in light and dark. */
export const PALETTES = [
  { id: "classic",   name: "Classic",   swatch: "#2563eb", note: "Blue on grey" },
  { id: "projector", name: "Projector", swatch: "#d97706", note: "Warm amber on stone" },
  { id: "lagoon",    name: "Lagoon",    swatch: "#0d9488", note: "Teal on slate" },
  { id: "velvet",    name: "Velvet",    swatch: "#e11d48", note: "Deep rose on zinc" },
  { id: "graphite",  name: "Graphite",  swatch: "#27272a", note: "Monochrome" },
  { id: "midnight",  name: "Midnight",  swatch: "#4f46e5", note: "Indigo on slate" },
  { id: "sunset",    name: "Sunset",    swatch: "#ea580c", note: "Orange on neutral" },
] as const;
export type Palette = (typeof PALETTES)[number]["id"];
const PALETTE_KEY = "sm-palette";

interface Ctx {
  theme: Theme;
  setTheme: (t: Theme) => void;
  palette: Palette;
  setPalette: (p: Palette) => void;
}

const ThemeContext = createContext<Ctx>({
  theme: "system", setTheme: () => {}, palette: "classic", setPalette: () => {},
});

function storedPalette(): Palette {
  try {
    const v = localStorage.getItem(PALETTE_KEY);
    return PALETTES.some((p) => p.id === v) ? (v as Palette) : "classic";
  } catch {
    return "classic";
  }
}

function applyPalette(p: Palette) {
  if (p === "classic") delete document.documentElement.dataset.palette;
  else document.documentElement.dataset.palette = p;
}

function applyTheme(theme: Theme) {
  const dark =
    theme === "dark" ||
    (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.classList.toggle("dark", dark);
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(
    () => (localStorage.getItem("ar-theme") as Theme) ?? "system"
  );
  const [palette, setPaletteState] = useState<Palette>(storedPalette);
  useEffect(() => applyPalette(palette), [palette]);
  const setPalette = (p: Palette) => {
    try { localStorage.setItem(PALETTE_KEY, p); } catch { /* private window: this visit only */ }
    setPaletteState(p);
  };

  useEffect(() => {
    applyTheme(theme);

    if (theme === "system") {
      const mq = window.matchMedia("(prefers-color-scheme: dark)");
      const handler = () => applyTheme("system");
      mq.addEventListener("change", handler);
      return () => mq.removeEventListener("change", handler);
    }
  }, [theme]);

  const setTheme = (t: Theme) => {
    localStorage.setItem("ar-theme", t);
    setThemeState(t);
  };

  return (
    <ThemeContext.Provider value={{ theme, setTheme, palette, setPalette }}>
      {children}
    </ThemeContext.Provider>
  );
}

export const useTheme = () => useContext(ThemeContext);
