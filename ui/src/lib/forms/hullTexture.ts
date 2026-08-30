/**
 * Procedural hull plating: texture coordinates and the textures themselves.
 *
 * An STL stores triangles and nothing else — no texture coordinates, no
 * materials. So a textured STL needs both halves generated: a UV projection over
 * the mesh, and images to project. Both are built here rather than shipped as
 * assets, which keeps the surface's look in code and needs no image files on a
 * build that has to run offline.
 *
 * Three sheets are produced from one shared layout — colour, bump and
 * roughness — so a plate that reads darker also sits at a different height and
 * catches light differently. The layout itself is a hierarchy: structural
 * plates, sub-panels inside the larger plates, and fine fittings (hatches,
 * vents, fastener runs) inside those. A single frequency of detail reads as
 * blocks at one distance and as nothing at every other; the hierarchy gives
 * the eye something at each zoom level.
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
    const sx = e1y * e2z - e1z * e2y;
    const sy = e1z * e2x - e1x * e2z;
    const sz = e1x * e2y - e1y * e2x;
    const nx = Math.abs(sx);
    const ny = Math.abs(sy);
    const nz = Math.abs(sz);

    // Dominant axis picks the projection plane, and the SIGN of that component
    // picks the handedness. Projecting both faces of an axis the same way maps
    // them from opposite sides of the same plane, which mirrors the texture on
    // whichever face points the other way — visible as plating that runs
    // backwards down one side of a symmetric hull. Flipping u for a
    // negative-facing triangle is the standard fix.
    let u0: number, v0: number, u1: number, v1: number, u2: number, v2: number;
    let flip: boolean;
    if (nx >= ny && nx >= nz) {
      u0 = az; v0 = ay; u1 = bz; v1 = by; u2 = cz; v2 = cy;
      flip = sx < 0;
    } else if (ny >= nx && ny >= nz) {
      u0 = ax; v0 = az; u1 = bx; v1 = bz; u2 = cx; v2 = cz;
      flip = sy < 0;
    } else {
      u0 = ax; v0 = ay; u1 = bx; v1 = by; u2 = cx; v2 = cy;
      flip = sz < 0;
    }
    if (flip) {
      u0 = -u0;
      u1 = -u1;
      u2 = -u2;
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

/** A panel-within-a-plate: same idea one octave down, drawn as an overlay so it
 * modulates whatever plate it sits on instead of carrying its own base tone. */
interface SubPanel {
  x: number;
  y: number;
  w: number;
  h: number;
  shade: number;
  lift: number;
  rough: number;
}

/** A leaf region — an unsplit plate or a sub-panel cell — the areas fine
 * details are placed into, so a hatch never straddles a seam. */
interface Region {
  x: number;
  y: number;
  w: number;
  h: number;
}

type DetailKind = "hatch" | "vent" | "strip" | "fitting" | "rivets";

interface Detail {
  kind: DetailKind;
  x: number;
  y: number;
  w: number;
  h: number;
  r1: number;
  r2: number;
}

/** How one sheet renders each detail kind. The geometry is shared; only the
 * inks differ, which is what keeps the three sheets in register. */
