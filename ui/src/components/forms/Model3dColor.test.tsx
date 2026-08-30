import { describe, expect, it } from "vitest";

import { fillColor } from "./Model3dNode";

/**
 * One 3D element serves every record on a station, so a per-record livery — one
 * ship painted gold, another red — can only come from the row. What matters is
 * the guard on the way back: a field holding a typo, an empty string or "none"
 * must cost the tint and nothing else. `new THREE.Color("nonsense")` does not
 * throw; it warns and leaves the material some unrelated colour, which on a dark
 * hull looks like a rendering bug rather than a data one.
 */
describe("fillColor", () => {
  it("passes a literal hex through", () => {
    expect(fillColor("#c2a24e", {})).toBe("#c2a24e");
  });

  it("fills a field token from the bound record", () => {
    expect(fillColor("{accent_hex}", { accent_hex: "#c0392b" })).toBe("#c0392b");
  });

  it("accepts the short hex form", () => {
    expect(fillColor("{accent_hex}", { accent_hex: "#abc" })).toBe("#abc");
  });

  it("returns null when the field is missing, so the default applies", () => {
    expect(fillColor("{accent_hex}", {})).toBeNull();
  });

  it("returns null rather than passing a non-colour to the renderer", () => {
    for (const value of ["none", "", "red", "#12345", "javascript:alert(1)"]) {
      expect(fillColor("{accent_hex}", { accent_hex: value })).toBeNull();
    }
  });

  it("is null for an absent property", () => {
    expect(fillColor(null, {})).toBeNull();
    expect(fillColor(undefined, {})).toBeNull();
  });
});

import { isGltfUrl } from "./Model3dNode";

/**
 * The two formats take completely different paths: a glTF brings its own
 * materials and textures, an STL brings triangles and nothing else. Picking
 * wrong does not error — it loads the file with the wrong parser and shows an
 * empty canvas.
 */
describe("isGltfUrl", () => {
  it("recognises both glTF extensions, either case", () => {
    for (const url of ["/a/ship.glb", "/a/ship.gltf", "/a/SHIP.GLB"]) {
      expect(isGltfUrl(url)).toBe(true);
    }
  });

  it("is false for an STL", () => {
    expect(isGltfUrl("/api/assets/public/ships/TB-10426.stl")).toBe(false);
  });

  it("ignores a query string, so a cache-busted asset still matches", () => {
    expect(isGltfUrl("/api/assets/public/ships/a.glb?v=12345")).toBe(true);
    expect(isGltfUrl("/api/assets/public/ships/a.stl?v=12345")).toBe(false);
  });

  it("is not fooled by the extension appearing elsewhere in the path", () => {
    expect(isGltfUrl("/glb/models/ship.stl")).toBe(false);
  });
});
