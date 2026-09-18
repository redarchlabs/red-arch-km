"""Stream ONE user-visible field out of a structured (JSON-schema) LLM response.

``llm_respond`` and ``llm_decide`` ask the model for strict JSON — a persona's
``reply`` plus a coach tip, a robot's ``say`` plus gesture/mood. Forwarding their
raw token deltas to a viewer would paint ``{"reply":"Hel``, and the fields that
are NOT speech (coaching, reasoning) would leak into the chat.

An incremental parser tracks the root property and publishes only newly decoded
characters, retaining nesting and escape state between chunks. The full raw
content is still returned, so the caller parses the completed JSON as before.

Field order matters: a strict ``json_schema`` emits properties in schema order, so
the watched field should be declared first if it is to stream early.
"""

from __future__ import annotations

from typing import Any

from api.services.spoken_summary import DeltaSink, _emit

# JSON's two-character escapes (\uXXXX is handled separately).
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_WHITESPACE = " \t\r\n"


class StringFieldParser:
    """Incrementally decode one root string property, visiting each input character once.

    String/escape state survives arbitrary chunk boundaries. Nested properties and
    property-looking text inside other strings never become the selected field.
    Invalid prefixes stop publication; the caller still validates the final JSON.
    """

    def __init__(self, field: str) -> None:
        self.field = field
        self.depth = 0
        self.phase = "start"
        self.string_role: str | None = None
        self.key: list[str] = []
        self.selected = False
        self.escape: str | None = None
        self.high_surrogate: int | None = None
        self.done = False

    def push(self, piece: str) -> str:
        out: list[str] = []
        for char in piece:
            if self.done:
                break
            if self.string_role is not None:
                self._string_char(char, out)
            elif char in _WHITESPACE:
                continue
            elif self.phase == "start":
                if char != "{":
                    self.done = True
                else:
                    self.depth = 1
                    self.phase = "key"
            elif self.depth == 1 and self.phase == "key":
                if char == '"':
                    self.key = []
                    self.string_role = "key"
                else:
                    self.done = True
            elif self.depth == 1 and self.phase == "colon":
                if char == ":":
                    self.phase = "value"
                else:
                    self.done = True
            elif self.depth == 1 and self.phase == "value":
                self.phase = "skip"
                if self.selected:
                    if char == '"':
                        self.string_role = "selected"
                    else:
                        self.done = True
                else:
                    self._skip_char(char)
            else:
                self._skip_char(char)
        return "".join(out)

    def _skip_char(self, char: str) -> None:
        if char == '"':
            self.string_role = "other"
        elif char in "{[":
            self.depth += 1
        elif char in "}]":
            self.depth -= 1
            if self.depth == 0:
                self.done = True
        elif char == "," and self.depth == 1:
            self.phase = "key"

    def _decoded(self, char: str, out: list[str]) -> None:
        code = ord(char)
        if self.high_surrogate is not None:
            if not 0xDC00 <= code <= 0xDFFF:
                self.done = True
                return
            char = chr(0x10000 + ((self.high_surrogate - 0xD800) << 10) + code - 0xDC00)
            self.high_surrogate = None
        elif 0xD800 <= code <= 0xDBFF:
            self.high_surrogate = code
            return
        elif 0xDC00 <= code <= 0xDFFF:
            self.done = True
            return
        if self.string_role == "key":
            self.key.append(char)
        elif self.string_role == "selected":
            out.append(char)

    def _string_char(self, char: str, out: list[str]) -> None:
        if self.escape is not None:
            if self.escape.startswith("u"):
                if char not in "0123456789abcdefABCDEF":
                    self.done = True
                    return
                self.escape += char
                if len(self.escape) == 5:
                    self._decoded(chr(int(self.escape[1:], 16)), out)
                    self.escape = None
            elif char == "u":
                self.escape = "u"
            elif char in _ESCAPES:
                self._decoded(_ESCAPES[char], out)
                self.escape = None
            else:
                self.done = True
        elif char == "\\":
            self.escape = ""
        elif char == '"':
            if self.high_surrogate is not None or self.string_role == "selected":
                self.done = True
            elif self.string_role == "key":
                self.selected = "".join(self.key) == self.field
                self.phase = "colon"
            self.string_role = None
        elif ord(char) < 0x20:
            self.done = True
        else:
            self._decoded(char, out)


def partial_string_field(buffer: str, field: str) -> str:
    """Value-so-far of a root string property; incomplete escapes are withheld."""
    return StringFieldParser(field).push(buffer)


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
    pieces: list[str] = []
    parser = StringFieldParser(field)
    async for chunk in stream:
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        piece = getattr(getattr(choices[0], "delta", None), "content", None)
        if not piece:
            continue
        pieces.append(piece)
        delta = parser.push(piece)
        if delta:
            await _emit(on_delta, delta)
    return "".join(pieces)
