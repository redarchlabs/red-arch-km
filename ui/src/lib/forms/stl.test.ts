import { describe, expect, it } from "vitest";

import { parseBinaryStl } from "./stl";

/** Build a binary STL the way a writer would: 80-byte header, uint32 count,
 * then 50 bytes per triangle (normal, three vertices, attribute word). */
function makeStl(triangles: number[][][]): ArrayBuffer {
  const buf = new ArrayBuffer(84 + triangles.length * 50);
  const view = new DataView(buf);
  view.setUint32(80, triangles.length, true);
  let off = 84;
  for (const tri of triangles) {
    for (let i = 0; i < 3; i++) view.setFloat32(off + i * 4, 0, true); // normal
    off += 12;
    for (const v of tri) {
      for (const c of v) {
        view.setFloat32(off, c, true);
        off += 4;
      }
    }
    view.setUint16(off, 0, true);
    off += 2;
  }
  return buf;
}

const UNIT = [
  [
    [0, 0, 0],
    [1, 0, 0],
    [0, 1, 0],
  ],
];

describe("parseBinaryStl", () => {
  it("reads triangle vertices", () => {
    const mesh = parseBinaryStl(makeStl(UNIT));
    expect(mesh.triangles).toHaveLength(1);
    expect(mesh.triangles[0]).toEqual([
      [0, 0, 0],
      [1, 0, 0],
      [0, 1, 0],
    ]);
  });

  it("reports the bounding box and centre", () => {
    const mesh = parseBinaryStl(
      makeStl([
        [
          [-2, -4, -6],
          [2, 4, 6],
          [0, 0, 0],
        ],
      ])
    );
    expect(mesh.min).toEqual([-2, -4, -6]);
    expect(mesh.max).toEqual([2, 4, 6]);
    expect(mesh.center).toEqual([0, 0, 0]);
    // Radius is the half-diagonal, which is what a viewer needs to frame it.
    expect(mesh.radius).toBeCloseTo(Math.sqrt(4 + 16 + 36), 5);
  });

  it("reads many triangles", () => {
    const many = Array.from({ length: 250 }, (_, i) => [
      [i, 0, 0],
      [i + 1, 0, 0],
      [i, 1, 0],
    ]);
    expect(parseBinaryStl(makeStl(many)).triangles).toHaveLength(250);
  });

  describe("malformed input", () => {
    // The URL is author-supplied and the file is fetched at render time, so a
    // truncated or wrong-typed response must not throw into the render tree.
    it("returns an empty mesh for a buffer too short for a header", () => {
      expect(parseBinaryStl(new ArrayBuffer(10)).triangles).toEqual([]);
    });

    it("returns an empty mesh for an empty buffer", () => {
      expect(parseBinaryStl(new ArrayBuffer(0)).triangles).toEqual([]);
    });

    it("stops at the end of a truncated triangle list", () => {
      // Claims three triangles, carries one and a half.
      const full = makeStl([UNIT[0], UNIT[0], UNIT[0]]);
      const truncated = full.slice(0, 84 + 75);
      const mesh = parseBinaryStl(truncated);
      expect(mesh.triangles).toHaveLength(1);
    });

    it("refuses a count larger than the buffer could hold", () => {
      const buf = makeStl(UNIT);
      new DataView(buf).setUint32(80, 10_000_000, true);
      expect(parseBinaryStl(buf).triangles).toHaveLength(1);
    });

    it("rejects an ASCII STL rather than reading its text as floats", () => {
      const ascii = new TextEncoder().encode(
        "solid ship\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nendloop\nendfacet\nendsolid"
      );
      expect(parseBinaryStl(ascii.buffer as ArrayBuffer).triangles).toEqual([]);
    });
  });
});
