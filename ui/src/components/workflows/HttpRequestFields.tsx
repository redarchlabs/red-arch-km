"use client";

import { Input } from "@/components/ui/input";
import {
  applyHttpField,
  applyHttpObjectField,
  HTTP_METHODS,
  readHttpObject,
  readHttpRequest,
} from "@/components/workflows/httpRequest";
import { RecordFieldEditor } from "@/components/workflows/RecordFieldEditor";
import type { Connection } from "@/lib/api/connections";

const selectClass = "h-9 w-full rounded-md border bg-background px-2 text-sm";

/**
 * Inspector for a `task` node whose `action_type` is `http_request` — the
 * authenticated connector call. Edits flow through the pure helpers in
 * `httpRequest.ts` and use the FULL-REPLACE path (`onReplace`) so pruning a blank
 * field (or clearing `capture`) actually removes the key, which a shallow merge
 * could not do.
 */
export function HttpRequestFields({
  nodeId,
  data,
  connections,
  onReplace,
}: {
  nodeId: string;
  data: Record<string, unknown>;
  /** Org connections for the picker; a text input is shown when absent. */
  connections?: Connection[];
  onReplace: (next: Record<string, unknown>) => void;
}) {
  const form = readHttpRequest(data);
  const setScalar = (field: Parameters<typeof applyHttpField>[1], value: string) =>
    onReplace(applyHttpField(data, field, value));
  const setObject = (field: "headers" | "body", value: Record<string, unknown>) =>
    onReplace(applyHttpObjectField(data, field, value));

  const knownConnection =
    !connections || form.connection === "" || connections.some((c) => c.name === form.connection);

  return (
    <div className="space-y-3">
      <div>
        <label className="text-xs font-medium text-muted-foreground">Connection</label>
        {connections && connections.length > 0 ? (
          <select
            value={form.connection}
            onChange={(e) => setScalar("connection", e.target.value)}
            className={`${selectClass} mt-1`}
          >
            <option value="">No connection (literal URL)</option>
            {connections.map((c) => (
              <option key={c.id} value={c.name}>
                {c.name}
                {c.auth_type !== "none" ? ` · ${c.auth_type}` : ""}
              </option>
            ))}
            {!knownConnection ? <option value={form.connection}>{form.connection}</option> : null}
          </select>
        ) : (
          <Input
            value={form.connection}
            onChange={(e) => setScalar("connection", e.target.value)}
            placeholder="Connection name"
            className="mt-1"
          />
        )}
        <p className="mt-1 text-xs text-muted-foreground">
          A saved connection injects its auth + base URL. Manage them under{" "}
          <span className="font-medium">Workflows → Connections</span>.
        </p>
      </div>

      <div className="flex gap-2">
        <div className="w-32">
          <label className="text-xs font-medium text-muted-foreground">Method</label>
          <select
            value={form.method}
            onChange={(e) => setScalar("method", e.target.value)}
            className={`${selectClass} mt-1`}
          >
            {HTTP_METHODS.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <div className="flex-1">
          <label className="text-xs font-medium text-muted-foreground">Path</label>
          <Input
            value={form.path}
            onChange={(e) => setScalar("path", e.target.value)}
            placeholder="/v1/charges"
            className="mt-1"
          />
        </div>
      </div>

      <div>
        <label className="text-xs font-medium text-muted-foreground">Literal URL (optional)</label>
        <Input
          value={form.url}
          onChange={(e) => setScalar("url", e.target.value)}
          placeholder="https://api.example.com/v1/ping"
          className="mt-1"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          Overrides the connection base URL + path. The host must be allow-listed.
        </p>
      </div>

      <div>
        <label className="text-xs font-medium text-muted-foreground">Headers</label>
        <div className="mt-1">
          <RecordFieldEditor
            key={`${nodeId}:http-headers`}
            value={readHttpObject(data, "headers")}
            onChange={(obj) => setObject("headers", obj)}
            emptyLabel="No extra headers."
            addLabel="Add header"
          />
        </div>
      </div>

      <div>
        <label className="text-xs font-medium text-muted-foreground">Body (JSON)</label>
        <div className="mt-1">
          <RecordFieldEditor
            key={`${nodeId}:http-body`}
            value={readHttpObject(data, "body")}
            onChange={(obj) => setObject("body", obj)}
            emptyLabel="No request body."
            addLabel="Add body field"
          />
        </div>
      </div>

      <div>
        <label className="text-xs font-medium text-muted-foreground">Capture response as</label>
        <Input
          value={form.capture}
          onChange={(e) => setScalar("capture", e.target.value)}
          placeholder="apiResponse"
          className="mt-1"
        />
        <p className="mt-1 text-xs text-muted-foreground">
          Optional run-variable name to store the response under for later nodes.
        </p>
      </div>
    </div>
  );
}
