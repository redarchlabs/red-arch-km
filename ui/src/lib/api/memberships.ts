import apiClient from "./client";

export interface DimensionRef {
  id: string;
  name: string;
}

export interface Membership {
  id: string;
  profile_id: string;
  org_id: string;
  is_org_admin: boolean;
  regions: DimensionRef[];
  departments: DimensionRef[];
  roles: DimensionRef[];
  groups: DimensionRef[];
}

export interface MembershipInput {
  profile_id: string;
  is_org_admin: boolean;
  region_ids: string[];
  department_ids: string[];
  role_ids: string[];
  group_ids: string[];
}

/**
 * Optional org override for site-admin use: the global console operates on a
 * chosen org rather than the ambient one in localStorage. The client
 * interceptor respects a per-request X-Org-ID (see client.ts).
 */
function orgHeaders(orgId?: string): { headers?: Record<string, string> } {
  return orgId ? { headers: { "X-Org-ID": orgId } } : {};
}

export async function getMembershipForUser(
  userId: string,
  orgId?: string,
): Promise<Membership | null> {
  const response = await apiClient.get<Membership | null>(
    `/memberships/by-user/${userId}`,
    orgHeaders(orgId),
  );
  return response.data;
}

export async function upsertMembership(
  input: MembershipInput,
  orgId?: string,
): Promise<Membership> {
  const response = await apiClient.post<Membership>("/memberships/", input, orgHeaders(orgId));
  return response.data;
}

export async function updateMembership(
  membershipId: string,
  input: Partial<Omit<MembershipInput, "profile_id">>,
  orgId?: string,
): Promise<Membership> {
  const response = await apiClient.patch<Membership>(
    `/memberships/${membershipId}`,
    input,
    orgHeaders(orgId),
  );
  return response.data;
}

export async function deleteMembership(membershipId: string, orgId?: string): Promise<void> {
  await apiClient.delete(`/memberships/${membershipId}`, orgHeaders(orgId));
}

export async function listUsersInOrg(
  orgId?: string,
): Promise<Array<{ id: string; username: string; email: string }>> {
  const response = await apiClient.get<{
    items: Array<{ id: string; username: string; email: string }>;
  }>("/users/", { params: { page_size: 200 }, ...orgHeaders(orgId) });
  return response.data.items;
}
