"use client";

/**
 * FormsPanel — lists the org's intake forms alongside the workflow designer so
 * the author can see which forms exist (the ones a `send_form` action can
 * email) and preview any of them without leaving the canvas.
 */
import { Eye, Pencil } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { FormPreview } from "@/components/forms/FormPreview";
import { Button } from "@/components/ui/button";
import { buttonVariants } from "@/components/ui/button-variants";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { EntityDefinition } from "@/lib/api/entities";
import type { Form } from "@/lib/api/forms";

interface FormsPanelProps {
  forms: Form[];
  entities: EntityDefinition[];
}

export function FormsPanel({ forms, entities }: FormsPanelProps) {
  const [preview, setPreview] = useState<Form | null>(null);

  return (
    <Card>
      <CardContent className="space-y-3 pt-6">
        <div>
          <h2 className="text-sm font-semibold">Forms</h2>
          <p className="text-xs text-muted-foreground">
            Intake forms a “send form” action can email. Preview to see what the
            recipient gets.
          </p>
        </div>

        {forms.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            No forms yet.{" "}
            <Link href="/forms" className="underline hover:text-foreground">
              Create one
            </Link>
            .
          </p>
        ) : (
          <ul className="divide-y rounded-md border">
            {forms.map((form) => {
              const entity = entities.find((e) => e.id === form.entity_definition_id);
              return (
                <li
                  key={form.id}
                  className="flex items-center justify-between gap-2 px-3 py-2"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium">{form.name}</p>
                    <p className="truncate text-xs text-muted-foreground">
                      {entity ? `Collects into ${entity.name}` : "—"}
                      {form.is_active ? "" : " · inactive"}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => setPreview(form)}
                    >
                      <Eye className="h-4 w-4" />
                      Preview
                    </Button>
                    <Link
                      href={`/forms/${form.id}`}
                      aria-label={`Edit ${form.name}`}
                      className={buttonVariants({ variant: "ghost", size: "icon" })}
                    >
                      <Pencil className="h-4 w-4" />
                    </Link>
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>

      <Dialog
        open={preview !== null}
        onClose={() => setPreview(null)}
        className="max-w-xl"
      >
        {preview ? (
          <>
            <DialogHeader>
              <DialogTitle>{preview.name}</DialogTitle>
              {preview.description ? (
                <DialogDescription>{preview.description}</DialogDescription>
              ) : null}
            </DialogHeader>
            <div className="max-h-[70vh] overflow-y-auto pr-1">
              <FormPreview formId={preview.id} />
            </div>
          </>
        ) : null}
      </Dialog>
    </Card>
  );
}
