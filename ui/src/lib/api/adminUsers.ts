/**
 * Site-admin console: global user management (/api/admin/*).
 */
import apiClient from "./client";

export interface AdminUser {
  id: string;
  username: string;
  email: string;
  description: string | null;
  is_site_admin: boolean;
  is_active: boolean;
}

export interface AdminUserPage {
  items: AdminUser[];
  total: number;
  page: number;
  page_size: number;
  pages: number;
}

export interface AdminUserUpdateInput {
  is_site_admin?: boolean;
  is_active?: boolean;
}

export interface UserMembershipSummary {
  membership_id: string;
  org_id: string;
  org_name: string;
  is_org_admin: boolean;
}

export async function listAllUsers(params: {
  page?: number;
  pageSize?: number;
  q?: string;
}): Promise<AdminUserPage> {
  const response = await apiClient.get<AdminUserPage>("/admin/users", {
    params: {
      page: params.page ?? 1,
      page_size: params.pageSize ?? 50,
      ...(params.q ? { q: params.q } : {}),
    },
  });
  return response.data;
}

export async function updateAdminUser(
  profileId: string,
  input: AdminUserUpdateInput,
): Promise<AdminUser> {
  const response = await apiClient.patch<AdminUser>(`/admin/users/${profileId}`, input);
  return response.data;
}

export async function fetchUserMemberships(
  profileId: string,
): Promise<UserMembershipSummary[]> {
  const response = await apiClient.get<UserMembershipSummary[]>(
    `/admin/users/${profileId}/memberships`,
  );
  return response.data;
}
