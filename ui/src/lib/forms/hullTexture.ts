/**
 * Procedural hull plating: texture coordinates and the textures themselves.
 *
 * An STL stores triangles and nothing else — no texture coordinates, no
 * materials. So a textured STL needs both halves generated: a UV projection over
 * the mesh, and images to project. Both are built here rather than shipped as
 * assets, which keeps the fleet's look in code and needs no image files on a
 * build that has to run offline.
 *
 * Three sheets are produced from one shared panel layout — colour, bump and
 * roughness — so a plate that reads darker also sits at a different height and
 * catches light differently. Keeping them in register is what makes the panels
 * read as physical plates instead of paint.
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

/** A stream of deterministic randoms. Every consumer gets its own start value so
 * adding draws to one feature cannot reshuffle another. */
function stream(start: number): () => number {
  let n = start;
  return () => rand(n++);
}

/** The three sheets a plated hull material needs, in register with each other. */
export interface HullMaps {
  color: HTMLCanvasElement;
  bump: HTMLCanvasElement;
  roughness: HTMLCanvasElement;
}

interface Panel {
  x: number;
  y: number;
  w: number;
  h: number;
  /** Tonal patchwork offset — the "aztec" variation between plates. */
  tone: number;
  /** A whisper of warm/cool bias so plates differ in more than brightness. */
  tint: number;
  /** How matte this plate is relative to its neighbours. */
  rough: number;
  /** Joint weight: most plates butt tightly, some carry a heavier seam. */
  seam: number;
  /** Height offset in the bump sheet — plates sit at slightly different levels. */
  lift: number;
  /** Plates past a threshold get visible scuffing. */
  scuff: number;
}

interface Streak {
  x: number;
  y: number;
  len: number;
  w: number;
  a: number;
}

interface Mottle {
  x: number;
  y: number;
  r: number;
  a: number;
  light: boolean;
}

/**
 * Rows of varying height, each filled with plates of varying width — a mix of
 * long plates and runs of short ones, some split into stacked halves. Rows
 * partition the sheet's height exactly and each row's plates partition its
 * width exactly (starting from a per-row phase so vertical joints never line up
 * column to column), which is what lets the sheet tile without a visible seam.
 */
function buildPanels(size: number, next: () => number): Panel[] {
  const panels: Panel[] = [];
  const minRow = size * 0.075;
  const maxRow = size * 0.17;

  const plate = (x: number, y: number, w: number, h: number) => {
    panels.push({
      x: x >= size ? x - size : x,
      y,
      w,
      h,
      tone: next(),
      tint: next(),
      rough: next(),
      seam: next(),
      lift: next(),
      scuff: next(),
    });
  };

  let y = 0;
  while (y < size - 0.5) {
    let h = minRow + next() * (maxRow - minRow);
    // A short remainder joins this row rather than becoming a sliver of its own.
    if (size - y - h < minRow) h = size - y;

    const phase = next() * size;
    const end = phase + size;
    let x = phase;
    while (x < end - 0.5) {
      const wide = next() < 0.3;
      let w = wide ? h * (1.5 + next() * 1.3) : h * (0.55 + next() * 0.7);
      if (end - x - w < h * 0.35) w = end - x;
      if (next() < 0.25) {
        // Two stacked half-height plates: long thin strips out of wide slots,
        // small squares out of narrow ones — the offset runs that stop the
        // layout reading as a grid.
        plate(x, y, w, h / 2);
        plate(x, y + h / 2, w, h / 2);
      } else {
        plate(x, y, w, h);
      }
      x += w;
    }
    y += h;
  }
  return panels;
}

function buildStreaks(size: number, next: () => number): Streak[] {
  const streaks: Streak[] = [];
  const count = Math.round(48 * (size / 1024));
  for (let i = 0; i < count; i++) {
    streaks.push({
      x: next() * size,
      y: next() * size,
      len: size * (0.03 + next() * 0.1),
      w: (1 + next() * 2.5) * (size / 1024),
      a: 0.03 + next() * 0.05,
    });
  }
  return streaks;
}

function buildMottles(size: number, next: () => number): Mottle[] {
  const mottles: Mottle[] = [];
  const count = Math.round(20 * (size / 1024));
  for (let i = 0; i < count; i++) {
    mottles.push({
      x: next() * size,
      y: next() * size,
      r: size * (0.08 + next() * 0.14),
      a: 0.02 + next() * 0.02,
      light: next() > 0.5,
    });
  }
  return mottles;
}

/** Draw a shape at every 3×3 tile offset, so anything crossing a canvas edge
 * reappears on the opposite side. This is what guarantees the sheet tiles
 * seamlessly under RepeatWrapping — the copies are translated by exactly one
 * tile, so nothing inside the canvas is ever painted twice. */
