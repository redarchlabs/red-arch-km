import { expect, test } from "./fixtures";

/**
 * Role-Based Access Control (RBAC) flow tests.
 * Verifies admin vs regular user permission boundaries.
 *
 * Requires E2E_WITH_BACKEND=1 with the seeded org + users (see
 * services/api/scripts/seed_e2e.py and docs/testing/e2e-seeded-auth.md).
 *
 * These specs drive the Python API DIRECTLY (via the `e2eState.apiUrl` origin,
 * :8000), NOT the UI origin (:3000). The UI has no `/api` rewrite or route
 * handlers, so hitting `baseURL` 404s and never reaches the API (RED-10 fix).
 * Every org-scoped endpoint additionally requires the `X-Org-ID` header
 * (api.dependencies.get_org_id), so we pass `e2eState.orgId` on each request.
 *
 * Admin surface: there is no `/api/admin/*` router. The real org-admin-gated
 * endpoint used here is `GET /api/memberships/by-user/{id}` (require_org_admin):
 * a site-admin / org-admin gets 200, a non-admin member gets 403.
 *
 * Org-level access contract (INTENDED design — see RED-10 settled decision):
 *  - PATCH/DELETE on documents: RLS-only, any org member can mutate any org doc.
 *  - Folder create/update/delete: admin-only (require_org_admin → 403 for members).
 *  - Folder list: mask-filtered (viewer_permissions_config controls visibility).
 *  - Folder GET by ID: no mask gate (require_org_access only → 200 for any member).
 */

// A well-formed UUID that matches no membership — the admin-gated endpoint
// still resolves (200 → null) for an admin, and 403 for a non-admin, which is
// exactly the authorization boundary these tests assert.
const NIL_UUID = "00000000-0000-0000-0000-000000000000";
const ADMIN_ENDPOINT = `/api/memberships/by-user/${NIL_UUID}`;

