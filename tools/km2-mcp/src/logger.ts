/**
 * Minimal logger.
 *
 * CRITICAL: an MCP stdio server speaks JSON-RPC over **stdout**. Writing
 * anything else to stdout corrupts the protocol stream. So all diagnostics go
 * to **stderr** only. Never console.log() in this process.
 */
type Level = "debug" | "info" | "warn" | "error";

const LEVELS: Record<Level, number> = { debug: 10, info: 20, warn: 30, error: 40 };
const threshold = LEVELS[(process.env.KM2_LOG_LEVEL as Level) ?? "info"] ?? LEVELS.info;

function emit(level: Level, msg: string, extra?: unknown): void {
  if (LEVELS[level] < threshold) return;
  const line = `[km2-mcp] ${level.toUpperCase()} ${msg}`;
  if (extra !== undefined) {
    process.stderr.write(`${line} ${safe(extra)}\n`);
  } else {
    process.stderr.write(`${line}\n`);
  }
}

function safe(value: unknown): string {
  try {
    return typeof value === "string" ? value : JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export const logger = {
  debug: (msg: string, extra?: unknown) => emit("debug", msg, extra),
  info: (msg: string, extra?: unknown) => emit("info", msg, extra),
  warn: (msg: string, extra?: unknown) => emit("warn", msg, extra),
  error: (msg: string, extra?: unknown) => emit("error", msg, extra),
};
