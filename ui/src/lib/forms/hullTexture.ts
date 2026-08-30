/**
 * Procedural hull plating: texture coordinates and the texture itself.
 *
 * An STL stores triangles and nothing else — no texture coordinates, no
 * materials. So a textured STL needs both halves generated: a UV projection over
 * the mesh, and an image to project. Both are built here rather than shipped as
 * assets, which keeps the fleet's look in code and needs no image files on a
 * build that has to run offline.
 */

/**
 * Box-project texture coordinates onto a non-indexed geometry.
 *
 * Each triangle is projected along whichever axis its face normal points down
 * hardest, using the other two coordinates as u/v. That is the standard trick
 * for a mesh with no UVs: it holds panel size roughly constant everywhere and
 * has no pole or seam artefacts, at the cost of a visible discontinuity where a
 * surface turns past 45° — which on hull plating reads as a panel break rather
 * than as a defect.
 *
 * `scale` is in model units per texture tile.
 */
export function boxProjectUvs(
  position: { array: ArrayLike<number>; count: number },
  scale: number
): Float32Array {
  const uv = new Float32Array(position.count * 2);
  const p = position.array;

  for (let t = 0; t < position.count; t += 3) {
    const i = t * 3;
    const ax = p[i],
      ay = p[i + 1],
      az = p[i + 2];
    const bx = p[i + 3],
      by = p[i + 4],
      bz = p[i + 5];
    const cx = p[i + 6],
      cy = p[i + 7],
      cz = p[i + 8];

    // Face normal via the cross product of two edges.
    const e1x = bx - ax,
      e1y = by - ay,
      e1z = bz - az;
    const e2x = cx - ax,
      e2y = cy - ay,
      e2z = cz - az;
    const nx = Math.abs(e1y * e2z - e1z * e2y);
    const ny = Math.abs(e1z * e2x - e1x * e2z);
    const nz = Math.abs(e1x * e2y - e1y * e2x);

    // Dominant axis picks the projection plane.
    let u0: number, v0: number, u1: number, v1: number, u2: number, v2: number;
    if (nx >= ny && nx >= nz) {
      u0 = az; v0 = ay; u1 = bz; v1 = by; u2 = cz; v2 = cy;
    } else if (ny >= nx && ny >= nz) {
      u0 = ax; v0 = az; u1 = bx; v1 = bz; u2 = cx; v2 = cz;
    } else {
      u0 = ax; v0 = ay; u1 = bx; v1 = by; u2 = cx; v2 = cy;
    }

    const k = t * 2;
    uv[k] = u0 / scale;
    uv[k + 1] = v0 / scale;
    uv[k + 2] = u1 / scale;
    uv[k + 3] = v1 / scale;
    uv[k + 4] = u2 / scale;
    uv[k + 5] = v2 / scale;
  }
  return uv;
}

/** Deterministic pseudo-random in [0,1) — so plating is stable across repaints
 * rather than shimmering every time the texture is rebuilt. */
function rand(seed: number): number {
  const x = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return x - Math.floor(x);
}

/**
 * Draw a tiling plating sheet: panels of slightly varying tone, darker seams
 * between them, and occasional lighter plates. Returns a canvas ready to become
 * a texture. The tone range is deliberately narrow — plating should read as
 * surface detail catching the light, not as a chequerboard.
 */
export function makePlatingCanvas(size = 512, cells = 8): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return canvas;

  // Mid grey base: the material's colour multiplies through this, so the texture
  // carries variation and the material carries the hue.
  ctx.fillStyle = "#b8b8b8";
  ctx.fillRect(0, 0, size, size);

  const step = size / cells;
  for (let gy = 0; gy < cells; gy++) {
    for (let gx = 0; gx < cells; gx++) {
      const r = rand(gx * 31 + gy * 17);
      // Panels are split irregularly: a run of full-width plates broken by
      // half-width ones is what stops a grid reading as graph paper.
      const half = rand(gx * 7 + gy * 13) > 0.72;
      const tone = 176 + Math.round((r - 0.5) * 26);
      ctx.fillStyle = `rgb(${tone},${tone},${tone})`;
      ctx.fillRect(gx * step, gy * step, half ? step / 2 : step, step);
      if (half) {
        const tone2 = 176 + Math.round((rand(gx * 3 + gy * 29) - 0.5) * 26);
        ctx.fillStyle = `rgb(${tone2},${tone2},${tone2})`;
        ctx.fillRect(gx * step + step / 2, gy * step, step / 2, step);
      }
    }
  }

  // Seams last, so they sit over every panel edge.
  ctx.strokeStyle = "rgba(90,96,104,0.55)";
  ctx.lineWidth = Math.max(1, size / 512);
  for (let i = 0; i <= cells; i++) {
    const at = i * step;
    ctx.beginPath();
    ctx.moveTo(at, 0);
    ctx.lineTo(at, size);
    ctx.moveTo(0, at);
    ctx.lineTo(size, at);
    ctx.stroke();
  }
  // The half-panel seams.
  ctx.strokeStyle = "rgba(90,96,104,0.35)";
  for (let gy = 0; gy < cells; gy++) {
    for (let gx = 0; gx < cells; gx++) {
      if (rand(gx * 7 + gy * 13) > 0.72) {
        const x = gx * step + step / 2;
        ctx.beginPath();
        ctx.moveTo(x, gy * step);
        ctx.lineTo(x, (gy + 1) * step);
        ctx.stroke();
      }
    }
  }
  return canvas;
}
