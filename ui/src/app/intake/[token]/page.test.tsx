import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PublicForm } from "@/lib/api/forms";

import IntakeFormPage from "./page";

// The page reads its route params with React 19's `use()`. The test toolchain
// resolves React 18 (no `use`), so polyfill it to synchronously unwrap the
// (already-resolved) params object we pass in.
vi.mock("react", async () => {
  const actual = await vi.importActual<typeof import("react")>("react");
  return { ...actual, use: <T,>(v: T): T => v };
});

const getPublicForm = vi.fn();
const submitPublicForm = vi.fn();

vi.mock("@/lib/api/forms", () => ({
  getPublicForm: (...a: unknown[]) => getPublicForm(...a),
  submitPublicForm: (...a: unknown[]) => submitPublicForm(...a),
}));

function makeForm(overrides: Partial<PublicForm> = {}): PublicForm {
  return {
    form_name: "Client Intake",
    description: "Tell us about you",
    fields: [
      { slug: "full_name", label: "Full name", field_type: "text", required: true, help_text: null, options: [] },
      { slug: "notes", label: "Notes", field_type: "long_text", required: false, help_text: "optional", options: [] },
    ],
    values: {},
    sections: [],
    status: "pending",
    ...overrides,
  };
}

function renderPage(token = "tok123") {
  // `use()` is polyfilled to return its argument, so pass the resolved object.
  return render(
    <IntakeFormPage params={{ token } as unknown as Promise<{ token: string }>} />,
  );
}

beforeEach(() => {
  getPublicForm.mockReset();
  submitPublicForm.mockReset();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("IntakeFormPage happy path", () => {
  it("loads the form by token and submits the entered values", async () => {
    getPublicForm.mockResolvedValue(makeForm());
    submitPublicForm.mockResolvedValue(undefined);

    renderPage("tok123");

    expect(await screen.findByText("Client Intake")).toBeTruthy();
    expect(getPublicForm).toHaveBeenCalledWith("tok123");

    const [fullName] = screen.getAllByRole("textbox");
    fireEvent.change(fullName!, { target: { value: "Jane Doe" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));

    await waitFor(() =>
      expect(submitPublicForm).toHaveBeenCalledWith("tok123", {
        values: { full_name: "Jane Doe" },
        sections: {},
      }),
    );
    // Confirmation replaces the form on success.
    expect(await screen.findByText("Thank you")).toBeTruthy();
  });
});

describe("IntakeFormPage validation affordances", () => {
  it("marks required fields and sets the native required attribute", async () => {
    getPublicForm.mockResolvedValue(makeForm());
    renderPage();

    await screen.findByText("Client Intake");

    // The required marker is shown for full_name, not for the optional notes.
    expect(screen.getByText("*")).toBeTruthy();
    const [fullName, notes] = screen.getAllByRole("textbox");
    expect(fullName!.hasAttribute("required")).toBe(true);
    expect(notes!.hasAttribute("required")).toBe(false);
  });

  it("surfaces a server-side validation error on submit", async () => {
    getPublicForm.mockResolvedValue(makeForm());
    submitPublicForm.mockRejectedValue(new Error("full_name is required"));

    renderPage();
    await screen.findByText("Client Intake");

    // Fill the required field so native validation lets the submit through to
    // the server, which is the branch under test (server-side rejection).
    const [fullName] = screen.getAllByRole("textbox");
    fireEvent.change(fullName!, { target: { value: "Jane Doe" } });
    fireEvent.click(screen.getByRole("button", { name: "Submit" }));

    expect(await screen.findByText("full_name is required")).toBeTruthy();
    // The form is still present (not replaced by the thank-you notice).
    expect(screen.queryByText("Thank you")).toBeNull();
  });
});

describe("IntakeFormPage non-happy states", () => {
  it("shows an unavailable notice when the form fails to load", async () => {
    getPublicForm.mockRejectedValue(new Error("Link expired"));
    renderPage();

    expect(await screen.findByText("This form isn't available")).toBeTruthy();
    expect(screen.getByText("Link expired")).toBeTruthy();
  });

  it("shows an already-submitted notice for a non-pending link", async () => {
    getPublicForm.mockResolvedValue(makeForm({ status: "submitted" }));
    renderPage();

    expect(await screen.findByText("Already submitted")).toBeTruthy();
  });
});
