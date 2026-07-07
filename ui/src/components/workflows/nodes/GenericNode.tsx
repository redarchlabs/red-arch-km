"use client";

import { type NodeProps } from "@xyflow/react";

import { BaseNode, useNodeChrome } from "./BaseNode";
import { handlesFor, metaFor } from "./nodeMeta";

/**
 * Fallback renderer for legacy vestigial types (merge / passthrough) — and any
 * future/unknown type — driven entirely by the registry so old graphs still
 * render rather than crash.
 */
export function GenericNode({ id, type, data, selected }: NodeProps) {
  const meta = metaFor(type);
  const Icon = meta.icon;
  const chrome = useNodeChrome(id);
  return (
    <BaseNode
      accent={meta.accent}
      label={meta.label}
      glyph={<Icon className="h-4 w-4" />}
      selected={selected}
      handles={handlesFor({ type: type ?? "", data: data as Record<string, unknown> })}
      status={chrome.status}
      problem={chrome.problem}
      problemCount={chrome.problemCount}
    />
  );
}
