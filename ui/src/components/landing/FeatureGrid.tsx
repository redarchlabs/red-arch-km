"use client";

import Link from "next/link";

import { PUBLIC_FEATURES } from "@/components/help/publicHelpContent";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * Responsive grid of feature cards, driven by the canonical PUBLIC_FEATURES
 * list. Each card links to its /help/<slug> deep page; planned features carry a
 * "Coming soon" badge instead of implying the capability ships today.
 */
export function FeatureGrid() {
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {PUBLIC_FEATURES.map((feature) => {
        const Icon = feature.icon;
        return (
          <Link
            key={feature.slug}
            href={`/help/${feature.slug}`}
            className="group rounded-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Card className="h-full transition-colors group-hover:border-primary/40 group-hover:bg-accent/40">
              <CardHeader>
                <div className="flex items-center justify-between gap-2">
                  <span className="inline-flex h-10 w-10 items-center justify-center rounded-md bg-accent text-accent-foreground">
                    <Icon className="h-5 w-5" />
                  </span>
                  {feature.comingSoon ? <Badge variant="secondary">Coming soon</Badge> : null}
                </div>
                <CardTitle className="mt-3 text-base">{feature.title}</CardTitle>
                <CardDescription>{feature.tagline}</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-muted-foreground">{feature.intro}</p>
              </CardContent>
            </Card>
          </Link>
        );
      })}
    </div>
  );
}
