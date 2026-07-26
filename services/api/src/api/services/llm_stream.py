"""Stream ONE user-visible field out of a structured (JSON-schema) LLM response.

``llm_respond`` and ``llm_decide`` ask the model for strict JSON — a persona's
``reply`` plus a coach tip, a robot's ``say`` plus gesture/mood. Forwarding their
raw token deltas to a viewer would paint ``{"reply":"Hel``, and the fields that
are NOT speech (coaching, reasoning) would leak into the chat.

So the tokens are accumulated here and, after each chunk, the value-so-far of one
named field is re-read from the partial document; only the newly-added characters
are published. The full raw content is still returned, so the caller parses the
completed JSON exactly as it did before.

Field order matters: a strict ``json_schema`` emits properties in schema order, so
the watched field should be declared first if it is to stream early.
"""

from __future__ import annotations

from typing import Any

from api.services.spoken_summary import DeltaSink, _emit

# JSON's two-character escapes (\uXXXX is handled separately).
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_WHITESPACE = " \t\r\n"


def partial_string_field(buffer: str, field: str) -> str:
    """Value-so-far of a top-level string ``field`` in a possibly-incomplete JSON doc.

    Returns "" until the field's opening quote has arrived. A trailing incomplete
    escape (``"a\\``) stops the scan rather than emitting half a character — the
    rest arrives with the next chunk.
    """
    start = buffer.find(f'"{field}"')
    if start == -1:
        return ""
    i = start + len(field) + 2
    while i < len(buffer) and buffer[i] in _WHITESPACE:
        i += 1
    if i >= len(buffer) or buffer[i] != ":":
        return ""
    i += 1
    while i < len(buffer) and buffer[i] in _WHITESPACE:
        i += 1
    if i >= len(buffer) or buffer[i] != '"':
        return ""

    i += 1
    out: list[str] = []
    while i < len(buffer):
        char = buffer[i]
        if char == '"':
            break  # value complete
        if char != "\\":
            out.append(char)
            i += 1
            continue
        if i + 1 >= len(buffer):
            break  # escape still arriving
        nxt = buffer[i + 1]
        if nxt == "u":
            if i + 5 >= len(buffer):
                break  # \uXXXX still arriving
            try:
                out.append(chr(int(buffer[i + 2 : i + 6], 16)))
            except ValueError:
                break
            i += 6
            continue
        if nxt not in _ESCAPES:
            break  # not valid JSON — stop rather than guess
        out.append(_ESCAPES[nxt])
        i += 2
    return "".join(out)


async def stream_json_content(
    client: Any,
    *,
    field: str,
    on_delta: DeltaSink,
    **kwargs: Any,
) -> str:
    """Run a streaming completion, publishing ``field``'s text as it is written.

    Returns the raw assembled content (the complete JSON document) so the caller
    parses it exactly as in the non-streaming path. Publishing is best-effort: a
    broken sink never breaks the call.
    """
    stream = await client.chat.completions.create(**kwargs, stream=True)
    buffer = ""
    published = 0
    async for chunk in stream:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        piece = getattr(getattr(choices[0], "delta", None), "content", None)
        if not piece:
            continue
        buffer += piece
        value = partial_string_field(buffer, field)
        if len(value) > published:
            await _emit(on_delta, value[published:])
            published = len(value)
    return buffer
