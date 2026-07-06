export interface ActionConfigField {
  key: string;
  label: string;
  type: "text" | "textarea" | "json" | "entity" | "form" | "trigger_field";
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
  send_form: "Email an intake form for the changed record",
  send_email: "Send an email",
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
  send_form: [
    { key: "form_id", label: "Form to send", type: "form" },
    {
      key: "recipient_field",
      label: "Email address from field",
      type: "trigger_field",
      help: "Which field on the changed record holds the recipient's email.",
    },
  ],
  send_email: [
    {
      key: "to",
      label: "To",
      type: "text",
      placeholder: "person@example.com or {{after.email}}",
      help: "A literal address, or {{after.<field>}} to pull it from the changed record.",
    },
    { key: "subject", label: "Subject", type: "text", placeholder: "Your request {{after.name}}" },
    {
      key: "body",
      label: "Message",
      type: "textarea",
      placeholder: "Hi {{after.name}},\n\nYour status is now {{after.status}}.",
      help: "Use {{after.<field>}} / {{before.<field>}} to insert record values.",
    },
  ],
  send_webhook: [
    { key: "url", label: "Webhook URL", type: "text", placeholder: "https://example.com/hook" },
    { key: "body", label: "Extra body (JSON)", type: "json", placeholder: "{ }" },
  ],
  log: [{ key: "message", label: "Message", type: "text", placeholder: "note" }],
};

export const ACTION_TYPES = Object.keys(ACTION_LABELS);
