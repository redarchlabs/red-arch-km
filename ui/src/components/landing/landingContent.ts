/**
 * Copy for the public landing page hero. Re-authored from the original Knowledge
 * Manager's home page ("Welcome to Your Organizational Source of Truth"), adapted
 * to km-2's actual feature framing. Feature cards below the hero are driven by the
 * shared PUBLIC_FEATURES list (see ../help/publicHelpContent.ts).
 */
export const LANDING_HERO = {
  eyebrow: "Red Arch Knowledge Manager",
  heading: "Your organization's source of truth.",
  subheading:
    "Centralize, secure, and grow your organization's knowledge in one place. Powerful tools for organizing documents and delivering intelligent search and chat mean your team can find the right information at the right time — while fine-grained controls by region, department, role, and group ensure each person only sees what they're authorized to.",
  primaryCta: { label: "Get started", href: "/sign-up" },
  secondaryCta: { label: "Sign in", href: "/login" },
} as const;
