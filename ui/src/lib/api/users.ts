import apiClient from "./client";

export interface CurrentUser {
  id: string;
  username: string;
  email: string;
  is_site_admin: boolean;
  orgs: Array<{ id: string; name: string; is_admin: boolean; home_view_id?: string | null }>;
}

export async function fetchMe(): Promise<CurrentUser> {
  const response = await apiClient.get<CurrentUser>("/users/me");
  return response.data;
}

export interface ProfileUpdateInput {
  description?: string | null;
}

export interface UserProfile {
  id: string;
  username: string;
  email: string;
  description: string | null;
  is_site_admin: boolean;
  is_active: boolean;
}

export async function updateMe(input: ProfileUpdateInput): Promise<UserProfile> {
  const response = await apiClient.patch<UserProfile>("/users/me", input);
  return response.data;
}
