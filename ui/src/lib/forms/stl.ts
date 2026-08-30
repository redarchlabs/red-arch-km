/**
 * Binary STL parsing, kept separate from the renderer so it can be tested
 * without a canvas.
 *
 * The format: 80 bytes of free-text header, a little-endian uint32 triangle
 * count, then 50 bytes per triangle — three floats of normal, nine of vertices,
 * and a uint16 attribute word nobody uses.
 *
 * Every failure returns an empty mesh rather than throwing. The URL is
 * author-supplied and fetched at render time, so a 404 page, a truncated
 * response or an ASCII STL are all things that will happen, and none of them
 * should take down the render tree around them.
 */

export type Vec3 = [number, number, number];
export type Triangle = [Vec3, Vec3, Vec3];

export interface Mesh {
  triangles: Triangle[];
  min: Vec3;
  max: Vec3;
  center: Vec3;
  /** Half the bounding-box diagonal: what a viewer needs to frame the object. */
  radius: number;
}

const EMPTY: Mesh = {
  triangles: [],
  min: [0, 0, 0],
  max: [0, 0, 0],
  center: [0, 0, 0],
  radius: 0,
};

const HEADER_BYTES = 80;
const COUNT_BYTES = 4;
const TRIANGLE_BYTES = 50;

/** An ASCII STL opens with "solid". Reading its text as little-endian floats
 * would produce a cloud of garbage triangles rather than an obvious failure. */
function isAscii(buffer: ArrayBuffer): boolean {
  if (buffer.byteLength < 5) return false;
  const head = new Uint8Array(buffer, 0, 5);
  return String.fromCharCode(...head).toLowerCase() === "solid";
}

export function parseBinaryStl(buffer: ArrayBuffer): Mesh {
  if (!buffer || buffer.byteLength < HEADER_BYTES + COUNT_BYTES) return EMPTY;
  if (isAscii(buffer)) return EMPTY;

  const view = new DataView(buffer);
  const claimed = view.getUint32(HEADER_BYTES, true);
  // Trust the buffer over the count: a truncated download and a corrupt count
  // are the same thing from here, and both are bounded by what actually arrived.
  const available = Math.floor(
    (buffer.byteLength - HEADER_BYTES - COUNT_BYTES) / TRIANGLE_BYTES
  );
  const count = Math.min(claimed, available);
  if (count <= 0) return EMPTY;

  const triangles: Triangle[] = [];
  const min: Vec3 = [Infinity, Infinity, Infinity];
  const max: Vec3 = [-Infinity, -Infinity, -Infinity];

  let off = HEADER_BYTES + COUNT_BYTES;
  for (let t = 0; t < count; t++) {
    off += 12; // the stored normal; recomputed at shade time from the winding
    const verts: Vec3[] = [];
    for (let v = 0; v < 3; v++) {
      const p: Vec3 = [
        view.getFloat32(off, true),
        view.getFloat32(off + 4, true),
        view.getFloat32(off + 8, true),
      ];
      off += 12;
      for (let i = 0; i < 3; i++) {
        if (p[i] < min[i]) min[i] = p[i];
        if (p[i] > max[i]) max[i] = p[i];
      }
      verts.push(p);
    }
    off += 2; // attribute word
    triangles.push(verts as unknown as Triangle);
  }

  const center: Vec3 = [
    (min[0] + max[0]) / 2,
    (min[1] + max[1]) / 2,
    (min[2] + max[2]) / 2,
  ];
  const radius =
    Math.sqrt(
      (max[0] - min[0]) ** 2 + (max[1] - min[1]) ** 2 + (max[2] - min[2]) ** 2
    ) / 2;

  return { triangles, min, max, center, radius };
}
