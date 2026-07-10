/**
 * Shared class for a native `<select>` styled to match the shadcn `Input`.
 * There is no shadcn `Select` primitive in this project yet, so the builder
 * editors and filter bars use a plain native select with this class rather than
 * copy-pasting the Tailwind string per file.
 */
export const nativeSelectClass =
  "h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
