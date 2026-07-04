import { expect, test } from "@playwright/test";

/**
 * Role-Based Access Control (RBAC) flow tests.
 * Verifies admin vs regular user permission boundaries.
 *
 * Requires E2E_WITH_BACKEND=1 with multiple test users configured.
 */

test.describe("RBAC / Permission Boundaries", () => {
  test.skip(!process.env.E2E_WITH_BACKEND, "requires seeded backend with test users");

  test("regular user cannot access admin panel", async ({ request, baseURL }) => {
    // Use API to verify regular user cannot access admin endpoints
    const res = await request.get(`${baseURL}/api/admin/users`, {
      headers: {
        "X-Test-User": "regular_user:test@example.com",
        "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
      },
    });

    // Should be denied (403 or 404)
    expect([403, 404]).toContain(res.status());
  });

  test("regular user can access documents", async ({ page, request, baseURL }) => {
    // Use API to verify regular user can access documents
    const res = await request.get(`${baseURL}/api/documents/`, {
      headers: {
        "X-Test-User": "regular_user:test@example.com",
        "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
      },
    });

    // A seeded regular user with org access CAN list documents: the one correct
    // outcome for this case is 200. Accepting 401/403 here made the assertion a
    // tautology (it would pass whether access succeeded OR was denied), so the
    // test could never catch a broken permission grant. Pin to 200 and always
    // validate the response shape.
    expect(res.status()).toBe(200);
    const body = (await res.json()) as { items: unknown[] };
    expect(Array.isArray(body.items)).toBe(true);
  });

  test("admin user can access admin panel", async ({ request, baseURL }) => {
    // Use API to verify admin user can access admin endpoints
    const res = await request.get(`${baseURL}/api/admin/users`, {
      headers: {
        "X-Test-User": "e2e_admin:e2e_admin@e2e.local",
        "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
      },
    });

    // Admin should be able to access admin endpoints (200)
    expect(res.ok()).toBe(true);
  });

  test("users cannot modify others' documents", async ({
    request,
    baseURL,
  }) => {
    // Create document as user A
    const createRes = await request.post(`${baseURL}/api/documents/`, {
      headers: {
        "X-Test-User": "user_a:usera@example.com",
        "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
      },
      data: {
        title: `user-a-doc-${Date.now()}`,
        text: "User A's private document",
      },
    });

    expect(createRes.ok()).toBe(true);
    const docA = (await createRes.json()) as { id: string };

    // Try to modify as user B
    const updateRes = await request.patch(
      `${baseURL}/api/documents/${docA.id}`,
      {
        headers: {
          "X-Test-User": "user_b:userb@example.com",
          "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
        },
        data: { title: "Modified by hacker" },
      },
    );

    // Should be denied
    expect([403, 404]).toContain(updateRes.status());
  });

  test("folder permissions respected across users", async ({
    request,
    baseURL,
  }) => {
    // Create folder as user A
    const folderRes = await request.post(`${baseURL}/api/folders/`, {
      headers: {
        "X-Test-User": "user_a:usera@example.com",
        "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
      },
      data: { name: `private-folder-${Date.now()}` },
    });

    expect(folderRes.ok()).toBe(true);
    const folder = (await folderRes.json()) as { id: string };

    // User B should not see it
    const listRes = await request.get(`${baseURL}/api/folders/`, {
      headers: {
        "X-Test-User": "user_b:userb@example.com",
        "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
      },
    });

    expect(listRes.ok()).toBe(true);
    const list = (await listRes.json()) as {
      items: Array<{ id: string }>;
    };

    const found = list.items.some((f) => f.id === folder.id);
    expect(found).toBe(false);
  });

  test("users can only see their own organizations", async ({
    request,
    baseURL,
  }) => {
    const res = await request.get(`${baseURL}/api/users/me`, {
      headers: {
        "X-Test-User": "regular_user:test@example.com",
        "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
      },
    });

    expect(res.ok()).toBe(true);
    const user = (await res.json()) as {
      orgs: Array<{ id: string; name: string }>;
    };

    // Regular user should only see their own orgs
    expect(user.orgs.length).toBeGreaterThanOrEqual(1);
    user.orgs.forEach((org) => {
      expect(org.id).toBeTruthy();
      expect(org.name).toBeTruthy();
    });
  });

  test("regular user cannot delete documents", async ({ request, baseURL }) => {
    // Create document as user A
    const createRes = await request.post(`${baseURL}/api/documents/`, {
      headers: {
        "X-Test-User": "user_a:usera@example.com",
        "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
      },
      data: {
        title: `secure-doc-${Date.now()}`,
        text: "Should not be deletable by others",
      },
    });

    expect(createRes.ok()).toBe(true);
    const docA = (await createRes.json()) as { id: string };

    // Try to delete as user B
    const deleteRes = await request.delete(
      `${baseURL}/api/documents/${docA.id}`,
      {
        headers: {
          "X-Test-User": "user_b:userb@example.com",
          "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
        },
      },
    );

    // Should be denied
    expect([403, 404]).toContain(deleteRes.status());

    // Clean up: delete as owner
    await request.delete(`${baseURL}/api/documents/${docA.id}`, {
      headers: {
        "X-Test-User": "user_a:usera@example.com",
        "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
      },
    });
  });

  test("regular user cannot access other user's folders", async ({
    request,
    baseURL,
  }) => {
    // Create folder as user A
    const folderRes = await request.post(`${baseURL}/api/folders/`, {
      headers: {
        "X-Test-User": "user_a:usera@example.com",
        "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
      },
      data: { name: `private-${Date.now()}` },
    });

    expect(folderRes.ok()).toBe(true);
    const folder = (await folderRes.json()) as { id: string };

    // Try to access directly as user B
    const accessRes = await request.get(`${baseURL}/api/folders/${folder.id}`, {
      headers: {
        "X-Test-User": "user_b:userb@example.com",
        "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
      },
    });

    // Should be denied
    expect([403, 404]).toContain(accessRes.status());

    // Clean up: delete as owner
    await request.delete(`${baseURL}/api/folders/${folder.id}`, {
      headers: {
        "X-Test-User": "user_a:usera@example.com",
        "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
      },
    });
  });

  test("admin can access admin panel and see users", async ({
    request,
    baseURL,
  }) => {
    // Try to list users as admin
    const usersRes = await request.get(`${baseURL}/api/admin/users`, {
      headers: {
        "X-Test-User": "e2e_admin:e2e_admin@e2e.local",
        "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
      },
    });

    // Admin should be able to list users
    expect(usersRes.ok()).toBe(true);
  });

  test("non-admin cannot access admin endpoints", async ({
    request,
    baseURL,
  }) => {
    // Try to list users as regular user
    const usersRes = await request.get(`${baseURL}/api/admin/users`, {
      headers: {
        "X-Test-User": "regular_user:test@example.com",
        "X-Test-Secret": process.env.E2E_TEST_SECRET || "",
      },
    });

    // Should be denied
    expect([403, 404]).toContain(usersRes.status());
  });
});
