"use client";

import { ArrowLeft, Link2, Plug, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { getApiErrorMessage } from "@/lib/api/errors";
import {
  createMcpServer,
  deleteMcpServer,
  disconnectMcpOAuth,
  listMcpPresets,
  listMcpServers,
  startMcpOAuth,
  testMcpServer,
  type McpAuthType,
  type McpOAuthIdentity,
  type McpPreset,
  type McpServer,
  type McpTransport,
} from "@/lib/api/mcpServers";

const selectClass = "h-9 w-full rounded-md border bg-background px-2 text-sm";
const TRANSPORTS: McpTransport[] = ["http", "sse", "stdio"];
const AUTH_TYPES: McpAuthType[] = ["none", "bearer", "api_key", "oauth"];

interface FormState {
  name: string;
  transport: McpTransport;
  url: string;
  command: string;
  authType: McpAuthType;
  secret: string;
  oauthIdentity: McpOAuthIdentity;
  oauthScopes: string;
  oauthClientId: string;
  oauthClientSecret: string;
}

const EMPTY_FORM: FormState = {
  name: "",
  transport: "http",
  url: "",
  command: "",
  authType: "none",
  secret: "",
  oauthIdentity: "org",
  oauthScopes: "",
  oauthClientId: "",
  oauthClientSecret: "",
};

function oauthBadge(s: McpServer): { label: string; variant: "default" | "outline" } | null {
  if (s.auth_type !== "oauth") return null;
  if (s.oauth_status.connected) return { label: `connected (${s.oauth_identity})`, variant: "default" };
  return { label: `not connected (${s.oauth_identity})`, variant: "outline" };
}

export default function McpServersPage() {
  const [items, setItems] = useState<McpServer[]>([]);
  const [presets, setPresets] = useState<McpPreset[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [tested, setTested] = useState<Record<string, string>>({});
  const popupRef = useRef<Window | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const [servers, ps] = await Promise.all([listMcpServers(), listMcpPresets()]);
      setItems(servers);
      setPresets(ps);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load MCP servers"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Handle the popup return + the opener refresh after an OAuth "Connect".
  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const connected = params.get("connected");
    if (connected !== null) {
      if (window.opener && window.opener !== window) {
        window.opener.postMessage({ type: "mcp-oauth-done", ok: connected === "1" }, window.location.origin);
        window.close();
        return;
      }
      setNotice(connected === "1" ? "Connected." : "Authorization was cancelled or failed.");
      window.history.replaceState({}, "", "/agents/mcp-servers");
    }
    const onMessage = (e: MessageEvent) => {
      if (e.origin === window.location.origin && e.data?.type === "mcp-oauth-done") {
        popupRef.current?.close();
        void load();
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [load]);

  const applyPreset = (key: string) => {
    const p = presets.find((x) => x.key === key);
    if (!p) return;
    setForm((f) => ({
      ...f,
      name: f.name || p.key,
      url: p.url,
      transport: p.transport as McpTransport,
      authType: p.auth_type as McpAuthType,
      oauthScopes: p.scopes ?? "",
    }));
  };

  const create = async () => {
    setSaving(true);
    setError(null);
    try {
      await createMcpServer({
        name: form.name,
        transport: form.transport,
        url: form.transport === "stdio" ? null : form.url || null,
        command: form.transport === "stdio" ? form.command || null : null,
        auth_type: form.authType,
        secret: form.authType === "bearer" || form.authType === "api_key" ? form.secret || null : null,
        oauth_identity: form.oauthIdentity,
        oauth_scopes: form.authType === "oauth" ? form.oauthScopes || null : null,
        oauth_client_id: form.authType === "oauth" ? form.oauthClientId || null : null,
        oauth_client_secret: form.authType === "oauth" ? form.oauthClientSecret || null : null,
      });
      setCreating(false);
      setForm(EMPTY_FORM);
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to create MCP server"));
    } finally {
      setSaving(false);
    }
  };

  const connect = async (server: McpServer) => {
    setError(null);
    try {
      const { authorization_url } = await startMcpOAuth(server.id);
      popupRef.current = window.open(authorization_url, "mcp-oauth", "width=640,height=760");
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to start authorization"));
    }
  };

  const disconnect = async (server: McpServer) => {
    await disconnectMcpOAuth(server.id);
    await load();
  };

  const test = async (server: McpServer) => {
    setTested((t) => ({ ...t, [server.id]: "testing…" }));
    try {
      const tools = await testMcpServer(server.id);
      setTested((t) => ({ ...t, [server.id]: `${tools.length} tools: ${tools.map((x) => x.name).join(", ")}` }));
    } catch (e: unknown) {
      setTested((t) => ({ ...t, [server.id]: getApiErrorMessage(e, "unreachable") }));
    }
  };

  const remove = async (server: McpServer) => {
    if (!confirm(`Delete MCP server "${server.name}"?`)) return;
    await deleteMcpServer(server.id);
    await load();
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/agents" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div className="flex-1">
          <h1 className="flex items-center gap-2 text-2xl font-semibold">
            <Plug className="h-6 w-6" /> MCP servers
          </h1>
          <p className="text-sm text-muted-foreground">
            External MCP servers your agents can call tools on. OAuth servers (Linear, Jira, GitHub…) use a
            browser sign-in; static-secret servers store an encrypted token.
          </p>
        </div>
        <Button size="sm" onClick={() => setCreating(true)}>
          <Plus className="h-4 w-4" /> Add server
        </Button>
      </div>

      {notice ? <p className="text-sm text-green-600">{notice}</p> : null}
      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No MCP servers configured.</p>
      ) : (
        <div className="space-y-2">
          {items.map((s) => {
            const badge = oauthBadge(s);
            return (
              <Card key={s.id}>
                <CardContent className="flex items-center gap-3 pt-6">
                  <div className="flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{s.name}</span>
                      <Badge variant="outline">{s.transport}</Badge>
                      <Badge variant="outline">{s.auth_type}</Badge>
                      {badge ? <Badge variant={badge.variant}>{badge.label}</Badge> : null}
                    </div>
                    <p className="text-xs text-muted-foreground">{s.url ?? s.command}</p>
                    {tested[s.id] ? <p className="mt-1 text-xs">{tested[s.id]}</p> : null}
                  </div>
                  {s.auth_type === "oauth" ? (
                    s.oauth_status.connected ? (
                      <Button size="sm" variant="outline" onClick={() => disconnect(s)}>
                        Disconnect
                      </Button>
                    ) : (
                      <Button size="sm" onClick={() => connect(s)}>
                        <Link2 className="h-4 w-4" /> Connect
                      </Button>
                    )
                  ) : null}
                  <Button size="sm" variant="outline" onClick={() => test(s)}>
                    Test
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => remove(s)}>
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}

      {creating ? (
        <Dialog open onClose={() => setCreating(false)}>
          <DialogHeader>
            <DialogTitle>Add MCP server</DialogTitle>
          </DialogHeader>
          <div className="max-h-[65vh] space-y-3 overflow-y-auto px-1 py-2">
            {presets.length > 0 ? (
              <label className="block text-sm">
                Start from a template
                <select className={selectClass} defaultValue="" onChange={(e) => applyPreset(e.target.value)}>
                  <option value="">— none —</option>
                  {presets.map((p) => (
                    <option key={p.key} value={p.key}>
                      {p.label}
                      {p.supports_dcr ? "" : " (needs client id/secret)"}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}

            <label className="block text-sm">
              Name
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="linear" />
            </label>

            <div className="grid grid-cols-2 gap-3">
              <label className="block text-sm">
                Transport
                <select
                  className={selectClass}
                  value={form.transport}
                  onChange={(e) => setForm({ ...form, transport: e.target.value as McpTransport })}
                >
                  {TRANSPORTS.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </label>
              <label className="block text-sm">
                Auth
                <select
                  className={selectClass}
                  value={form.authType}
                  onChange={(e) => setForm({ ...form, authType: e.target.value as McpAuthType })}
                >
                  {AUTH_TYPES.map((a) => (
                    <option key={a} value={a}>
                      {a}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {form.transport === "stdio" ? (
              <label className="block text-sm">
                Command
                <Input value={form.command} onChange={(e) => setForm({ ...form, command: e.target.value })} placeholder="npx -y @modelcontextprotocol/server-…" />
              </label>
            ) : (
              <label className="block text-sm">
                URL
                <Input value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://mcp.example.com/sse" />
              </label>
            )}

            {form.authType === "bearer" || form.authType === "api_key" ? (
              <label className="block text-sm">
                Secret
                <Input type="password" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} />
              </label>
            ) : null}

            {form.authType === "oauth" ? (
              <fieldset className="space-y-2 rounded-md border p-2 text-sm">
                <legend className="px-1 text-xs text-muted-foreground">OAuth</legend>
                <label className="block">
                  Identity
                  <select
                    className={selectClass}
                    value={form.oauthIdentity}
                    onChange={(e) => setForm({ ...form, oauthIdentity: e.target.value as McpOAuthIdentity })}
                  >
                    <option value="org">Per organization (one shared sign-in)</option>
                    <option value="user">Per user (each member connects their own)</option>
                  </select>
                </label>
                <label className="block">
                  Scopes (optional, space-separated)
                  <Input value={form.oauthScopes} onChange={(e) => setForm({ ...form, oauthScopes: e.target.value })} />
                </label>
                <p className="text-xs text-muted-foreground">
                  Leave client id/secret blank if the server supports dynamic registration. Providers like GitHub
                  need a pre-registered OAuth app — paste its client id/secret below.
                </p>
                <label className="block">
                  Client ID (optional)
                  <Input value={form.oauthClientId} onChange={(e) => setForm({ ...form, oauthClientId: e.target.value })} />
                </label>
                <label className="block">
                  Client secret (optional)
                  <Input type="password" value={form.oauthClientSecret} onChange={(e) => setForm({ ...form, oauthClientSecret: e.target.value })} />
                </label>
              </fieldset>
            ) : null}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreating(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={create} disabled={saving || !form.name}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </Dialog>
      ) : null}
    </div>
  );
}