function wrapped(
  ctx: CanvasRenderingContext2D,
  size: number,
  draw: (c: CanvasRenderingContext2D) => void
): void {
  for (let dx = -1; dx <= 1; dx++) {
    for (let dy = -1; dy <= 1; dy++) {
      ctx.save();
      ctx.translate(dx * size, dy * size);
      draw(ctx);
      ctx.restore();
    }
  }
}

/** Per-pixel grain so raking light finds tooth everywhere instead of dead-flat
 * regions. Each pixel is hashed independently, so the grain has no continuity
 * requirement across the tile edge and cannot break the wrap. */
function addGrain(ctx: CanvasRenderingContext2D, size: number, amp: number): void {
  const img = ctx.getImageData(0, 0, size, size);
  const d = img.data;
  const total = size * size;
  for (let px = 0, i = 0; px < total; px++, i += 4) {
    let h = px | 0;
    h = Math.imul(h ^ (h >>> 15), 2246822519);
    h = Math.imul(h ^ (h >>> 13), 3266489917);
    h ^= h >>> 16;
    const n = ((h & 0xff) / 255 - 0.5) * amp;
    d[i] += n;
    d[i + 1] += n;
    d[i + 2] += n;
  }
  ctx.putImageData(img, 0, 0);
}

function paintColor(
  ctx: CanvasRenderingContext2D,
  size: number,
  panels: Panel[],
  streaks: Streak[],
  mottles: Mottle[]
): void {
  const k = size / 1024;
  ctx.fillStyle = "#b6b6b6";
  ctx.fillRect(0, 0, size, size);

  // Mid grey with a narrow spread: the material's colour multiplies through
  // this sheet, so the texture carries variation and the material carries the
  // hue. Anything stronger than a few percent reads as a chequerboard.
  for (const p of panels) {
    const t = 182 + (p.tone - 0.5) * 22 + (p.scuff > 0.94 ? 9 : 0);
    const bias = (p.tint - 0.5) * 5;
    const r = Math.round(t + bias);
    const g = Math.round(t);
    const b = Math.round(t - bias);
    wrapped(ctx, size, (c) => {
      c.fillStyle = `rgb(${r},${g},${b})`;
      c.fillRect(p.x, p.y, p.w, p.h);
      if (p.w > 8 * k && p.h > 8 * k) {
        // Grime settles where plates meet: a faint inset band darkens each
        // plate's border without touching the seam line itself.
        c.strokeStyle = "rgba(28,32,38,0.08)";
        c.lineWidth = 3 * k;
        c.strokeRect(p.x + 1.5 * k, p.y + 1.5 * k, p.w - 3 * k, p.h - 3 * k);
      }
    });
  }

  for (const p of panels) {
    const heavy = p.seam > 0.82;
    wrapped(ctx, size, (c) => {
      c.strokeStyle = heavy ? "rgba(50,55,62,0.6)" : "rgba(62,68,76,0.35)";
      c.lineWidth = (heavy ? 2 : 1) * k;
      c.strokeRect(p.x, p.y, p.w, p.h);
    });
  }

  // A handful of scuffed plates, mottled inside their own outline only —
  // clipping keeps the wear from smearing across a seam, which would read as
  // dirt on the lens rather than on the hull.
  panels.forEach((p, i) => {
    if (p.scuff < 0.9) return;
    wrapped(ctx, size, (c) => {
      c.save();
      c.beginPath();
      c.rect(p.x, p.y, p.w, p.h);
      c.clip();
      for (let j = 0; j < 4; j++) {
        const bx = p.x + rand(i * 17.31 + j * 3.7) * p.w;
        const by = p.y + rand(i * 23.7 + j * 5.1) * p.h;
        const br = (0.15 + rand(i * 31.9 + j * 7.7) * 0.3) * Math.min(p.w, p.h);
        c.fillStyle =
          rand(i * 41.3 + j * 11.1) > 0.5 ? "rgba(255,255,255,0.05)" : "rgba(20,24,30,0.06)";
        c.beginPath();
        c.arc(bx, by, br, 0, Math.PI * 2);
        c.fill();
      }
      c.restore();
    });
  });

  // Weathering streaks, fading downward from their source point the way
  // residue trails off a fitting. Texture-space "down" lands in arbitrary
  // directions after the box projection, which conveniently varies them.
  for (const s of streaks) {
    wrapped(ctx, size, (c) => {
      const g = c.createLinearGradient(0, s.y, 0, s.y + s.len);
      g.addColorStop(0, `rgba(24,28,34,${s.a})`);
      g.addColorStop(1, "rgba(24,28,34,0)");
      c.fillStyle = g;
      c.fillRect(s.x - s.w / 2, s.y, s.w, s.len);
    });
  }

  // Broad soft mottling below the panel frequency, so large hull areas drift
  // in tone instead of repeating one flat value tile after tile.
  for (const m of mottles) {
    const rgb = m.light ? "236,238,240" : "16,20,26";
    wrapped(ctx, size, (c) => {
      const g = c.createRadialGradient(m.x, m.y, 0, m.x, m.y, m.r);
      g.addColorStop(0, `rgba(${rgb},${m.a})`);
      g.addColorStop(1, `rgba(${rgb},0)`);
      c.fillStyle = g;
      c.fillRect(m.x - m.r, m.y - m.r, m.r * 2, m.r * 2);
    });
  }

  addGrain(ctx, size, 7);
}

