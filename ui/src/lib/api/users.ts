import apiClient from "./client";

export interface CurrentUser {
  id: string;
  username: string;
  email: string;
  is_site_admin: boolean;
  orgs: Array<{ id: string; name: string }>;
}

export async function fetchMe(): Promise<CurrentUser> {
  const response = await apiClient.get<CurrentUser>("/users/me");
  return response.data;
}