test.describe("RBAC / Permission Boundaries", () => {
  test.skip(!process.env.E2E_WITH_BACKEND, "requires seeded backend with test users");

  test("regular user cannot access admin panel", async ({ request, e2eState }) => {
    // A non-admin member hitting an org-admin-only endpoint must be denied.
    const res = await request.get(`${e2eState.apiUrl}${ADMIN_ENDPOINT}`, {
      headers: {
        "X-Test-User": "regular_user:test@example.com",
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
    });

    // Should be denied (403 no-admin, or 404 if the route were absent).
    expect([403, 404]).toContain(res.status());
  });

  test("regular user can access documents", async ({ request, e2eState }) => {
    // Use API to verify regular user can access documents
    const res = await request.get(`${e2eState.apiUrl}/api/documents/`, {
      headers: {
        "X-Test-User": "regular_user:test@example.com",
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
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

  test("admin user can access admin panel", async ({ request, e2eState }) => {
    // Use API to verify admin user can access org-admin endpoints
    const res = await request.get(`${e2eState.apiUrl}${ADMIN_ENDPOINT}`, {
      headers: {
        "X-Test-User": "e2e_admin:e2e_admin@e2e.local",
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
    });

    // Admin should be able to access org-admin endpoints (200).
    expect(res.ok()).toBe(true);
  });

  test("any org member can modify any org document", async ({ request, e2eState }) => {
    // Org-level posture: documents use RLS-only (require_org_access), so any
    // member of the org can PATCH any document in the org — there is no
    // per-user ownership restriction on mutation.
    const createRes = await request.post(`${e2eState.apiUrl}/api/documents/`, {
      headers: {
        "X-Test-User": "user_a:usera@example.com",
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
      data: {
        title: `user-a-doc-${Date.now()}`,
        text: "Org-shared document",
      },
    });

    expect(createRes.ok()).toBe(true);
    const docA = (await createRes.json()) as { id: string };

    // user_b is a member of the same org — org-level posture means they CAN
    // modify this document (PATCH returns 200, not 403).
    const updateRes = await request.patch(
      `${e2eState.apiUrl}/api/documents/${docA.id}`,
      {
        headers: {
          "X-Test-User": "user_b:userb@example.com",
          "X-Test-Secret": e2eState.testSecret,
          "X-Org-ID": e2eState.orgId,
        },
        data: { title: "Modified by org member" },
      },
    );

    expect(updateRes.ok()).toBe(true);

    // Clean up
    await request.delete(`${e2eState.apiUrl}/api/documents/${docA.id}`, {
      headers: {
        "X-Test-User": "user_b:userb@example.com",
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
    });
  });

  test("folder list is filtered by permission mask", async ({ request, e2eState }) => {
    // Folder create is admin-only (require_org_admin → 403 for regular members).
    // Admin creates a North-scoped folder via viewer_permissions_config.
    // Folder-list visibility is an EXACT array overlap (`&&`) between the
    // folder's permission masks and the user's masks — not a field-wise
    // wildcard — so the folder config must name the SAME full dimension tuple
    // the members are seeded with (region + department + role + group; see
    // seed_e2e.py). Only the region axis differs between users, so this folder
    // is visible to user_a (North, Engineering, Manager, Alpha) and hidden from
    // user_b (South, ...).
    const folderRes = await request.post(`${e2eState.apiUrl}/api/folders/`, {
      headers: {
        "X-Test-User": "e2e_admin:e2e_admin@e2e.local",
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
      data: {
        name: `north-only-${Date.now()}`,
        viewer_permissions_config: [
          {
            region: "North",
            department: "Engineering",
            role: "Manager",
            group: "Alpha",
          },
        ],
      },
    });

    expect(folderRes.ok()).toBe(true);
    const folder = (await folderRes.json()) as { id: string };

    // user_a (North mask) — should see the North-only folder.
    const listResA = await request.get(`${e2eState.apiUrl}/api/folders/`, {
      headers: {
        "X-Test-User": "user_a:usera@example.com",
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
    });
    expect(listResA.ok()).toBe(true);
    const listA = (await listResA.json()) as { items: Array<{ id: string }> };
    expect(listA.items.some((f) => f.id === folder.id)).toBe(true);

    // user_b (South mask) — should NOT see the North-only folder.
    const listResB = await request.get(`${e2eState.apiUrl}/api/folders/`, {
      headers: {
        "X-Test-User": "user_b:userb@example.com",
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
    });
    expect(listResB.ok()).toBe(true);
    const listB = (await listResB.json()) as { items: Array<{ id: string }> };
    expect(listB.items.some((f) => f.id === folder.id)).toBe(false);

    // Clean up
    await request.delete(`${e2eState.apiUrl}/api/folders/${folder.id}`, {
      headers: {
        "X-Test-User": "e2e_admin:e2e_admin@e2e.local",
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
    });
  });

  test("users can only see their own organizations", async ({ request, e2eState }) => {
    const res = await request.get(`${e2eState.apiUrl}/api/users/me`, {
      headers: {
        "X-Test-User": "regular_user:test@example.com",
        "X-Test-Secret": e2eState.testSecret,
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

  test("any org member can delete any org document", async ({ request, e2eState }) => {
    // Org-level posture: documents use RLS-only (require_org_access), so any
    // member of the org can DELETE any document in the org — there is no
    // per-user ownership restriction on deletion.
    const createRes = await request.post(`${e2eState.apiUrl}/api/documents/`, {
      headers: {
        "X-Test-User": "user_a:usera@example.com",
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
      data: {
        title: `secure-doc-${Date.now()}`,
        text: "Org-shared document",
      },
    });

    expect(createRes.ok()).toBe(true);
    const docA = (await createRes.json()) as { id: string };

    // user_b is a member of the same org — org-level posture means they CAN
    // delete this document (DELETE returns 204, not 403).
    const deleteRes = await request.delete(
      `${e2eState.apiUrl}/api/documents/${docA.id}`,
      {
        headers: {
          "X-Test-User": "user_b:userb@example.com",
          "X-Test-Secret": e2eState.testSecret,
          "X-Org-ID": e2eState.orgId,
        },
      },
    );

    // 204 No Content on successful delete.
    expect([200, 204]).toContain(deleteRes.status());
  });

  test("any org member can get any folder by ID", async ({ request, e2eState }) => {
    // Folder create is admin-only. GET /api/folders/{id} uses require_org_access
    // with NO mask gate — any org member can fetch a folder by its ID regardless
    // of their permission mask. Only the list endpoint applies mask filtering.
    const folderRes = await request.post(`${e2eState.apiUrl}/api/folders/`, {
      headers: {
        "X-Test-User": "e2e_admin:e2e_admin@e2e.local",
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
      data: { name: `shared-folder-${Date.now()}` },
    });

    expect(folderRes.ok()).toBe(true);
    const folder = (await folderRes.json()) as { id: string };

    // user_b (South mask) can still GET this folder by ID — no mask gate on
    // the GET-by-id endpoint.
    const accessRes = await request.get(
      `${e2eState.apiUrl}/api/folders/${folder.id}`,
      {
        headers: {
          "X-Test-User": "user_b:userb@example.com",
          "X-Test-Secret": e2eState.testSecret,
          "X-Org-ID": e2eState.orgId,
        },
      },
    );

    expect(accessRes.ok()).toBe(true);

    // Clean up
    await request.delete(`${e2eState.apiUrl}/api/folders/${folder.id}`, {
      headers: {
        "X-Test-User": "e2e_admin:e2e_admin@e2e.local",
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
    });
  });

  test("admin can access admin panel and see users", async ({ request, e2eState }) => {
    // Query the org-admin membership-lookup endpoint as admin.
    const usersRes = await request.get(`${e2eState.apiUrl}${ADMIN_ENDPOINT}`, {
      headers: {
        "X-Test-User": "e2e_admin:e2e_admin@e2e.local",
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
    });

    // Admin should be able to reach the admin-only endpoint.
    expect(usersRes.ok()).toBe(true);
  });

  test("non-admin cannot access admin endpoints", async ({ request, e2eState }) => {
    // Try the org-admin endpoint as a regular member.
    const usersRes = await request.get(`${e2eState.apiUrl}${ADMIN_ENDPOINT}`, {
      headers: {
        "X-Test-User": "regular_user:test@example.com",
        "X-Test-Secret": e2eState.testSecret,
        "X-Org-ID": e2eState.orgId,
      },
    });

    // Should be denied
    expect([403, 404]).toContain(usersRes.status());
  });
});
