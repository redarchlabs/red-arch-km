"use client";

import { ArrowLeft, Plug, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

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
  listMcpServers,
  testMcpServer,
  type McpServer,
  type McpTransport,
} from "@/lib/api/mcpServers";

const selectClass = "h-9 w-full rounded-md border bg-background px-2 text-sm";
const TRANSPORTS: McpTransport[] = ["http", "sse", "stdio"];

export default function McpServersPage() {
  const [items, setItems] = useState<McpServer[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [tested, setTested] = useState<Record<string, string>>({});

  const [name, setName] = useState("");
  const [transport, setTransport] = useState<McpTransport>("http");
  const [url, setUrl] = useState("");
  const [command, setCommand] = useState("");
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setItems(await listMcpServers());
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load MCP servers"));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const create = async () => {
    setSaving(true);
    try {
      await createMcpServer({
        name,
        transport,
        url: transport === "stdio" ? null : url || null,
        command: transport === "stdio" ? command || null : null,
        secret: secret || null,
      });
      setCreating(false);
      setName("");
      setUrl("");
      setCommand("");
      setSecret("");
      await load();
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to create MCP server"));
    } finally {
      setSaving(false);
    }
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
            External MCP servers your agents can call tools on. Secrets are encrypted and never shown again.
          </p>
        </div>
        <Button size="sm" onClick={() => setCreating(true)}>
          <Plus className="h-4 w-4" /> Add server
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      {isLoading ? (
        <Skeleton className="h-32 w-full" />
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No MCP servers configured.</p>
      ) : (
        <div className="space-y-2">
          {items.map((s) => (
            <Card key={s.id}>
              <CardContent className="flex items-center gap-3 pt-6">
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{s.name}</span>
                    <Badge variant="outline">{s.transport}</Badge>
                    {s.has_secret ? <Badge variant="outline">secret set</Badge> : null}
                  </div>
                  <p className="text-xs text-muted-foreground">{s.url ?? s.command}</p>
                  {tested[s.id] ? <p className="mt-1 text-xs">{tested[s.id]}</p> : null}
                </div>
                <Button size="sm" variant="outline" onClick={() => test(s)}>
                  Test
                </Button>
                <Button size="sm" variant="ghost" onClick={() => remove(s)}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {creating ? (
        <Dialog open onClose={() => setCreating(false)}>
          <DialogHeader>
            <DialogTitle>Add MCP server</DialogTitle>
          </DialogHeader>
          <div className="space-y-3 px-1 py-2">
            <label className="block text-sm">
              Name
              <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="github" />
            </label>
            <label className="block text-sm">
              Transport
              <select
                className={selectClass}
                value={transport}
                onChange={(e) => setTransport(e.target.value as McpTransport)}
              >
                {TRANSPORTS.map((t) => (
                  <option key={t} value={t}>
                    {t}
                  </option>
                ))}
              </select>
            </label>
            {transport === "stdio" ? (
              <label className="block text-sm">
                Command
                <Input value={command} onChange={(e) => setCommand(e.target.value)} placeholder="npx -y @modelcontextprotocol/server-github" />
              </label>
            ) : (
              <label className="block text-sm">
                URL
                <Input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://mcp.example.com/mcp" />
              </label>
            )}
            <label className="block text-sm">
              Bearer / API secret (optional)
              <Input type="password" value={secret} onChange={(e) => setSecret(e.target.value)} />
            </label>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreating(false)} disabled={saving}>
              Cancel
            </Button>
            <Button onClick={create} disabled={saving || !name}>
              {saving ? "Saving…" : "Save"}
            </Button>
          </DialogFooter>
        </Dialog>
      ) : null}
    </div>
  );
}
