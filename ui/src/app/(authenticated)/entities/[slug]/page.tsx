"use client";

import { ArrowLeft } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { EntityRecordsTable } from "@/components/entities/EntityRecordsTable";
import { EntitySchemaEditor } from "@/components/entities/EntitySchemaEditor";
import { Skeleton } from "@/components/ui/skeleton";
import {
  getEntity,
  listEntities,
  listRelationships,
  type EntityDefinition,
  type EntityRelationship,
} from "@/lib/api/entities";
import { getApiErrorMessage } from "@/lib/api/errors";

export default function EntityDetailPage() {
  const params = useParams<{ slug: string }>();
  const slug = params.slug;

  const [entity, setEntity] = useState<EntityDefinition | null>(null);
  const [relationships, setRelationships] = useState<EntityRelationship[]>([]);
  const [allEntities, setAllEntities] = useState<EntityDefinition[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      // Resolve the entity by slug from the list (no get-by-slug endpoint),
      // then hydrate its fields + relationships by id.
      const all = await listEntities();
      setAllEntities(all);
      const match = all.find((e) => e.slug === slug);
      if (!match) {
        setError("Entity not found");
        setEntity(null);
        return;
      }
      const [full, rels] = await Promise.all([getEntity(match.id), listRelationships(match.id)]);
      setEntity(full);
      setRelationships(rels);
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, "Failed to load entity"));
    } finally {
      setIsLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-3">
        <Link href="/entities" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <div>
          <h1 className="text-2xl font-semibold">{entity?.name ?? slug}</h1>
          <p className="text-sm text-muted-foreground">Schema and records</p>
        </div>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      {isLoading ? (
        <Skeleton className="h-64 w-full" />
      ) : entity ? (
        <>
          <EntitySchemaEditor
            entity={entity}
            relationships={relationships}
            allEntities={allEntities}
            onChange={() => void load()}
          />
          <EntityRecordsTable entity={entity} />
        </>
      ) : null}
    </div>
  );
}