interface DetailStyle {
  hatchFill: string;
  hatchLine: string;
  vent: string;
  rivet: string;
  strip: string;
  fittingFill: string;
  fittingLine: string;
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
 * Rows of varying height, each filled with plates of varying width — mostly
 * small plates with occasional long structural runs. Rows partition the
 * sheet's height exactly and each row's plates partition its width exactly
 * (starting from a per-row phase so vertical joints never line up column to
 * column), which is what lets the sheet tile without a visible seam.
 */
function buildPanels(size: number, next: () => number): Panel[] {
  const panels: Panel[] = [];
  const minRow = size * 0.05;
  const maxRow = size * 0.115;

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
    // The odd deeper band keeps a structural rhythm now that the default row
    // is small; without these the sheet drifts toward tilework.
    if (next() < 0.18) h *= 1.5;
    // A short remainder joins this row rather than becoming a sliver of its own.
    if (size - y - h < minRow) h = size - y;

    const phase = next() * size;
    const end = phase + size;
    let x = phase;
    while (x < end - 0.5) {
      const kind = next();
      let w: number;
      if (kind < 0.07) w = h * (2.8 + next() * 1.4);
      else if (kind < 0.29) w = h * (1.6 + next() * 1.2);
      else w = h * (0.55 + next() * 0.7);
      if (end - x - w < h * 0.35) w = end - x;
      if (next() < 0.28) {
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

/**
 * Second level of the hierarchy: most larger plates are split into 2–3 cells
 * along their long axis, some cells split once more across. Every cell sits
 * strictly inside its parent plate, so this level inherits the parent's wrap
 * behaviour — a cell of an edge-crossing plate crosses the edge with it and is
 * completed by the same wrapped draw.
 */
function buildSubPanels(
  size: number,
  panels: Panel[],
  next: () => number
): { subs: SubPanel[]; leaves: Region[] } {
  const subs: SubPanel[] = [];
  const leaves: Region[] = [];
  const minCell = size * 0.02;

  const cell = (x: number, y: number, w: number, h: number) => {
    subs.push({ x, y, w, h, shade: next(), lift: next(), rough: next() });
    leaves.push({ x, y, w, h });
  };

  for (const p of panels) {
    const long = Math.max(p.w, p.h);
    if (Math.min(p.w, p.h) < minCell * 2.2 || next() > 0.7) {
      leaves.push({ x: p.x, y: p.y, w: p.w, h: p.h });
      continue;
    }
    const alongX = p.w >= p.h;
    let n = next() < 0.35 ? 3 : 2;
    if (long / n < minCell * 1.4) n = 2;

    const fr: number[] = [];
    let total = 0;
    for (let i = 0; i < n; i++) {
      const f = 0.7 + next() * 0.9;
      fr.push(f);
      total += f;
    }
    let off = 0;
    for (let i = 0; i < n; i++) {
      const span = (fr[i] / total) * long;
      const cx = alongX ? p.x + off : p.x;
      const cy = alongX ? p.y : p.y + off;
      const cw = alongX ? span : p.w;
      const ch = alongX ? p.h : span;
      const across = alongX ? ch : cw;
      if (next() < 0.35 && across > minCell * 2) {
        const g = 0.35 + next() * 0.3;
        if (alongX) {
          cell(cx, cy, cw, ch * g);
          cell(cx, cy + ch * g, cw, ch * (1 - g));
        } else {
          cell(cx, cy, cw * g, ch);
          cell(cx + cw * g, cy, cw * (1 - g), ch);
        }
      } else {
        cell(cx, cy, cw, ch);
      }
      off += span;
    }
  }
  return { subs, leaves };
}

/**
 * Third level: sparse fittings placed inside leaf regions with a margin, so no
 * detail ever crosses a seam — which is also what makes them tile: they wrap
 * exactly when their containing plate wraps. The randoms are drawn
 * unconditionally so every leaf advances the stream by the same amount and a
 * probability tweak cannot re-lay everything after it.
 */
function buildDetails(size: number, leaves: Region[], next: () => number): Detail[] {
  const details: Detail[] = [];
  const k = size / 1024;
  for (const leaf of leaves) {
    const roll = next();
    const jx = next();
    const jy = next();
    const r1 = next();
    const r2 = next();
    const m = Math.min(leaf.w, leaf.h);
    if (m < 14 * k) continue;

    if (roll < 0.06) {
      const w = m * (0.3 + r1 * 0.18);
      const h = w * (0.8 + r2 * 0.5);
      const x = leaf.x + (leaf.w - w) * (0.2 + jx * 0.6);
      const y = leaf.y + (leaf.h - h) * (0.2 + jy * 0.6);
      details.push({ kind: "hatch", x, y, w, h, r1, r2 });
    } else if (roll < 0.11 && m > 22 * k) {
      const w = leaf.w * (0.22 + r1 * 0.15);
      const h = leaf.h * (0.18 + r2 * 0.12);
      const x = leaf.x + (leaf.w - w) * (0.2 + jx * 0.6);
      const y = leaf.y + (leaf.h - h) * (0.2 + jy * 0.6);
      details.push({ kind: "vent", x, y, w, h, r1, r2 });
    } else if (roll < 0.21) {
      // A fastener run along one inset edge; w or h of zero encodes direction.
      const inset = 3.5 * k;
      const horizontal = r1 < 0.5;
      if (horizontal) {
        const y = r2 < 0.5 ? leaf.y + inset : leaf.y + leaf.h - inset;
        details.push({ kind: "rivets", x: leaf.x + inset, y, w: leaf.w - inset * 2, h: 0, r1, r2 });
      } else {
        const x = r2 < 0.5 ? leaf.x + inset : leaf.x + leaf.w - inset;
        details.push({ kind: "rivets", x, y: leaf.y + inset, w: 0, h: leaf.h - inset * 2, r1, r2 });
      }
    } else if (roll < 0.28) {
      // A single inset access strip parallel to the leaf's long edge.
      const inset = 3 * k;
      if (leaf.w >= leaf.h) {
        const y = leaf.y + leaf.h * (0.25 + r1 * 0.5);
        details.push({ kind: "strip", x: leaf.x + inset, y, w: leaf.w - inset * 2, h: 0, r1, r2 });
      } else {
        const x = leaf.x + leaf.w * (0.25 + r1 * 0.5);
        details.push({ kind: "strip", x, y: leaf.y + inset, w: 0, h: leaf.h - inset * 2, r1, r2 });
      }
    } else if (roll < 0.33) {
      const s = (4 + r1 * 6) * k;
      const x = leaf.x + (leaf.w - s) * (0.15 + jx * 0.7);
      const y = leaf.y + (leaf.h - s) * (0.15 + jy * 0.7);
      details.push({ kind: "fitting", x, y, w: s, h: s * (0.7 + r2 * 0.6), r1, r2 });
    }
  }
  return details;
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

/** Sub-panels are painted as translucent overlays plus a hairline outline —
 * they modulate the plate under them rather than replacing it, so the plate's
 * aztec patchwork stays the dominant read. */
function drawSubPanels(
  ctx: CanvasRenderingContext2D,
  size: number,
  subs: SubPanel[],
  field: "shade" | "lift" | "rough",
  strength: number,
  line: string,
  lineWidth: number
): void {
  for (const s of subs) {
    const v = s[field] - 0.5;
    const a = Math.abs(v) * strength;
    wrapped(ctx, size, (c) => {
      c.fillStyle = v > 0 ? `rgba(255,255,255,${a})` : `rgba(10,14,20,${a})`;
      c.fillRect(s.x, s.y, s.w, s.h);
      c.strokeStyle = line;
      c.lineWidth = lineWidth;
      c.strokeRect(s.x, s.y, s.w, s.h);
    });
  }
}

/** One geometry routine for the fine details, inked per sheet — identical
 * shapes across colour, bump and roughness is what makes a hatch read as a
 * physical thing rather than three unrelated smudges. */
function drawDetail(c: CanvasRenderingContext2D, d: Detail, k: number, s: DetailStyle): void {
  switch (d.kind) {
    case "hatch":
      c.fillStyle = s.hatchFill;
      c.fillRect(d.x, d.y, d.w, d.h);
      c.strokeStyle = s.hatchLine;
      c.lineWidth = 0.8 * k;
      c.strokeRect(d.x, d.y, d.w, d.h);
      break;
    case "vent": {
      const lines = 3 + Math.round(d.r2 * 3);
      c.fillStyle = s.vent;
      if (d.r1 > 0.5) {
        const gap = d.h / lines;
        for (let i = 0; i < lines; i++) {
          c.fillRect(d.x, d.y + i * gap, d.w, Math.max(0.8 * k, gap * 0.35));
        }
      } else {
        const gap = d.w / lines;
        for (let i = 0; i < lines; i++) {
          c.fillRect(d.x + i * gap, d.y, Math.max(0.8 * k, gap * 0.35), d.h);
        }
      }
      break;
    }
    case "rivets": {
      const dot = 1.4 * k;
      const gap = 7 * k;
      c.fillStyle = s.rivet;
      if (d.h === 0) {
        for (let x = d.x; x <= d.x + d.w; x += gap) c.fillRect(x, d.y - dot / 2, dot, dot);
      } else {
        for (let y = d.y; y <= d.y + d.h; y += gap) c.fillRect(d.x - dot / 2, y, dot, dot);
      }
      break;
    }
    case "strip": {
      const t = 0.9 * k;
      c.fillStyle = s.strip;
      if (d.h === 0) c.fillRect(d.x, d.y - t / 2, d.w, t);
      else c.fillRect(d.x - t / 2, d.y, t, d.h);
      break;
    }
    case "fitting":
      c.fillStyle = s.fittingFill;
      c.fillRect(d.x, d.y, d.w, d.h);
      c.strokeStyle = s.fittingLine;
      c.lineWidth = 0.6 * k;
      c.strokeRect(d.x, d.y, d.w, d.h);
      break;
  }
}

function drawDetails(
  ctx: CanvasRenderingContext2D,
  size: number,
  details: Detail[],
  style: DetailStyle
): void {
  const k = size / 1024;
  for (const d of details) {
    wrapped(ctx, size, (c) => drawDetail(c, d, k, style));
  }
}

const COLOR_DETAIL: DetailStyle = {
  hatchFill: "rgba(24,28,34,0.08)",
  hatchLine: "rgba(40,45,52,0.4)",
  vent: "rgba(30,34,40,0.3)",
  rivet: "rgba(30,34,40,0.2)",
  strip: "rgba(45,50,58,0.3)",
  fittingFill: "rgba(20,24,30,0.1)",
  fittingLine: "rgba(35,40,46,0.3)",
};

// Bump inks: dark is recessed, light is raised. Hatches and vents sink,
// fastener heads and fittings stand proud.
const BUMP_DETAIL: DetailStyle = {
  hatchFill: "rgba(0,0,0,0.15)",
  hatchLine: "rgba(0,0,0,0.45)",
  vent: "rgba(0,0,0,0.5)",
  rivet: "rgba(255,255,255,0.4)",
  strip: "rgba(0,0,0,0.3)",
  fittingFill: "rgba(255,255,255,0.3)",
  fittingLine: "rgba(0,0,0,0.3)",
};

// Roughness inks: light is matte. Vents and worn hatches go dull; fittings
// are machined metal, so they hold more sheen than the plate around them.
const ROUGH_DETAIL: DetailStyle = {
  hatchFill: "rgba(255,255,255,0.12)",
  hatchLine: "rgba(255,255,255,0.2)",
  vent: "rgba(255,255,255,0.2)",
  rivet: "rgba(255,255,255,0.15)",
  strip: "rgba(255,255,255,0.14)",
  fittingFill: "rgba(0,0,0,0.18)",
  fittingLine: "rgba(255,255,255,0.15)",
};

function paintColor(
  ctx: CanvasRenderingContext2D,
  size: number,
  panels: Panel[],
  subs: SubPanel[],
  details: Detail[],
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

  drawSubPanels(ctx, size, subs, "shade", 0.11, "rgba(58,64,72,0.22)", 0.5 * k);

  for (const p of panels) {
    const heavy = p.seam > 0.82;
    wrapped(ctx, size, (c) => {
      c.strokeStyle = heavy ? "rgba(50,55,62,0.6)" : "rgba(62,68,76,0.35)";
      c.lineWidth = (heavy ? 1.5 : 0.8) * k;
      c.strokeRect(p.x, p.y, p.w, p.h);
    });
  }

  drawDetails(ctx, size, details, COLOR_DETAIL);

  // A handful of scuffed plates, mottled inside their own outline only —
  // clipping keeps the wear from smearing across a seam, which would read as
  // dirt on the lens rather than on the hull.
  panels.forEach((p, i) => {
    if (p.scuff < 0.93) return;
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

function paintBump(
  ctx: CanvasRenderingContext2D,
  size: number,
  panels: Panel[],
  subs: SubPanel[],
  details: Detail[]
): void {
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

  drawSubPanels(ctx, size, subs, "lift", 0.07, "rgba(0,0,0,0.18)", 0.5 * k);

  for (const p of panels) {
    const heavy = p.seam > 0.82;
    wrapped(ctx, size, (c) => {
      c.strokeStyle = heavy ? "rgba(0,0,0,0.5)" : "rgba(0,0,0,0.3)";
      c.lineWidth = (heavy ? 2 : 1) * k;
      c.strokeRect(p.x, p.y, p.w, p.h);
    });
  }

  drawDetails(ctx, size, details, BUMP_DETAIL);
  addGrain(ctx, size, 5);
}

function paintRoughness(
  ctx: CanvasRenderingContext2D,
  size: number,
  panels: Panel[],
  subs: SubPanel[],
  details: Detail[],
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

  drawSubPanels(ctx, size, subs, "rough", 0.12, "rgba(255,255,255,0.18)", 0.5 * k);

  // Joints scatter light: a slightly rougher line along every seam breaks up
  // specular highlights exactly where the colour sheet draws its seams.
  for (const p of panels) {
    wrapped(ctx, size, (c) => {
      c.strokeStyle = "rgba(255,255,255,0.28)";
      c.lineWidth = 1 * k;
      c.strokeRect(p.x, p.y, p.w, p.h);
    });
  }

  drawDetails(ctx, size, details, ROUGH_DETAIL);

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
 * layout hierarchy so they stay in register. Deterministic — the same canvases
 * come back on every call — and everything near a canvas edge is drawn again
 * one tile over, so the sheets repeat seamlessly. 2048² by default: the fine
 * details are single-pixel work at 1024 once a tile spans a decent stretch of
 * hull, and the whole build still lands well inside a page-load budget.
 */
export function makeHullMaps(size = 2048): HullMaps {
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
  const { subs, leaves } = buildSubPanels(size, panels, stream(30001));
  const details = buildDetails(size, leaves, stream(50001));
  const streaks = buildStreaks(size, stream(90001));
  const mottles = buildMottles(size, stream(70001));

  paintColor(colorCtx, size, panels, subs, details, streaks, mottles);
  paintBump(bumpCtx, size, panels, subs, details);
  paintRoughness(roughCtx, size, panels, subs, details, streaks);
  return { color, bump, roughness };
}
