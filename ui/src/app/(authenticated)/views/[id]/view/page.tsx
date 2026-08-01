"use client";

import { use } from "react";

import { ViewRuntime } from "@/components/views/ViewRuntime";

/** Runtime viewer for a view, inside the normal app chrome. The chrome-free
 * tablet/wall presentation of the same view lives at `../kiosk`. */
export default function ViewViewerPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ViewRuntime id={id} />;
}
