"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import {
  resolveInitialTheme,
  THEME_STORAGE_KEY,
  type Theme,
} from "@/lib/theme";

interface ThemeState {
  theme: Theme;
  setTheme: (theme: Theme) => void;
}

const ThemeContext = createContext<ThemeState | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  // The pre-paint script in the root layout already stamped data-theme, so
  // initialize from the DOM to stay consistent with what's on screen.
  const [theme, setThemeState] = useState<Theme>(() => {
    if (typeof document !== "undefined") {
      return resolveInitialTheme(
        document.documentElement.dataset.theme ?? null,
        window.matchMedia("(prefers-color-scheme: dark)").matches,
      );
    }
    return "light";
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      // Storage unavailable (private mode) — theme still applies this session.
    }
  }, []);

  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeState {
  const ctx = useContext(ThemeContext);
  if (ctx === null) {
    throw new Error("useTheme must be used within a ThemeProvider");
  }
  return ctx;
}
