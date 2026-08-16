#!/usr/bin/env bash
#
# Back up km2 (databases + code) and the SALT child-audio corpora to an
# external drive as a browsable, re-runnable rsync mirror.
#
#   usage: backup-to-external.sh /media/jblair/<DRIVE> [options]
#
#   --dry-run        show what would be copied; no writes, no container stops
#   --no-stop        don't stop containers for volume snapshots (faster, but
#                    volume tarballs may be torn if the stores are being written)
#   --mirror-delete  pass --delete to rsync so removed files vanish from the
#                    backup too (off by default: backups are additive)
#   --skip-audio     databases + code only
#   --archive-clips  pack outputs/train/*/clips into per-corpus tarballs instead
#                    of copying 227k individual files. Roughly 4x faster onto a
#                    USB flash drive; the clips are derived from samples/phase2
#                    anyway, so they don't need to stay individually browsable.
#
# Restore instructions are written to RESTORE.md inside the backup.

set -Eeuo pipefail

# ---------------------------------------------------------------- config ----

KM2_REPO="/home/jblair/github/redarchlabs/red-arch-km-2"
AUDIO_REPOS=(
    "/home/jblair/github/salt-prompt-tester"
    "/home/jblair/github/salt-decision-queue"
)

PG_CONTAINER="km2_postgres"

# volume -> label. Snapshotted cold (container stopped) unless --no-stop.
VOLUMES=(
    "docker_postgres-data:postgres-volume:km2_postgres"
    "docker_neo4j-data:neo4j:km2_neo4j"
    "docker_qdrant-data:qdrant:km2_qdrant"
    "docker_minio-data:minio:km2_minio"
    "docker_redis-data:redis:km2_redis"
    "docker_keycloak-data:keycloak:"
    "red-arch-km_postgres-data:legacy-postgres:"
)

# Build artifacts and virtualenvs: regenerable, and 6.5G of dead weight.
EXCLUDES=(
    ".venv" ".venv-*" "node_modules" "__pycache__"
    ".mypy_cache" ".pytest_cache" ".ruff_cache" ".playwright-mcp"
)

DRY_RUN=0
NO_STOP=0
MIRROR_DELETE=0
SKIP_AUDIO=0
ARCHIVE_CLIPS=0

# Derived clip trees: ~227k files at a 53KB median. Copied file-by-file these
# dominate wall-clock on any flash-based target.
CLIPS_PARENT="/home/jblair/github/salt-prompt-tester/outputs/train"

# ------------------------------------------------------------- utilities ----

log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

STOPPED=()

restart_stopped() {
    [[ ${#STOPPED[@]} -eq 0 ]] && return 0
    log "restarting containers: ${STOPPED[*]}"
    for c in "${STOPPED[@]}"; do docker start "$c" >/dev/null 2>&1 || warn "could not restart $c"; done
    STOPPED=()
}
trap restart_stopped EXIT

stop_container() {
    local name="$1"
    [[ -z "$name" || $NO_STOP -eq 1 || $DRY_RUN -eq 1 ]] && return 0
    docker ps --format '{{.Names}}' | grep -qx "$name" || return 0
    docker stop "$name" >/dev/null && STOPPED+=("$name")
}

human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "$1"; }

# du that honours the exclude list, in bytes
tree_bytes() {
    local args=()
    for e in "${EXCLUDES[@]}"; do args+=(--exclude="$e"); done
    du -sb "${args[@]}" "$1" 2>/dev/null | cut -f1
}

# --------------------------------------------------------------- preflight --

parse_args() {
    [[ $# -ge 1 ]] || die "usage: $(basename "$0") /path/to/drive [--dry-run] [--no-stop] [--mirror-delete] [--skip-audio]"
    DRIVE="$1"; shift
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --dry-run)       DRY_RUN=1 ;;
            --no-stop)       NO_STOP=1 ;;
            --mirror-delete) MIRROR_DELETE=1 ;;
            --skip-audio)    SKIP_AUDIO=1 ;;
            --archive-clips) ARCHIVE_CLIPS=1 ;;
            *) die "unknown option: $1" ;;
        esac
        shift
    done
    DEST="${DRIVE%/}/km2-backup"
}

