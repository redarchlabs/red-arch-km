#!/usr/bin/env python3
"""Upload binary assets into an org's asset store.

An org asset is content a view has to load that belongs to one tenant rather
than to the platform: a 3D model, a texture, a floor plan. It lives in object
storage under the org's own prefix, not in `ui/public/`, so that one customer's
content stays out of everyone else's build and can be replaced without a deploy.

This script is the platform-side tool. It knows how to talk to the endpoint and
nothing about what is being uploaded — the files and their destination paths are
arguments, so tenant content never has to live in this repository.

    # one file
    python3 scripts/upload_asset.py --org "My Org" turbine.glb --as public/models/turbine.glb

    # a directory, keeping its layout under a prefix
    python3 scripts/upload_asset.py --org "My Org" --dir ./models --prefix public/models

Anything under `public/` is readable by an anonymous visitor holding a share
link to one of the org's public views; anything else needs a session. That is
the only access-control knob, so choose the prefix deliberately.

Auth is the dev stack's E2E header bypass by default; set KM2_TEST_USER and
KM2_TEST_SECRET, or point KM2_BASE at another stack.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("KM2_BASE", "http://localhost:8000")
AUTH = {
    "X-Test-User": os.environ.get("KM2_TEST_USER", "siteadmin:siteadmin@example.com"),
    "X-Test-Secret": os.environ.get("KM2_TEST_SECRET", "dev-test-secret-change-me"),
}

# Mirrors api.services.assets.CONTENT_TYPES. The server derives the stored type
# from the path and will reject anything else, so failing here is friendlier
# than a 400 after reading the whole file.
ALLOWED_SUFFIXES = {
    ".stl", ".glb", ".gltf", ".obj", ".mtl",
    ".png", ".jpg", ".jpeg", ".webp", ".svg", ".ktx2", ".bin",
}


class ApiError(RuntimeError):
    pass


def _request(method: str, path: str, *, headers: dict[str, str], body: bytes | None = None):
    req = urllib.request.Request(  # noqa: S310 - KM2_BASE is operator config, not user input
        f"{BASE}{path}", data=body, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as response:  # noqa: S310
            raw = response.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise ApiError(f"{method} {path} -> {exc.code}: {exc.read().decode()[:400]}") from None


def resolve_org(ref: str) -> str:
    """Accept either an org uuid or its name."""
    listing = _request("GET", "/api/orgs/", headers={**AUTH, "Content-Type": "application/json"})
    rows = listing["items"] if isinstance(listing, dict) else (listing or [])
    for org in rows:
        if ref in (org["id"], org["name"]):
            return org["id"]
    raise SystemExit(f"no org matching {ref!r}; have: {', '.join(sorted(o['name'] for o in rows))}")


def _multipart(field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    """Build a multipart/form-data body without pulling in a dependency."""
    boundary = "----km2asset" + os.urandom(12).hex()
    guessed = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    body = b"".join(
        [
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {guessed}\r\n\r\n".encode(),
            data,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
    )
    return body, f"multipart/form-data; boundary={boundary}"


def upload(org_id: str, source: pathlib.Path, dest: str) -> dict:
    data = source.read_bytes()
    body, content_type = _multipart("file", source.name, data)
    # The destination is a path, and each segment has to survive the URL intact.
    quoted = urllib.parse.quote(dest, safe="/")
    return _request(
        "PUT",
        f"/api/assets/{quoted}",
        headers={**AUTH, "X-Org-ID": org_id, "Content-Type": content_type, "Content-Length": str(len(body))},
        body=body,
    )


def _pairs(args: argparse.Namespace) -> list[tuple[pathlib.Path, str]]:
    """Resolve the arguments into (local file, destination path) pairs."""
    prefix = (args.prefix or "").strip("/")

    if args.dir:
        root = pathlib.Path(args.dir)
        if not root.is_dir():
            raise SystemExit(f"{root} is not a directory")
        found = sorted(p for p in root.rglob("*") if p.is_file())
        skipped = [p for p in found if p.suffix.lower() not in ALLOWED_SUFFIXES]
        for p in skipped:
            print(f"skip {p} (unsupported type)", file=sys.stderr)
        return [
            (p, f"{prefix}/{p.relative_to(root).as_posix()}" if prefix else p.relative_to(root).as_posix())
            for p in found
            if p not in skipped
        ]

    if not args.files:
        raise SystemExit("give one or more files, or --dir")
    if args.dest and len(args.files) != 1:
        raise SystemExit("--as names a single destination; use --prefix for several files")

    out = []
    for name in args.files:
        path = pathlib.Path(name)
        if not path.is_file():
            raise SystemExit(f"{path} is not a file")
        dest = args.dest or (f"{prefix}/{path.name}" if prefix else path.name)
        out.append((path, dest))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", help="files to upload")
    parser.add_argument("--org", required=True, help="org uuid or name")
    parser.add_argument("--dir", help="upload every supported file under this directory")
    parser.add_argument("--prefix", help="destination prefix, e.g. public/models")
    parser.add_argument("--as", dest="dest", help="exact destination path for a single file")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    pairs = _pairs(args)
    if args.dry_run:
        for source, dest in pairs:
            print(f"{source} -> {dest}")
        return 0

    org_id = resolve_org(args.org)
    for source, dest in pairs:
        result = upload(org_id, source, dest)
        print(f"{dest}  {result['bytes']:,} bytes  {result['content_type']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ApiError as exc:
        print(exc, file=sys.stderr)
        sys.exit(1)