function paintBump(ctx: CanvasRenderingContext2D, size: number, panels: Panel[]): void {
  const k = size / 1024;
  ctx.fillStyle = "rgb(128,128,128)";
  ctx.fillRect(0, 0, size, size);

  // Height only: plates at slightly different levels, recessed joints, and
  // grain. Grime and streaks stay out of this sheet on purpose — baked into a
  // bump map they would read as dents rather than dirt.
  for (const p of panels) {
    const v = Math.round(128 + (p.lift - 0.5) * 13);
    wrapped(ctx, size, (c) => {
      c.fillStyle = `rgb(${v},${v},${v})`;
      c.fillRect(p.x, p.y, p.w, p.h);
    });
  }
  for (const p of panels) {
    const heavy = p.seam > 0.82;
    wrapped(ctx, size, (c) => {
      c.strokeStyle = heavy ? "rgba(0,0,0,0.5)" : "rgba(0,0,0,0.3)";
      c.lineWidth = (heavy ? 2.5 : 1.2) * k;
      c.strokeRect(p.x, p.y, p.w, p.h);
    });
  }
  addGrain(ctx, size, 5);
}

function paintRoughness(
  ctx: CanvasRenderingContext2D,
  size: number,
  panels: Panel[],
  streaks: Streak[]
): void {
  const k = size / 1024;
  ctx.fillStyle = "rgb(150,150,150)";
  ctx.fillRect(0, 0, size, size);

  // Plate-to-plate finish variation does more for "real hull" than any amount
  // of colour: some plates hold a sheen, most sit matte, the odd one is a
  // clearly duller replacement. The extremes of the same random that drives
  // the mid-range spread pick out those special plates.
  for (const p of panels) {
    let v = Math.round(150 + (p.rough - 0.5) * 80);
    if (p.rough > 0.95) v = 205;
    else if (p.rough < 0.04) v = 96;
    wrapped(ctx, size, (c) => {
      c.fillStyle = `rgb(${v},${v},${v})`;
      c.fillRect(p.x, p.y, p.w, p.h);
    });
  }

  // Joints scatter light: a slightly rougher line along every seam breaks up
  // specular highlights exactly where the colour sheet draws its seams.
  for (const p of panels) {
    wrapped(ctx, size, (c) => {
      c.strokeStyle = "rgba(255,255,255,0.28)";
      c.lineWidth = 1.2 * k;
      c.strokeRect(p.x, p.y, p.w, p.h);
    });
  }

  // The same streaks the colour sheet darkens go matte here — grime is dull.
  for (const s of streaks) {
    wrapped(ctx, size, (c) => {
      const g = c.createLinearGradient(0, s.y, 0, s.y + s.len);
      g.addColorStop(0, `rgba(255,255,255,${Math.min(s.a * 1.8, 0.14)})`);
      g.addColorStop(1, "rgba(255,255,255,0)");
      c.fillStyle = g;
      c.fillRect(s.x - s.w / 2, s.y, s.w, s.len);
    });
  }

  addGrain(ctx, size, 12);
}

/**
 * Draw the tiling plating sheets: colour, bump and roughness, all from one
 * panel layout so they stay in register. Deterministic — the same canvases
 * come back on every call — and everything near a canvas edge is drawn again
 * one tile over, so the sheets repeat seamlessly.
 */
export function makeHullMaps(size = 1024): HullMaps {
  const color = document.createElement("canvas");
  const bump = document.createElement("canvas");
  const roughness = document.createElement("canvas");
  for (const canvas of [color, bump, roughness]) {
    canvas.width = size;
    canvas.height = size;
  }

  const colorCtx = color.getContext("2d");
  const bumpCtx = bump.getContext("2d");
  const roughCtx = roughness.getContext("2d");
  // No 2D context (jsdom, some SSR paths): blank canvases still make valid,
  // if featureless, textures — better than throwing during render.
  if (!colorCtx || !bumpCtx || !roughCtx) return { color, bump, roughness };

  // Separate streams per feature so tuning one (say, streak count) cannot
  // reshuffle the randoms and re-lay every panel.
  const panels = buildPanels(size, stream(1));
  const streaks = buildStreaks(size, stream(90001));
  const mottles = buildMottles(size, stream(70001));

  paintColor(colorCtx, size, panels, streaks, mottles);
  paintBump(bumpCtx, size, panels);
  paintRoughness(roughCtx, size, panels, streaks);
  return { color, bump, roughness };
}
