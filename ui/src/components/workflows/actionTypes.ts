export interface ActionConfigField {
  key: string;
  label: string;
  type: "text" | "json" | "entity";
  placeholder?: string;
  help?: string;
  /**
   * For `json` fields: key the value editor to the fields of the entity whose
   * slug is stored at this config key (e.g. `values` keyed to `target_slug`).
   */
  entityFieldsFrom?: string;
}

// Keep in sync with the backend ACTION_REGISTRY
// (services/api/src/api/services/workflow/actions.py). Only actions with a
// registered handler belong here — offering one without a handler produces an
// action that always fails at run time.
export const ACTION_LABELS: Record<string, string> = {
  update_record_field: "Update field on the changed record",
  create_record: "Create a record in another entity",
  send_webhook: "Send a webhook (HTTP POST)",
  log: "Log a message (no side effect)",
};

/** The config inputs the inspector renders for each action type. */
export const ACTION_CONFIG_FIELDS: Record<string, ActionConfigField[]> = {
  update_record_field: [
    { key: "field", label: "Field slug", type: "text", placeholder: "status" },
    { key: "value", label: "New value", type: "text", placeholder: "closed" },
  ],
  create_record: [
    { key: "target_slug", label: "Target entity", type: "entity", placeholder: "task" },
    {
      key: "values",
      label: "Values",
      type: "json",
      entityFieldsFrom: "target_slug",
      placeholder: '{ "title": "Follow up" }',
    },
  ],
  send_webhook: [
    { key: "url", label: "Webhook URL", type: "text", placeholder: "https://example.com/hook" },
    { key: "body", label: "Extra body (JSON)", type: "json", placeholder: "{ }" },
  ],
  log: [{ key: "message", label: "Message", type: "text", placeholder: "note" }],
};

export const ACTION_TYPES = Object.keys(ACTION_LABELS);
