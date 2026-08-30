import type { CSSProperties } from "react";

import type { ViewAppearance } from "@/lib/api/forms";

/**
 * Turn a view's appearance block into props for its wrapper element.
 *
 * Colors and the radius become CSS custom properties, so they cascade into the
 * whole subtree and compose with the theme already in effect rather than
 * replacing it. The treatments (surface, button finish, texture, heading case)
 * become `data-` attributes that generic rules in globals.css key off — the
 * stylesheet knows "glass" and "gradient", never which org asked for them.
 *
 * The server validates every value (allow-listed token names, hex-only colors,
 * enums, a bounded radius) before it is stored. This function re-checks colors
 * anyway: it is the last step before the value reaches a `style` attribute, and
 * a view definition can arrive from an import bundle or an older row that
 * predates the validator. A rejected value is dropped, never partially applied.
 */

const HEX_COLOR = /^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$/;

export interface AppearanceProps {
  style?: CSSProperties;
  "data-surface"?: string;
  "data-button-finish"?: string;
  "data-texture"?: string;
  "data-heading-case"?: string;
  "data-frame"?: string;
  "data-nav"?: string;
  /** The bound record's value for `state_field`, once it matched a declared
   * state. Present so a stylesheet (or a test) can see which state is live. */
  "data-view-state"?: string;
}

/** A field value is usable as a state key only if it is a plain scalar: the key
 * is a string comparison, and an object or array has no meaningful spelling. */
function stateKey(value: unknown): string | null {
  if (typeof value === "string") return value;
  if (typeof value === "number" && Number.isFinite(value)) return String(value);
  if (typeof value === "boolean") return String(value);
  return null;
}

export function appearanceProps(
  appearance: ViewAppearance | null | undefined,
  accentColor?: string | null,
  /** The bound record's values, used only to resolve `state_field`. */
  values?: Record<string, unknown> | null
): AppearanceProps {
  const style: Record<string, string> = {};

  // The org accent is the older, narrower hook. Appearance is more specific, so
  // it wins where both name the primary color.
  if (accentColor && HEX_COLOR.test(accentColor)) {
    style["--color-primary"] = accentColor;
  }

  if (!appearance) {
    return Object.keys(style).length ? { style: style as CSSProperties } : {};
  }

  // The state's overrides layer OVER the base, so a view declares its resting
  // look once and each state names only what changes.
  let liveState: string | null = null;
  if (appearance.state_field && appearance.states && values) {
    const key = stateKey(values[appearance.state_field]);
    if (key !== null && Object.prototype.hasOwnProperty.call(appearance.states, key)) {
      liveState = key;
    }
  }
  const colors = {
    ...(appearance.colors ?? {}),
    ...(liveState !== null ? (appearance.states?.[liveState]?.colors ?? {}) : {}),
  };

  for (const [token, color] of Object.entries(colors)) {
    // Token names reach a custom-property name, so they are held to the same
    // shape the server allow-list guarantees: lowercase words and hyphens only.
    if (!/^[a-z]+(?:-[a-z]+)*$/.test(token)) continue;
    if (typeof color !== "string" || !HEX_COLOR.test(color)) continue;
    style[`--color-${token}`] = color;
  }

  if (typeof appearance.radius_px === "number" && Number.isFinite(appearance.radius_px)) {
    // Clamped rather than dropped: an out-of-range radius is a cosmetic mistake,
    // and the nearest legal value is a better outcome than silently ignoring it.
    const px = Math.min(48, Math.max(0, Math.round(appearance.radius_px)));
    style["--view-radius"] = `${px}px`;
  }

  const props: AppearanceProps = {};
  if (Object.keys(style).length) props.style = style as CSSProperties;
  if (appearance.surface) props["data-surface"] = appearance.surface;
  if (appearance.button_finish) props["data-button-finish"] = appearance.button_finish;
  if (appearance.texture) props["data-texture"] = appearance.texture;
  if (appearance.heading_case) props["data-heading-case"] = appearance.heading_case;
  if (appearance.frame) props["data-frame"] = appearance.frame;
  if (appearance.nav) props["data-nav"] = appearance.nav;
  if (liveState !== null) props["data-view-state"] = liveState;
  return props;
}
