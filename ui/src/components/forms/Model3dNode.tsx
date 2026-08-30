"use client";

import { useEffect, useRef, useState } from "react";

import { fillTokens } from "@/lib/forms/href";
import { parseBinaryStl, type Mesh, type Vec3 } from "@/lib/forms/stl";
import type { Model3dElement } from "@/lib/api/forms";

/**
 * A binary STL rendered as a slowly turning flat-shaded solid.
 *
 * Painted on a 2D canvas rather than through WebGL: this appears on status
 * screens, the meshes are a few hundred triangles, and a 3D library is a lot of
 * weight to carry for a turning object — particularly on a build that has to run
 * with no network. Triangles are sorted back-to-front and filled (a painter's
 * algorithm), which is exact enough for a convex-ish hull and has no depth
 * buffer to manage.
 */

function shade(hex: string, amount: number): string {
  const h = hex.replace("#", "");
  const full = h.length === 3 ? h.split("").map((c) => c + c).join("") : h;
  const n = parseInt(full, 16);
  const clamp = (v: number) => Math.max(0, Math.min(255, Math.round(v)));
  const r = clamp(((n >> 16) & 255) * amount);
  const g = clamp(((n >> 8) & 255) * amount);
  const b = clamp((n & 255) * amount);
  return `rgb(${r},${g},${b})`;
}

export function Model3dNode({
  el,
  values,
  recordId,
}: {
  el: Model3dElement;
  values: Record<string, unknown>;
  recordId?: string | null;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [mesh, setMesh] = useState<Mesh | null>(null);
  const [error, setError] = useState<string | null>(null);

  const url = fillTokens(el.url ?? "", { ...values, id: recordId ?? "" });

  useEffect(() => {
    let alive = true;
    setError(null);
    setMesh(null);
    if (!url || url === "#") {
      setError("No model.");
      return;
    }
    void (async () => {
      try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(String(res.status));
        const parsed = parseBinaryStl(await res.arrayBuffer());
        if (!alive) return;
        if (parsed.triangles.length === 0) {
          setError("That file is not a binary STL.");
          return;
        }
        setMesh(parsed);
      } catch {
        if (alive) setError("Could not load the model.");
      }
    })();
    return () => {
      alive = false;
    };
  }, [url]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = hostRef.current;
    if (!canvas || !host || !mesh) return;
    let raf = 0;
    const start = performance.now();

    const draw = () => {
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const dpr = window.devicePixelRatio || 1;
      const w = host.clientWidth || 300;
      const h = el.height ?? 260;
      if (canvas.width !== Math.floor(w * dpr) || canvas.height !== Math.floor(h * dpr)) {
        canvas.width = Math.floor(w * dpr);
        canvas.height = Math.floor(h * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const base =
        el.color ||
        getComputedStyle(host).getPropertyValue("--color-primary").trim() ||
        "#7f9bd4";

      const spin = el.spin_seconds ?? 18;
      const t = spin > 0 ? ((performance.now() - start) / (spin * 1000)) * Math.PI * 2 : 0;
      const yaw = t + (el.angle ?? 0) * (Math.PI / 180);
      // A fixed tilt so the object is seen from slightly above: a hull viewed
      // exactly edge-on reads as a line.
      const pitch = -0.42;

      const scale = mesh.radius > 0 ? (Math.min(w, h) * 0.42) / mesh.radius : 1;
      const cx = w / 2;
      const cy = h / 2;

      const project = (p: Vec3) => {
        const x = p[0] - mesh.center[0];
        const y = p[1] - mesh.center[1];
        const z = p[2] - mesh.center[2];
        const rx = x * Math.cos(yaw) - y * Math.sin(yaw);
        const ry = x * Math.sin(yaw) + y * Math.cos(yaw);
        const rz = z;
        const py = ry * Math.cos(pitch) - rz * Math.sin(pitch);
        const pz = ry * Math.sin(pitch) + rz * Math.cos(pitch);
        return { sx: cx + rx * scale, sy: cy - pz * scale, depth: py };
      };

      const faces = mesh.triangles.map((tri) => {
        const a = project(tri[0]);
        const b = project(tri[1]);
        const c = project(tri[2]);
        // Screen-space normal: its sign is the facing, its magnitude drives the
        // fill. Cheaper than rotating the stored normal and never out of step
        // with the winding actually being drawn.
        const nz = (b.sx - a.sx) * (c.sy - a.sy) - (b.sy - a.sy) * (c.sx - a.sx);
        return { a, b, c, nz, depth: (a.depth + b.depth + c.depth) / 3 };
      });
      faces.sort((f, g) => g.depth - f.depth);

      for (const f of faces) {
        if (f.nz <= 0) continue; // back face
        const area = Math.abs(f.nz);
        // Facing-ness stands in for a light: broad faces read bright, faces
        // turning away fall off. Bounded so nothing goes flat black or blows out.
        const lit = 0.45 + Math.min(0.75, area / 900);
        ctx.fillStyle = shade(base, lit);
        ctx.beginPath();
        ctx.moveTo(f.a.sx, f.a.sy);
        ctx.lineTo(f.b.sx, f.b.sy);
        ctx.lineTo(f.c.sx, f.c.sy);
        ctx.closePath();
        ctx.fill();
      }

      if (spin > 0) raf = requestAnimationFrame(draw);
    };

    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, [mesh, el]);

  return (
    <div ref={hostRef} className="w-full">
      {el.label ? (
        <p className="mb-1 text-sm font-medium text-muted-foreground">{el.label}</p>
      ) : null}
      <canvas
        ref={canvasRef}
        style={{ width: "100%", height: el.height ?? 260 }}
        role="img"
        aria-label={el.label ?? "3D model"}
      />
      {error ? <p className="mt-1 text-xs text-muted-foreground">{error}</p> : null}
    </div>
  );
}
