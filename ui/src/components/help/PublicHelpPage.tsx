import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button-variants";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

import type { PublicFeature } from "./publicHelpContent";

/**
 * Shared renderer for a single public /help/<slug> feature page. Data-driven
 * from PUBLIC_FEATURES so every feature page is one entry in a list, not a
 * hand-written page. Planned features (comingSoon) surface a badge and note
 * rather than implying the capability exists today.
 */
export function PublicHelpPage({ feature }: { feature: PublicFeature }) {
  const Icon = feature.icon;
  return (
    <article className="mx-auto max-w-3xl px-4 py-12 sm:px-6">
      <div className="flex items-center gap-3">
        <span className="inline-flex h-11 w-11 items-center justify-center rounded-md bg-accent text-accent-foreground">
          <Icon className="h-6 w-6" />
        </span>
        <h1 className="text-3xl font-bold tracking-tight">{feature.title}</h1>
        {feature.comingSoon ? <Badge variant="secondary">Coming soon</Badge> : null}
      </div>

      <p className="mt-2 text-lg italic text-muted-foreground">{feature.tagline}</p>
      <p className="mt-6 text-base text-muted-foreground">{feature.intro}</p>

      <div className="mt-10 grid gap-4 sm:grid-cols-2">
        {feature.cards.map((card) => (
          <Card key={card.title} className="h-full">
            <CardHeader>
              <div className="flex items-center justify-between gap-2">
                <CardTitle className="text-base">{card.title}</CardTitle>
                {card.comingSoon ? <Badge variant="secondary">Coming soon</Badge> : null}
              </div>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-muted-foreground">{card.description}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <div className="mt-12 flex items-center gap-3">
        <Link href="/help" className={buttonVariants({ variant: "outline" })}>
          ← All features
        </Link>
        <Link href="/sign-up" className={buttonVariants({})}>
          Get started
        </Link>
      </div>
    </article>
  );
}
