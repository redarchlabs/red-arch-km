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

export async function getMembershipForUser(userId: string): Promise<Membership | null> {
  const response = await apiClient.get<Membership | null>(
    `/memberships/by-user/${userId}`,
  );
  return response.data;
}

export async function upsertMembership(input: MembershipInput): Promise<Membership> {
  const response = await apiClient.post<Membership>("/memberships/", input);
  return response.data;
}

export async function updateMembership(
  membershipId: string,
  input: Partial<Omit<MembershipInput, "profile_id">>,
): Promise<Membership> {
  const response = await apiClient.patch<Membership>(
    `/memberships/${membershipId}`,
    input,
  );
  return response.data;
}


export async function listUsersInOrg(): Promise<
  Array<{ id: string; username: string; email: string }>
> {
  const response = await apiClient.get<
    Array<{ id: string; username: string; email: string }>
  >("/users/");
  return response.data;
}
