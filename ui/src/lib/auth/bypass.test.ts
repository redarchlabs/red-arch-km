import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  BYPASS_USER,
  bypassHeaders,
  clearBypassSecret,
  getBypassSecret,
  isBypassEnabled,
  setBypassSecret,
} from "./bypass";

const SECRET_KEY = "redarch:bypassSecret";

describe("isBypassEnabled", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("is OFF when the flag is unset — the default every normal build ships", () => {
    vi.stubEnv("NEXT_PUBLIC_BYPASS_AUTH", undefined);
    expect(isBypassEnabled()).toBe(false);
  });

  it("is on only for the exact value 1", () => {
    vi.stubEnv("NEXT_PUBLIC_BYPASS_AUTH", "1");
    expect(isBypassEnabled()).toBe(true);
  });

  it.each(["0", "true", "yes", "", " 1"])(
    "does not enable on the loose truthy value %o",
    (value) => {
      // A flag that disables authentication must not be switchable by accident, so
      // anything other than "1" is off — including strings that read as true.
      vi.stubEnv("NEXT_PUBLIC_BYPASS_AUTH", value);
      expect(isBypassEnabled()).toBe(false);
    },
  );
});

describe("secret storage", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("round-trips the secret and clears it", () => {
    expect(getBypassSecret()).toBeNull();
    expect(setBypassSecret("s3cret")).toBe(true);
    expect(getBypassSecret()).toBe("s3cret");
    clearBypassSecret();
    expect(getBypassSecret()).toBeNull();
  });

  it("uses sessionStorage, not localStorage — the session must die with the tab", () => {
    setBypassSecret("s3cret");
    expect(window.sessionStorage.getItem(SECRET_KEY)).toBe("s3cret");
    expect(window.localStorage.getItem(SECRET_KEY)).toBeNull();
  });

  it("treats an empty stored value as absent", () => {
    window.sessionStorage.setItem(SECRET_KEY, "");
    expect(getBypassSecret()).toBeNull();
  });

  it("survives a storage accessor that throws", () => {
    // Private windows and blocked site data THROW on access rather than returning null.
    // An exception here would take down the auth facade every page depends on.
    //
    // Spy on Storage.PROTOTYPE, not on the sessionStorage instance: jsdom's Storage is a
    // Proxy with no own method properties, so an instance spy is never looked up and the
    // assertions below would pass without ever exercising the catch.
    const blocked = () => {
      throw new Error("blocked");
    };
    const spies = (["getItem", "setItem", "removeItem"] as const).map((method) =>
      vi.spyOn(Storage.prototype, method).mockImplementation(blocked),
    );

    try {
      expect(getBypassSecret()).toBeNull();
      expect(setBypassSecret("x")).toBe(false);
      expect(() => clearBypassSecret()).not.toThrow();
    } finally {
      spies.forEach((spy) => spy.mockRestore());
    }
  });
});

describe("bypassHeaders", () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it("is empty with no secret, so the request 401s into the sign-in redirect", () => {
    expect(bypassHeaders()).toEqual({});
  });

  it("sends the test-user pair once a secret is stored", () => {
    setBypassSecret("s3cret");
    expect(bypassHeaders()).toEqual({
      "X-Test-User": BYPASS_USER,
      "X-Test-Secret": "s3cret",
    });
  });

  it("authenticates as siteadmin — the identity that already has every org", () => {
    // Not cosmetic: provision_user_from_claims matches strictly on auth_subject
    // (e2e-<username>), so a different name creates a member-of-nothing user and the org
    // switcher reads "No organizations".
    expect(BYPASS_USER).toBe("siteadmin");
  });
});
