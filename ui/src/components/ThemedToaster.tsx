"use client";

import { Toaster } from "sonner";

import { useTheme } from "@/context/ThemeContext";

/**
 * Wires sonner's Toaster to the app theme so toasts don't render in the default
 * light palette while the app is in dark/redarch mode. The custom "redarch"
 * theme maps to sonner's dark palette (it is a dark-based brand theme).
 */
export function ThemedToaster() {
  const { theme } = useTheme();
  return <Toaster richColors position="top-right" theme={theme === "light" ? "light" : "dark"} />;
}
