"use client";

import { useEffect, useRef, useState } from "react";

import type { Model3dElement } from "@/lib/api/forms";
import { fillTokens } from "@/lib/forms/href";
import { boxProjectUvs, makeHullMaps } from "@/lib/forms/hullTexture";

/**
 * A 3D model, rendered with three.js.
 *
 * The rig follows the one in reachy-virtual-robot's `robot3d.js`, which drives
 * the printed Reachy STLs: a hemisphere light for ambient fill plus a warm key
 * and a cool back-fill, `MeshStandardMaterial` so the surface actually responds
 * to them, and OrbitControls for a viewer who wants to look at the other side.
 *
 * three.js is BUNDLED, not fetched. That repo vendors `three.module.js` into its
 * static directory because it serves plain HTML with an importmap; here the
 * bundler does the same job, and the dynamic import below keeps it in a chunk
 * that only loads on a page carrying this element. Either way nothing reaches
 * for a CDN at runtime, which is the requirement — these screens run on
 * isolated networks.
 */

export function Model3dNode({
  el,
  values,
  recordId,
}: {
  el: Model3dElement;
  values: Record<string, unknown>;
  recordId?: string | null;
}) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const url = fillTokens(el.url ?? "", { ...values, id: recordId ?? "" });
  const height = el.height ?? 260;
  const spin = el.spin_seconds ?? 18;
  const angle = el.angle ?? 0;
  const colorProp = el.color ?? null;
  const finish = el.finish ?? "smooth";
  const panelScale = el.panel_scale ?? 0.12;

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    if (!url || url === "#") {
      setError("No model.");
      setLoading(false);
      return;
    }

    let disposed = false;
    let cleanup: (() => void) | null = null;

    void (async () => {
      // Loaded here rather than at module scope so three.js lands in its own
      // chunk: a view with no 3D on it should not pay for the library.
      const THREE = await import("three");
      const { STLLoader } = await import("three/examples/jsm/loaders/STLLoader.js");
      const { OrbitControls } = await import("three/examples/jsm/controls/OrbitControls.js");
      if (disposed) return;

      const width = host.clientWidth || 300;
      const scene = new THREE.Scene();
      const camera = new THREE.PerspectiveCamera(38, width / height, 0.1, 100000);
      const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setSize(width, height);
      host.appendChild(renderer.domElement);

      // Same three-light rig as robot3d.js: ambient sky/ground fill, a bright key
      // from the front-right, and a cool back-fill so the shadowed side still
      // reads as a surface rather than a silhouette.
      scene.add(new THREE.HemisphereLight(0xdfefff, 0x1b2430, 1.1));
      const key = new THREE.DirectionalLight(0xffffff, 1.4);
      key.position.set(180, 320, 420);
      scene.add(key);
      const fill = new THREE.DirectionalLight(0x9fc0ff, 0.5);
      fill.position.set(-260, 120, -160);
      scene.add(fill);

      const themed = getComputedStyle(host).getPropertyValue("--color-primary").trim();
      const material = new THREE.MeshStandardMaterial({
        color: new THREE.Color(colorProp || themed || "#7f9bd4"),
        roughness: 0.5,
        metalness: 0.25,
      });

      // Plating is generated, not loaded: an STL has no UVs and no material, so
      // both halves have to be produced here. Three sheets built off one panel
      // layout drive `map`, `bumpMap` and `roughnessMap`, so the seam that
      // darkens the colour is the seam that catches the light and scatters it.
      const plating: import("three").Texture[] = [];
      if (finish === "panelled") {
        const maps = makeHullMaps();
        const asTexture = (canvas: HTMLCanvasElement) => {
          const tex = new THREE.CanvasTexture(canvas);
          tex.wrapS = THREE.RepeatWrapping;
          tex.wrapT = THREE.RepeatWrapping;
          tex.anisotropy = renderer.capabilities.getMaxAnisotropy();
          plating.push(tex);
          return tex;
        };
        // Only the colour sheet is sRGB — bump and roughness are data, and
        // tagging them as colour would gamma-shift the values they encode.
        material.map = asTexture(maps.color);
        material.map.colorSpace = THREE.SRGBColorSpace;
        material.bumpMap = asTexture(maps.bump);
        material.bumpScale = 0.5;
        material.roughnessMap = asTexture(maps.roughness);
        // The scalar multiplies the map, so it moves to 1 and the map carries
        // the actual per-plate values.
        material.roughness = 1.0;
        material.needsUpdate = true;
      }

      const controls = new OrbitControls(camera, renderer.domElement);
      controls.enableDamping = false; // damping fights a manual render loop
      controls.enablePan = false;

      const pivot = new THREE.Group();
      scene.add(pivot);

      let raf = 0;
      let mesh: import("three").Mesh | null = null;

      const onResize = () => {
        const w = host.clientWidth || 300;
        camera.aspect = w / height;
        camera.updateProjectionMatrix();
        renderer.setSize(w, height);
      };
      window.addEventListener("resize", onResize);

      cleanup = () => {
        cancelAnimationFrame(raf);
        window.removeEventListener("resize", onResize);
        controls.dispose();
        mesh?.geometry.dispose();
        for (const tex of plating) tex.dispose();
        material.dispose();
        renderer.dispose();
        renderer.domElement.remove();
      };

      new STLLoader().load(
        url,
        (geometry) => {
          if (disposed) return;
          // STLs carry no origin convention — these are authored Z-up in
          // millimetres — so centre the geometry and frame the camera off its
          // own bounding sphere instead of assuming a scale.
          geometry.computeVertexNormals();
          geometry.center();
          geometry.computeBoundingSphere();
          const r = geometry.boundingSphere?.radius ?? 1;

          if (plating.length > 0) {
            // Panel size is expressed as a fraction of the model, so plating
            // stays the same apparent size whether the STL is authored in
            // millimetres or metres.
            const pos = geometry.getAttribute("position");
            const uv = boxProjectUvs(
              { array: pos.array as ArrayLike<number>, count: pos.count },
              Math.max(r * panelScale, 1e-4)
            );
            geometry.setAttribute("uv", new THREE.BufferAttribute(uv, 2));
          }

          mesh = new THREE.Mesh(geometry, material);
          // Z-up model into three's Y-up world, then a slight downward tilt so
          // the hull is seen from above rather than edge-on.
          mesh.rotation.x = -Math.PI / 2;
          pivot.add(mesh);
          pivot.rotation.y = (angle * Math.PI) / 180;

          camera.position.set(r * 1.6, r * 1.15, r * 2.0);
          camera.lookAt(0, 0, 0);
          controls.minDistance = r * 1.2;
          controls.maxDistance = r * 8;
          controls.update();

          setLoading(false);
          setError(null);

          let last = performance.now();
          const frame = () => {
            raf = requestAnimationFrame(frame);
            const now = performance.now();
            if (spin > 0) {
              pivot.rotation.y += ((now - last) / 1000) * ((Math.PI * 2) / spin);
            }
            last = now;
            controls.update();
            renderer.render(scene, camera);
          };
          raf = requestAnimationFrame(frame);
        },
        undefined,
        () => {
          if (disposed) return;
          setLoading(false);
          setError("Could not load the model.");
        }
      );
    })();

    return () => {
      disposed = true;
      cleanup?.();
    };
  }, [url, height, spin, angle, colorProp, finish, panelScale]);

  return (
    <div className="w-full">
      {el.label ? (
        <p className="mb-1 text-sm font-medium text-muted-foreground">{el.label}</p>
      ) : null}
      <div
        ref={hostRef}
        style={{ width: "100%", height }}
        role="img"
        aria-label={el.label ?? "3D model"}
      />
      {error ? <p className="mt-1 text-xs text-muted-foreground">{error}</p> : null}
      {loading && !error ? (
        <p className="mt-1 text-xs text-muted-foreground">Loading model…</p>
      ) : null}
    </div>
  );
}
