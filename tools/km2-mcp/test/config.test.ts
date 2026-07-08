import { test } from "node:test";
import assert from "node:assert/strict";
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

test("HEADLESS accepts truthy variants", () => {
  for (const v of ["1", "yes", "on", "TRUE"]) {
    withEnv({ KM2_HEADLESS: v }, () => assert.equal(loadConfig().headless, true));
  }
  for (const v of ["0", "no", "off", "false", ""]) {
    withEnv({ KM2_HEADLESS: v }, () => assert.equal(loadConfig().headless, false));
  }
});
