"use client";

import Link from "next/link";

import { FeatureGrid } from "@/components/landing/FeatureGrid";
import { LANDING_HERO } from "@/components/landing/landingContent";
import { PublicHeader } from "@/components/landing/PublicHeader";
import { buttonVariants } from "@/components/ui/button";

/**
 * Public marketing landing page for logged-out visitors. Rendered by the root
 * route (src/app/page.tsx) only when there is no authenticated Clerk session;
 * signed-in users are redirected to /documents before this ever renders.
 */
export function Landing() {
  return (
    <div className="min-h-screen bg-background text-foreground">
      <PublicHeader />

      <main>
        {/* Hero */}
        <section className="mx-auto max-w-4xl px-4 py-16 text-center sm:px-6 sm:py-24">
          <p className="text-sm font-medium uppercase tracking-wide text-muted-foreground">
            {LANDING_HERO.eyebrow}
          </p>
          <h1 className="mt-3 text-4xl font-bold tracking-tight sm:text-5xl">
            {LANDING_HERO.heading}
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-base text-muted-foreground sm:text-lg">
            {LANDING_HERO.subheading}
          </p>
          <div className="mt-8 flex items-center justify-center gap-3">
            <Link
              href={LANDING_HERO.primaryCta.href}
              className={buttonVariants({ size: "lg" })}
            >
              {LANDING_HERO.primaryCta.label}
            </Link>
            <Link
              href={LANDING_HERO.secondaryCta.href}
              className={buttonVariants({ variant: "outline", size: "lg" })}
            >
              {LANDING_HERO.secondaryCta.label}
            </Link>
          </div>
        </section>

        {/* Features */}
        <section className="mx-auto max-w-6xl px-4 pb-20 sm:px-6">
          <div className="mb-8 text-center">
            <h2 className="text-2xl font-semibold tracking-tight">
              Centralize your knowledge. Secure it. Ask it anything.
            </h2>
            <p className="mt-2 text-sm text-muted-foreground">
              Explore what Red Arch Knowledge Manager brings to your team.
            </p>
          </div>
          <FeatureGrid />
        </section>
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto max-w-6xl px-4 py-6 text-center text-sm text-muted-foreground sm:px-6">
          © Red Arch Labs · <Link href="/help" className="underline underline-offset-4">Features</Link>
        </div>
      </footer>
    </div>
  );
}
