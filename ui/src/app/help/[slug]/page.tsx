import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { PublicHelpPage } from "@/components/help/PublicHelpPage";
import { featureBySlug, PUBLIC_FEATURES } from "@/components/help/publicHelpContent";

interface PageProps {
  params: Promise<{ slug: string }>;
}

/** Pre-render every known feature page. */
export function generateStaticParams() {
  return PUBLIC_FEATURES.map((f) => ({ slug: f.slug }));
}

export async function generateMetadata({ params }: PageProps): Promise<Metadata> {
  const { slug } = await params;
  const feature = featureBySlug(slug);
  if (!feature) return { title: "Not found" };
  return {
    title: `${feature.title} · Red Arch Knowledge Manager`,
    description: feature.tagline,
  };
}

export default async function FeatureHelpPage({ params }: PageProps) {
  const { slug } = await params;
  const feature = featureBySlug(slug);
  if (!feature) notFound();
  return <PublicHelpPage feature={feature} />;
}