# Non-POSIX filesystems (exFAT/NTFS) can't store unix perms or hardlinks;
# rsync -a against them re-copies everything on every run.
set_rsync_opts() {
    local fs; fs="$(stat -f -c %T "$DRIVE")"
    case "$fs" in
        exfat|msdos|vfat|fuseblk|ntfs)
            warn "destination is $fs — permissions and symlinks will not be preserved"
            RSYNC_OPTS=(-rlt --modify-window=1 --no-perms --no-owner --no-group)
            ;;
        *)
            RSYNC_OPTS=(-aH)
            ;;
    esac
    RSYNC_OPTS+=(--partial --human-readable --info=progress2)
    [[ $MIRROR_DELETE -eq 1 ]] && RSYNC_OPTS+=(--delete)
    [[ $DRY_RUN -eq 1 ]] && RSYNC_OPTS+=(--dry-run)
    for e in "${EXCLUDES[@]}"; do RSYNC_OPTS+=(--exclude="$e"); done
    # anchored to the transfer root, so only salt-prompt-tester is affected
    [[ $ARCHIVE_CLIPS -eq 1 ]] && RSYNC_OPTS+=(--exclude="/outputs/train/*/clips/")
}

estimate_and_check_space() {
    local need=0 sz
    sz="$(tree_bytes "$KM2_REPO")"; need=$((need + sz))
    if [[ $SKIP_AUDIO -eq 0 ]]; then
        for r in "${AUDIO_REPOS[@]}"; do
            [[ -d "$r" ]] || continue
            sz="$(tree_bytes "$r")"; need=$((need + sz))
        done
    fi
    # docker volumes, uncompressed, as a deliberate over-estimate
    need=$((need + 2 * 1024 * 1024 * 1024))

    local avail; avail="$(df -B1 --output=avail "$DRIVE" | tail -1 | tr -d ' ')"
    log "estimated backup size: $(human "$need")"
    log "available on drive:    $(human "$avail")"
    if (( avail < need )); then
        die "not enough free space on $DRIVE (need ~$(human "$need"), have $(human "$avail"))"
    fi
}

preflight() {
    command -v docker >/dev/null || die "docker not found"
    command -v rsync  >/dev/null || die "rsync not found"
    command -v zstd   >/dev/null || die "zstd not found"

    [[ -d "$DRIVE" ]] || die "drive path does not exist: $DRIVE"
    mountpoint -q "$DRIVE" 2>/dev/null || warn "$DRIVE is not a mount point — is the external drive attached?"
    [[ -w "$DRIVE" ]] || die "drive is not writable: $DRIVE"
    [[ -d "$KM2_REPO" ]] || die "km2 repo not found: $KM2_REPO"

    set_rsync_opts
    estimate_and_check_space

    if [[ $DRY_RUN -eq 0 ]]; then
        mkdir -p "$DEST"/{databases,code,audio}
    fi
}

# -------------------------------------------------------------- databases ---

# Stream a docker volume out as a zstd tarball owned by the invoking user
# (tar runs as root inside the container; the host shell creates the file).
dump_volume() {
    local vol="$1" out="$2"
    docker volume inspect "$vol" >/dev/null 2>&1 || { warn "volume $vol not found, skipping"; return 0; }
    docker run --rm --log-driver=none -v "$vol":/data:ro alpine tar -C /data -cf - . \
        | zstd -T0 -3 -q -f -o "$out"
}

backup_postgres_logical() {
    local out="$DEST/databases/postgres"
    mkdir -p "$out"
    docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER" || {
        warn "$PG_CONTAINER not running — skipping logical dump (volume snapshot still taken)"
        return 0
    }
    log "pg_dump: logical dump of km2 postgres"
    docker exec "$PG_CONTAINER" sh -c \
        'pg_dumpall -U "$POSTGRES_USER" --globals-only' > "$out/globals.sql"
    docker exec "$PG_CONTAINER" sh -c \
        'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$out/redarch_km.dump"
    docker exec "$PG_CONTAINER" sh -c \
        'echo "$POSTGRES_DB"' > "$out/DBNAME.txt"
}

backup_databases() {
    log "=== databases ==="
    if [[ $DRY_RUN -eq 1 ]]; then
        log "(dry run) would dump postgres + ${#VOLUMES[@]} docker volumes"
        return 0
    fi

    backup_postgres_logical

    for entry in "${VOLUMES[@]}"; do
        IFS=: read -r vol label container <<< "$entry"
        log "snapshot: $label ($vol)"
        stop_container "$container"
        dump_volume "$vol" "$DEST/databases/${label}.tar.zst"
        restart_stopped
    done
}

# ------------------------------------------------------------------- code ---

backup_repo() {
    local src="$1" dest_parent="$2" name
    name="$(basename "$src")"
    [[ -d "$src" ]] || { warn "missing: $src"; return 0; }

    log "rsync: $name -> $dest_parent/$name"
    rsync "${RSYNC_OPTS[@]}" "$src/" "$dest_parent/$name/"

    # A git bundle captures full history in one file, independent of the
    # working tree and of whether the remote still exists.
    if [[ $DRY_RUN -eq 0 && -d "$src/.git" ]]; then
        log "git bundle: $name"
        git -C "$src" bundle create "$dest_parent/${name}.gitbundle" --all >/dev/null 2>&1 \
            || warn "git bundle failed for $name"
    fi
}

