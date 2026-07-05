/**
 * Theme constants shared by ThemeContext (runtime) and the root layout's
 * pre-paint inline script.
 */

export const THEMES = ["light", "dark", "redarch"] as const;
export type Theme = (typeof THEMES)[number];

export const THEME_STORAGE_KEY = "redarch:theme";

export const THEME_LABELS: Record<Theme, string> = {
  light: "Light",
  dark: "Dark",
  redarch: "Red Arch",
};

export function isTheme(value: unknown): value is Theme {
  return typeof value === "string" && (THEMES as readonly string[]).includes(value);
}

/** Stored theme, falling back to the OS preference on first visit. */
export function resolveInitialTheme(
  stored: string | null,
  prefersDark: boolean,
): Theme {
  if (isTheme(stored)) {
    return stored;
  }
  return prefersDark ? "dark" : "light";
}
