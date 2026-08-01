"use client";

import { use } from "react";

import { ViewRuntime } from "@/components/views/ViewRuntime";

/**
 * KIOSK presentation of a view: the same runtime as `../view`, but the
 * authenticated layout recognises this route and drops the nav rail, header and
 * help dock, and the renderer gets the full screen with no card frame.
 *
 * This is the URL you put on a shared tablet or a wall display — a crew station,
 * a check-in pad, a status board — where the person in front of it is doing one
 * task and KM2's authoring UI is only a distraction and a way out of the app.
 * Auth still applies (it is inside the authenticated segment), so the device
 * must be signed in.
 */
export default function ViewKioskPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ViewRuntime id={id} kiosk />;
}