backup_code()  { log "=== code ==="; backup_repo "$KM2_REPO" "$DEST/code"; }

archive_clips() {
    local out="$DEST/audio/derived-clips" corpus d
    [[ -d "$CLIPS_PARENT" ]] || { warn "no clips parent: $CLIPS_PARENT"; return 0; }
    mkdir -p "$out"
    for d in "$CLIPS_PARENT"/*/clips; do
        [[ -d "$d" ]] || continue
        corpus="$(basename "$(dirname "$d")")"
        log "archive: $corpus clips -> ${corpus}-clips.tar.zst"
        tar -C "$(dirname "$d")" -cf - clips \
            | zstd -T0 -3 -q -f -o "$out/${corpus}-clips.tar.zst"
    done
    cat > "$out/README.txt" <<EOF
Derived clips, packed per corpus to avoid ~227k small-file writes.

Extract:  zstd -dc <corpus>-clips.tar.zst | tar -C <dest> -xf -

These are sliced from samples/phase2/audio/ by scripts/slice_audio_cha.py and
are regenerable; the source recordings in audio/salt-prompt-tester/samples/ are
the authoritative copy.
EOF
}

backup_audio() {
    [[ $SKIP_AUDIO -eq 1 ]] && { log "=== audio (skipped) ==="; return 0; }
    log "=== child audio corpora ==="
    for r in "${AUDIO_REPOS[@]}"; do backup_repo "$r" "$DEST/audio"; done
    [[ $ARCHIVE_CLIPS -eq 1 && $DRY_RUN -eq 0 ]] && archive_clips
    return 0
}

# --------------------------------------------------------------- manifest ---

write_manifest() {
    [[ $DRY_RUN -eq 1 ]] && return 0
    log "=== manifest ==="
    {
        echo "km2 backup"
        echo "created:  $(date -Is)"
        echo "host:     $(hostname)"
        echo "source:   $KM2_REPO"
        echo
        echo "== contents =="
        du -sh "$DEST"/databases "$DEST"/code "$DEST"/audio 2>/dev/null
        echo
        echo "== database artifacts =="
        ls -lh "$DEST"/databases 2>/dev/null
        echo
        echo "== git heads =="
        for r in "$KM2_REPO" "${AUDIO_REPOS[@]}"; do
            [[ -d "$r/.git" ]] || continue
            printf '%s  %s\n' "$(basename "$r")" "$(git -C "$r" log --oneline -1 2>/dev/null)"
        done
    } > "$DEST/MANIFEST.txt"
    cat "$DEST/MANIFEST.txt"
}

write_restore_doc() {
    [[ $DRY_RUN -eq 1 ]] && return 0
    cat > "$DEST/RESTORE.md" <<'EOF'
# Restoring this backup

## Postgres (logical — preferred)

```bash
docker compose -f docker/docker-compose.yml up -d postgres
cat databases/postgres/globals.sql | docker exec -i km2_postgres psql -U "$POSTGRES_USER" -d postgres
docker exec -i km2_postgres pg_restore -U "$POSTGRES_USER" -d "$(cat databases/postgres/DBNAME.txt)" \
    --clean --if-exists < databases/postgres/redarch_km.dump
```

## Any volume (byte-exact — postgres-volume, neo4j, qdrant, minio, redis, keycloak, legacy-postgres)

Volume tarballs restore the on-disk format directly, so the container image
version must match the one that produced them (see MANIFEST.txt):

```bash
docker volume create docker_neo4j-data
zstd -dc databases/neo4j.tar.zst \
  | docker run --rm -i -v docker_neo4j-data:/data alpine tar -C /data -xf -
```

Image versions at backup time: postgres:18, neo4j:5.25.1, qdrant/qdrant:v1.12.4,
minio RELEASE.2024-10-13T13-34-11Z, redis:7.4-alpine.

## Code

`code/red-arch-km-2/` is a plain working tree. `code/red-arch-km-2.gitbundle`
holds full history and can be cloned standalone:

```bash
git clone red-arch-km-2.gitbundle red-arch-km-2
```

Virtualenvs and `node_modules` were excluded — rebuild with `uv sync` and `npm install`.

## Audio

`audio/salt-prompt-tester/` and `audio/salt-decision-queue/` are plain trees.
`outputs/train/*/clips` are derived from `samples/phase2/audio/` via
`scripts/slice_audio_cha.py`; the source recordings are the authoritative copy.

## Note

`.env` files are included so the stack is restorable as-is. This backup is
unencrypted — the drive holds child speech recordings and live credentials.
EOF
}

# ------------------------------------------------------------------- main ---

main() {
    parse_args "$@"
    preflight
    backup_databases
    backup_code
    backup_audio
    write_manifest
    write_restore_doc
    log "done -> $DEST"
}

main "$@"
