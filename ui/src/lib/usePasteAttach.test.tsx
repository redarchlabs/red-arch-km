import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const uploadDocument = vi.fn();
vi.mock("@/lib/api/documents", () => ({
  uploadDocument: (...a: unknown[]) => uploadDocument(...a),
}));

import { MAX_ATTACHMENTS, usePasteAttach } from "./usePasteAttach";

const file = (name = "shot.png", type = "image/png") =>
  new File([new Uint8Array([1, 2, 3])], name, { type });

function pasteEvent(files: File[]) {
  return { clipboardData: { files }, preventDefault: vi.fn() } as never;
}

beforeEach(() => {
  vi.clearAllMocks();
  uploadDocument.mockResolvedValue({ documents: [{ id: "doc-1" }], skipped: [] });
  // jsdom has no object URLs.
  globalThis.URL.createObjectURL = vi.fn(() => "blob:preview");
  globalThis.URL.revokeObjectURL = vi.fn();
});

describe("usePasteAttach", () => {
  it("uploads on paste, not on send", async () => {
    // A screenshot takes a moment to store and OCR. Doing it while someone is
    // still typing means the message goes the instant they press send.
    const { result } = renderHook(() => usePasteAttach());

    await act(async () => result.current.onPaste(pasteEvent([file()])));

    await waitFor(() => expect(result.current.documentIds).toEqual(["doc-1"]));
    expect(uploadDocument).toHaveBeenCalled();
  });

  it("leaves ordinary text pasting alone", () => {
    // preventDefault on a text paste would stop the characters reaching the box.
    const { result } = renderHook(() => usePasteAttach());
    const event = pasteEvent([]);

    act(() => result.current.onPaste(event));

    expect((event as unknown as { preventDefault: () => void }).preventDefault).not.toHaveBeenCalled();
  });

  it("never reports an id for something still uploading", async () => {
    // A message must not claim an attachment the server does not have yet.
    let settle: (v: unknown) => void = () => {};
    uploadDocument.mockReturnValue(new Promise((r) => (settle = r)));
    const { result } = renderHook(() => usePasteAttach());

    act(() => result.current.onPaste(pasteEvent([file()])));

    await waitFor(() => expect(result.current.attachments).toHaveLength(1));
    expect(result.current.documentIds).toEqual([]);
    await act(async () => settle({ documents: [{ id: "doc-9" }], skipped: [] }));
    await waitFor(() => expect(result.current.documentIds).toEqual(["doc-9"]));
  });

  it("keeps a failed upload visible instead of dropping it", async () => {
    // Silently vanishing is worse: you would send the message believing the
    // screenshot went with it.
    uploadDocument.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => usePasteAttach());

    await act(async () => result.current.onPaste(pasteEvent([file()])));

    await waitFor(() => expect(result.current.attachments[0].error).toBe("Upload failed"));
    expect(result.current.documentIds).toEqual([]);
  });

  it("stops at the cap", async () => {
    const { result } = renderHook(() => usePasteAttach());

    await act(async () => result.current.onPaste(pasteEvent(Array.from({ length: 9 }, () => file()))));

    await waitFor(() => expect(result.current.attachments).toHaveLength(MAX_ATTACHMENTS));
    expect(result.current.full).toBe(true);
  });

  it("gives a pasted screenshot a distinguishable name", async () => {
    // Every screenshot ever pasted arrives as "image.png"; a work order full of
    // them is unreadable a week later.
    const { result } = renderHook(() => usePasteAttach());

    await act(async () => result.current.onPaste(pasteEvent([file("image.png")])));

    await waitFor(() => expect(result.current.attachments[0].name).toMatch(/^pasted-.*\.png$/));
  });

  it("keeps a real filename", async () => {
    const { result } = renderHook(() => usePasteAttach());

    await act(async () => result.current.onPaste(pasteEvent([file("q3-report.pdf", "application/pdf")])));

    await waitFor(() => expect(result.current.attachments[0].name).toBe("q3-report.pdf"));
  });
});
