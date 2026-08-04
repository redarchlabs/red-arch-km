import { test } from "node:test";
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import { loadConfig } from "../src/config.js";

const KEYS = [
  "KM2_APP_URL",
  "KM2_API_URL",
  "KM2_CLERK_JWT_TEMPLATE",
  "KM2_ORG_STORAGE_KEY",
  "KM2_ORG_ID",
  "KM2_USER_DATA_DIR",
  "KM2_HEADLESS",
  "KM2_LOGIN_TIMEOUT_MS",
];

function withEnv(env: Record<string, string | undefined>, fn: () => void): void {
  const saved = new Map<string, string | undefined>();
  for (const k of KEYS) saved.set(k, process.env[k]);
  for (const k of KEYS) delete process.env[k];
  for (const [k, v] of Object.entries(env)) if (v !== undefined) process.env[k] = v;
  try {
    fn();
  } finally {
    for (const k of KEYS) {
      const v = saved.get(k);
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  }
}

test("defaults are dev-friendly and secret-free", () => {
  withEnv({}, () => {
    const cfg = loadConfig();
    assert.equal(cfg.appUrl, "http://localhost:3000");
    assert.equal(cfg.apiUrl, "http://localhost:8000/api");
    assert.equal(cfg.orgStorageKey, "redarch:currentOrgId");
    assert.equal(cfg.clerkJwtTemplate, undefined);
    assert.equal(cfg.orgIdOverride, undefined);
    assert.equal(cfg.headless, false);
    assert.equal(cfg.loginTimeoutMs, 180_000);
  });
});

test("env overrides win and trailing slashes are trimmed", () => {
  withEnv(
    { KM2_APP_URL: "https://app.example.com/", KM2_API_URL: "https://api.example.com/api/", KM2_HEADLESS: "true", KM2_ORG_ID: "org-xyz" },
    () => {
      const cfg = loadConfig();
      assert.equal(cfg.appUrl, "https://app.example.com");
      assert.equal(cfg.apiUrl, "https://api.example.com/api");
      assert.equal(cfg.headless, true);
      assert.equal(cfg.orgIdOverride, "org-xyz");
    },
  );
});

test("USER_DATA_DIR expands a leading ~/ but leaves other paths alone", () => {
  const home = os.homedir();
  withEnv({ KM2_USER_DATA_DIR: "~/.km2-mcp/profile-x" }, () => {
    assert.equal(loadConfig().userDataDir, path.join(home, ".km2-mcp", "profile-x"));
  });
  withEnv({ KM2_USER_DATA_DIR: "~" }, () => {
    assert.equal(loadConfig().userDataDir, home);
  });
  // Absolute and relative paths are untouched, and a ~ that is not the whole
  // first segment is an ordinary filename character.
  for (const p of ["/var/tmp/km2", "./profile", "/tmp/a~b"]) {
    withEnv({ KM2_USER_DATA_DIR: p }, () => assert.equal(loadConfig().userDataDir, p));
  }
  // Unset still falls back to the documented default.
  withEnv({}, () => {
    assert.equal(loadConfig().userDataDir, path.join(home, ".km2-mcp", "profile"));
  });
});

test("HEADLESS accepts truthy variants", () => {
  for (const v of ["1", "yes", "on", "TRUE"]) {
    withEnv({ KM2_HEADLESS: v }, () => assert.equal(loadConfig().headless, true));
  }
  for (const v of ["0", "no", "off", "false", ""]) {
    withEnv({ KM2_HEADLESS: v }, () => assert.equal(loadConfig().headless, false));
  }
});
