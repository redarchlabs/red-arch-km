/**
 * BrowserSession — rides the user's live Clerk session.
 *
 * The whole auth model: we drive a *persistent* Playwright Chromium profile
 * pointed at the running KM2 web app. The user signs in once, in that window,
 * through the app's normal Clerk flow (SSO/MFA and all). The session persists
 * in the profile dir, so it survives restarts.
 *
 * For every API call we then harvest a fresh token via
 * `window.Clerk.session.getToken()` — the exact path the app uses
 * (ui/src/lib/auth/clerk.ts) — and read the active org from the same
 * localStorage key the app writes. Clerk.js owns token refresh/rotation, so we
 * never hand-roll any of that. No secrets are stored by this process; the only
 * sensitive artifact is the profile dir (session cookies), created 0700.
 */
import fs from "node:fs";
import path from "node:path";
import { chromium, type BrowserContext, type Page } from "playwright";
import type { Config } from "./config.js";
import { logger } from "./logger.js";

export interface SessionInfo {
  loaded: boolean;
  hasSession: boolean;
  userId: string | null;
  username: string | null;
  email: string | null;
}

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export class BrowserSession {
  private readonly cfg: Config;
  private context: BrowserContext | null = null;
  private page: Page | null = null;
  private launching: Promise<void> | null = null;

  constructor(cfg: Config) {
    this.cfg = cfg;
  }

  private appOrigin(): string {
    return new URL(this.cfg.appUrl).origin;
  }

  private onAppOrigin(): boolean {
    const p = this.page;
    if (!p) return false;
    try {
      return new URL(p.url()).origin === this.appOrigin();
    } catch {
      return false;
    }
  }

  /** Lazily launch the persistent browser context and keep one page open. */
  private async ensureBrowser(): Promise<Page> {
    if (this.page && !this.page.isClosed()) return this.page;
    if (!this.launching) {
      this.launching = this.launch().finally(() => {
        this.launching = null;
      });
    }
    await this.launching;
    if (!this.page) throw new Error("Browser failed to launch");
    return this.page;
  }

  private async launch(): Promise<void> {
    fs.mkdirSync(this.cfg.userDataDir, { recursive: true, mode: 0o700 });
    logger.info(
      `Launching ${this.cfg.browserChannel ?? "chromium"} profile at ${this.cfg.userDataDir} ` +
        `(headless=${this.cfg.headless})`,
    );
    this.context = await chromium.launchPersistentContext(this.cfg.userDataDir, {
      headless: this.cfg.headless,
      channel: this.cfg.browserChannel,
      viewport: this.cfg.headless ? { width: 1280, height: 900 } : null,
      // Strip the automation fingerprint so IdP bot-detection (GoDaddy/M365)
      // doesn't reject sign-in as an "unusual browser".
      ignoreDefaultArgs: ["--enable-automation"],
      args: ["--no-first-run", "--no-default-browser-check", "--disable-blink-features=AutomationControlled"],
    });
    // Belt-and-suspenders: mask navigator.webdriver on every navigation.
    await this.context.addInitScript(() => {
      Object.defineProperty(navigator, "webdriver", { get: () => undefined });
    });
    // Reuse the auto-opened page, or make one.
    this.page = this.context.pages()[0] ?? (await this.context.newPage());
  }

  /** Ensure the harvesting page is on the app origin with Clerk loaded. */
  private async ensureAppLoaded(navigateIfElsewhere = true): Promise<Page> {
    const page = await this.ensureBrowser();
    if (!this.onAppOrigin()) {
      if (!navigateIfElsewhere) return page;
      await page.goto(`${this.cfg.appUrl}/`, { waitUntil: "domcontentloaded" });
    }
    // Clerk.js sets `Clerk.loaded = true` once it has booted.
    await page
      .waitForFunction(() => Boolean((globalThis as unknown as { Clerk?: { loaded?: boolean } }).Clerk?.loaded), {
        timeout: 15_000,
      })
      .catch(() => {
        /* Clerk may still be initializing; callers handle null session. */
      });
    return page;
  }

  /** Read Clerk's current session/user without disturbing an in-progress login. */
  async status(): Promise<SessionInfo> {
    const page = await this.ensureAppLoaded();
    return this.readSession(page);
  }

  private async readSession(page: Page): Promise<SessionInfo> {
    return page.evaluate(() => {
      const clerk = (globalThis as unknown as { Clerk?: any }).Clerk;
      if (!clerk) return { loaded: false, hasSession: false, userId: null, username: null, email: null };
      const u = clerk.user;
      const email =
        u?.primaryEmailAddress?.emailAddress ?? u?.emailAddresses?.[0]?.emailAddress ?? null;
      return {
        loaded: Boolean(clerk.loaded),
        hasSession: Boolean(clerk.session),
        userId: u?.id ?? null,
        username: u?.username ?? null,
        email,
      };
    });
  }

  /**
   * Mint a fresh Clerk JWT the same way the app does. Returns null when there
   * is no active session (caller should surface NotAuthenticatedError).
   */
  async getToken(): Promise<string | null> {
    const page = await this.ensureAppLoaded();
    const template = this.cfg.clerkJwtTemplate ?? null;
    const harvest = () =>
      page.evaluate(async (tpl: string | null) => {
        const clerk = (globalThis as unknown as { Clerk?: any }).Clerk;
        if (!clerk?.session) return null;
        try {
          return (await clerk.session.getToken(tpl ? { template: tpl } : undefined)) ?? null;
        } catch {
          return null;
        }
      }, template);

    let token = await harvest();
    if (!token) {
      // One reload in case Clerk hadn't finished booting on this page.
      await page.reload({ waitUntil: "domcontentloaded" }).catch(() => {});
      await page
        .waitForFunction(() => Boolean((globalThis as unknown as { Clerk?: { loaded?: boolean } }).Clerk?.loaded), {
          timeout: 10_000,
        })
        .catch(() => {});
      token = await harvest();
    }
    return token;
  }

  /** The active org id: env override wins, else the app's localStorage value. */
  async getOrgId(): Promise<string | null> {
    if (this.cfg.orgIdOverride) return this.cfg.orgIdOverride;
    const page = await this.ensureAppLoaded();
    const key = this.cfg.orgStorageKey;
    const orgId = await page.evaluate((k: string) => {
      try {
        return (globalThis as unknown as { localStorage?: Storage }).localStorage?.getItem(k) ?? null;
      } catch {
        return null;
      }
    }, key);
    return orgId && orgId.trim() ? orgId : null;
  }

  /** Persist a new active org into the app's localStorage (keeps app + MCP in sync). */
  async setOrgId(orgId: string): Promise<void> {
    const page = await this.ensureAppLoaded();
    const key = this.cfg.orgStorageKey;
    await page.evaluate(
      ({ k, v }: { k: string; v: string }) => {
        (globalThis as unknown as { localStorage?: Storage }).localStorage?.setItem(k, v);
      },
      { k: key, v: orgId },
    );
  }

  /**
   * Open the browser and wait (up to loginTimeoutMs) for the user to finish
   * signing in. If a persisted session already exists this returns immediately.
   * Tolerates the page being on an SSO origin mid-flow.
   */
  async login(): Promise<SessionInfo> {
    const page = await this.ensureBrowser();

    // Kick off on the app so Clerk boots; if already elsewhere (mid-SSO) leave it.
    if (!this.onAppOrigin()) {
      await page.goto(`${this.cfg.appUrl}/`, { waitUntil: "domcontentloaded" }).catch(() => {});
    }

    const deadline = Date.now() + this.cfg.loginTimeoutMs;
    logger.info("Waiting for Clerk sign-in to complete in the browser window…");
    // eslint-disable-next-line no-constant-condition
    while (true) {
      if (this.onAppOrigin()) {
        const info = await this.readSession(page).catch(() => null);
        if (info?.hasSession) {
          logger.info(`Signed in as ${info.username ?? info.email ?? info.userId ?? "unknown"}`);
          return info;
        }
      }
      if (Date.now() >= deadline) {
        // Return whatever we can see so the caller can explain the timeout.
        const info = this.onAppOrigin() ? await this.readSession(page).catch(() => null) : null;
        return info ?? { loaded: false, hasSession: false, userId: null, username: null, email: null };
      }
      await sleep(1500);
    }
  }

  async close(): Promise<void> {
    try {
      await this.context?.close();
    } catch (err) {
      logger.warn("Error closing browser context", err);
    } finally {
      this.context = null;
      this.page = null;
    }
  }
}
