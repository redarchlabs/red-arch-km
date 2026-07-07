import type { Metadata } from "next";

import { FeatureGrid } from "@/components/landing/FeatureGrid";

export const metadata: Metadata = {
  title: "Features · Red Arch Knowledge Manager",
  description: "Centralize your knowledge, secure it, and ask it anything.",
};

/**
 * Public help/features landing. Lists every feature via the shared FeatureGrid;
 * each card links to its /help/<slug> deep page.
 */
export default function HelpLandingPage() {
  return (
    <div className="mx-auto max-w-6xl px-4 py-12 sm:px-6">
      <div className="mb-10 text-center">
        <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">Features</h1>
        <p className="mt-3 text-lg text-muted-foreground">
          Centralize your knowledge. Secure it. Ask it anything.
        </p>
      </div>
      <FeatureGrid />
    </div>
  );
}
