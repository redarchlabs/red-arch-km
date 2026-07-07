/**
 * Site-admin console: sent-email inspection backed by the Mailpit container
 * (/api/admin/emails). Mailpit is a dev/staging tool, so `available` is false
 * when it isn't running — the console renders a "not running" state, not an error.
 */
import apiClient from "./client";

export interface EmailAddress {
  name: string | null;
  address: string;
}

export interface SentEmailSummary {
  id: string;
  from_addr: EmailAddress | null;
  to: EmailAddress[];
  subject: string;
  created: string | null;
  size: number | null;
  attachments: number;
  snippet: string | null;
}

export interface SentEmailList {
  available: boolean;
  total: number;
  messages: SentEmailSummary[];
  detail: string | null;
}

export interface SentEmailDetail {
  id: string;
  from_addr: EmailAddress | null;
  to: EmailAddress[];
  cc: EmailAddress[];
  bcc: EmailAddress[];
  subject: string;
  date: string | null;
  text: string | null;
  html: string | null;
  attachments: string[];
}

/** Page of captured messages (newest first). */
export async function fetchSentEmails(start = 0, limit = 50): Promise<SentEmailList> {
  const response = await apiClient.get<SentEmailList>("/admin/emails", {
    params: { start, limit },
  });
  return response.data;
}

/** One captured message's full headers + body, by Mailpit message id. */
export async function fetchSentEmail(id: string): Promise<SentEmailDetail> {
  const response = await apiClient.get<SentEmailDetail>(`/admin/emails/${id}`);
  return response.data;
}
