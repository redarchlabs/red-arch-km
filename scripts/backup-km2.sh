#!/usr/bin/env bash
#
# Back up km2 (databases + code) and the SALT child-audio corpora to either a
# mounted drive or an SSH host, as a browsable, re-runnable rsync mirror.
#
#   usage: backup-km2.sh <destination> [options]
#
#     destination:  /media/jblair/DRIVE          local path
#                   jblair@192.168.0.100:/srv    ssh target
#                   backup-1:/srv/backups        ssh alias from ~/.ssh/config
#
#   --dry-run        show what would be copied; no writes, no container stops
#   --no-stop        don't stop containers for volume snapshots (faster, but
#                    volume tarballs may be torn if the stores are being written)
#   --mirror-delete  pass --delete to rsync so removed files vanish from the
#                    backup too (off by default: backups are additive)
#   --skip-audio     databases + code only
#   --archive-clips  pack outputs/train/*/clips into per-corpus tarballs instead
#                    of copying 227k individual files. Worth it for USB targets;
#                    usually unnecessary over LAN to a real filesystem.
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

# volume:label:container-to-stop-for-consistency
VOLUMES=(
    "docker_postgres-data:postgres-volume:km2_postgres"
    "docker_neo4j-data:neo4j:km2_neo4j"
    "docker_qdrant-data:qdrant:km2_qdrant"
    "docker_minio-data:minio:km2_minio"
    "docker_redis-data:redis:km2_redis"
    "docker_keycloak-data:keycloak:"
    "red-arch-km_postgres-data:legacy-postgres:"
)

# Build artifacts and virtualenvs: regenerable, and ~6.5G of dead weight.
EXCLUDES=(
    ".venv" ".venv-*" "node_modules" "__pycache__"
    ".mypy_cache" ".pytest_cache" ".ruff_cache" ".playwright-mcp"
)

CLIPS_PARENT="/home/jblair/github/salt-prompt-tester/outputs/train"

DRY_RUN=0; NO_STOP=0; MIRROR_DELETE=0; SKIP_AUDIO=0; ARCHIVE_CLIPS=0
REMOTE=""   # "user@host" when the destination is over ssh

# Reuse one connection for the many small ssh calls below.
SSH_OPTS=(-o ControlMaster=auto -o ControlPath=/tmp/.km2bk-%r@%h:%p -o ControlPersist=120)

# ------------------------------------------------------------- utilities ----

log()  { printf '\033[1;34m[%s]\033[0m %s\n' "$(date +%H:%M:%S)" "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "$1"; }

# --- destination abstraction: identical call sites for local and ssh ---------

d_sh()    { if [[ -n "$REMOTE" ]]; then ssh "${SSH_OPTS[@]}" "$REMOTE" "$1"; else bash -c "$1"; fi; }
d_mkdir() { d_sh "mkdir -p '$1'"; }
d_put()   { if [[ -n "$REMOTE" ]]; then ssh "${SSH_OPTS[@]}" "$REMOTE" "cat > '$1'"; else cat > "$1"; fi; }
d_target(){ if [[ -n "$REMOTE" ]]; then printf '%s:%s' "$REMOTE" "$1"; else printf '%s' "$1"; fi; }
d_avail() { d_sh "df -B1 --output=avail '$1' | tail -1 | tr -d ' '"; }

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

# du honouring the exclude list, in bytes
tree_bytes() {
    local args=()
    for e in "${EXCLUDES[@]}"; do args+=(--exclude="$e"); done
    # du exits non-zero on any unreadable entry but still prints a total; without
    # the `|| true` pipefail turns that into a silent abort of the whole script
    du -sb "${args[@]}" "$1" 2>/dev/null | cut -f1 || true
}

# --------------------------------------------------------------- preflight --

parse_args() {
    [[ $# -ge 1 ]] || die "usage: $(basename "$0") <dest-path | [user@]host:/path> [--dry-run] [--no-stop] [--mirror-delete] [--skip-audio] [--archive-clips]"
    local target="$1"; shift
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

    # host:/path means ssh; a bare path (or one starting with / or .) is local
    if [[ "$target" == *:* && "$target" != /* && "$target" != .* ]]; then
        REMOTE="${target%%:*}"
        DEST="${target#*:}"
        DEST="${DEST%/}/km2-backup"
    else
        DEST="${target%/}/km2-backup"
    fi
}

# Non-POSIX filesystems (exFAT/NTFS) can't store unix perms or hardlinks, which
# also makes rsync re-copy everything on every run.
set_rsync_opts() {
    local fs="ext"
    [[ -z "$REMOTE" ]] && fs="$(stat -f -c %T "$(dirname "$DEST")")"
    case "$fs" in
        exfat|msdos|vfat|fuseblk|ntfs)
            warn "destination is $fs — permissions and symlinks will not be preserved"
            RSYNC_OPTS=(-rlt --modify-window=1 --no-perms --no-owner --no-group) ;;
        *)
            RSYNC_OPTS=(-aH) ;;
    esac
    RSYNC_OPTS+=(--partial --human-readable --info=progress2)
    [[ -n "$REMOTE" ]] && RSYNC_OPTS+=(-e "ssh ${SSH_OPTS[*]}")
    [[ $MIRROR_DELETE -eq 1 ]] && RSYNC_OPTS+=(--delete)
    [[ $DRY_RUN -eq 1 ]] && RSYNC_OPTS+=(--dry-run)
    for e in "${EXCLUDES[@]}"; do RSYNC_OPTS+=(--exclude="$e"); done
    [[ $ARCHIVE_CLIPS -eq 1 ]] && RSYNC_OPTS+=(--exclude="/outputs/train/*/clips/")
    # trailing `[[ ]] &&` that evaluates false would return non-zero and, under
    # `set -e`, abort the caller with no diagnostic at all
    return 0
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
    need=$((need + 2 * 1024 * 1024 * 1024))   # volumes, uncompressed: deliberate over-estimate

    local base avail
    base="$(dirname "$DEST")"
    avail="$(d_avail "$base" 2>/dev/null || true)"
    [[ "$avail" =~ ^[0-9]+$ ]] || die "could not read free space at $base"
    log "estimated backup size: $(human "$need")"
    log "available at dest:     $(human "$avail")"
    (( avail > need )) || die "not enough free space at $base (need ~$(human "$need"), have $(human "$avail"))"
}

preflight() {
    command -v rsync >/dev/null || die "rsync not found"
    command -v zstd  >/dev/null || die "zstd not found"
    command -v docker >/dev/null || die "docker not found"
    [[ -d "$KM2_REPO" ]] || die "km2 repo not found: $KM2_REPO"

    local base; base="$(dirname "$DEST")"

    if [[ -n "$REMOTE" ]]; then
        log "destination: $REMOTE:$DEST (ssh)"
        ssh "${SSH_OPTS[@]}" -o BatchMode=yes -o ConnectTimeout=8 "$REMOTE" true 2>/dev/null \
            || die "cannot reach $REMOTE over ssh with key auth (try: ssh-copy-id $REMOTE)"
        d_sh "command -v rsync >/dev/null" 2>/dev/null || die "rsync not installed on $REMOTE"
        d_sh "command -v zstd  >/dev/null" 2>/dev/null || die "zstd not installed on $REMOTE"
        d_sh "[ -d '$base' ]" 2>/dev/null || die "no such directory on $REMOTE: $base
  create it:  ssh $REMOTE \"sudo mkdir -p $base && sudo chown \\\$(id -un):\\\$(id -gn) $base\""
        d_sh "[ -w '$base' ]" 2>/dev/null || die "$base exists on $REMOTE but is not writable by your user"
    else
        log "destination: $DEST (local)"
        [[ -d "$base" ]] || die "destination path does not exist: $base"
        [[ -w "$base" ]] || die "destination not writable: $base"
    fi

    set_rsync_opts
    estimate_and_check_space
    [[ $DRY_RUN -eq 0 ]] && d_mkdir "$DEST/databases" && d_mkdir "$DEST/code" && d_mkdir "$DEST/audio"
    return 0
}

# -------------------------------------------------------------- databases ---

# Stream a docker volume out as a zstd tarball. tar runs as root inside the
# container, but the destination file is created by the receiving shell, so
# nothing lands root-owned and no sudo is needed anywhere.
dump_volume() {
    local vol="$1" out="$2"
    docker volume inspect "$vol" >/dev/null 2>&1 || { warn "volume $vol not found, skipping"; return 0; }
    docker run --rm --log-driver=none -v "$vol":/data:ro alpine tar -C /data -cf - . \
        | zstd -T0 -3 -q -c \
        | d_put "$out"
}

backup_postgres_logical() {
    local out="$DEST/databases/postgres"
    d_mkdir "$out"
    docker ps --format '{{.Names}}' | grep -qx "$PG_CONTAINER" || {
        warn "$PG_CONTAINER not running — skipping logical dump (volume snapshot still taken)"
        return 0
    }
    log "pg_dump: logical dump of km2 postgres"
    docker exec "$PG_CONTAINER" sh -c 'pg_dumpall -U "$POSTGRES_USER" --globals-only' | d_put "$out/globals.sql"
    docker exec "$PG_CONTAINER" sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' | d_put "$out/redarch_km.dump"
    docker exec "$PG_CONTAINER" sh -c 'echo "$POSTGRES_DB"' | d_put "$out/DBNAME.txt"
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

# ------------------------------------------------------------- code/audio ---

backup_repo() {
    local src="$1" dest_parent="$2" name tmp
    name="$(basename "$src")"
    [[ -d "$src" ]] || { warn "missing: $src"; return 0; }

    [[ $DRY_RUN -eq 0 ]] && d_mkdir "$dest_parent/$name"
    log "rsync: $name"
    rsync "${RSYNC_OPTS[@]}" "$src/" "$(d_target "$dest_parent/$name")/"

    # A git bundle captures full history in one file, independent of the working
    # tree and of whether the remote still exists.
    if [[ $DRY_RUN -eq 0 && -d "$src/.git" ]]; then
        log "git bundle: $name"
        tmp="$(mktemp -d)"
        if git -C "$src" bundle create "$tmp/b.bundle" --all >/dev/null 2>&1; then
            d_put "$dest_parent/${name}.gitbundle" < "$tmp/b.bundle"
        else
            warn "git bundle failed for $name"
        fi
        rm -rf "$tmp"
    fi
}

backup_code() { log "=== code ==="; backup_repo "$KM2_REPO" "$DEST/code"; }

archive_clips() {
    local out="$DEST/audio/derived-clips" corpus d
    [[ -d "$CLIPS_PARENT" ]] || { warn "no clips parent: $CLIPS_PARENT"; return 0; }
    d_mkdir "$out"
    for d in "$CLIPS_PARENT"/*/clips; do
        [[ -d "$d" ]] || continue
        corpus="$(basename "$(dirname "$d")")"
        log "archive: $corpus clips"
        tar -C "$(dirname "$d")" -cf - clips | zstd -T0 -3 -q -c | d_put "$out/${corpus}-clips.tar.zst"
    done
    printf '%s\n' \
        'Derived clips, packed per corpus to avoid ~227k small-file writes.' \
        '' \
        'Extract:  zstd -dc <corpus>-clips.tar.zst | tar -C <dest> -xf -' \
        '' \
        'Sliced from samples/phase2/audio/ by scripts/slice_audio_cha.py and fully' \
        'regenerable; the source recordings under samples/ are authoritative.' \
        | d_put "$out/README.txt"
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
        echo "created: $(date -Is)"
        echo "source:  $(hostname)"
        echo "dest:    ${REMOTE:+$REMOTE:}$DEST"
        echo
        echo "== git heads =="
        for r in "$KM2_REPO" "${AUDIO_REPOS[@]}"; do
            [[ -d "$r/.git" ]] || continue
            printf '%s  %s\n' "$(basename "$r")" "$(git -C "$r" log --oneline -1 2>/dev/null)"
        done
        echo
        echo "== image versions at backup time =="
        echo "postgres:18  neo4j:5.25.1  qdrant/qdrant:v1.12.4"
        echo "minio RELEASE.2024-10-13T13-34-11Z  redis:7.4-alpine"
    } | d_put "$DEST/MANIFEST.txt"

    d_sh "du -sh '$DEST'/databases '$DEST'/code '$DEST'/audio 2>/dev/null" || true
}

write_restore_doc() {
    [[ $DRY_RUN -eq 1 ]] && return 0
    d_put "$DEST/RESTORE.md" <<'EOF'
# Restoring this backup

## Postgres (logical — preferred)

```bash
cat databases/postgres/globals.sql | docker exec -i km2_postgres psql -U "$POSTGRES_USER" -d postgres
docker exec -i km2_postgres pg_restore -U "$POSTGRES_USER" \
    -d "$(cat databases/postgres/DBNAME.txt)" --clean --if-exists \
    < databases/postgres/redarch_km.dump
```

## Any volume (byte-exact)

Volume tarballs restore the on-disk format directly, so the container image
version must match the one recorded in MANIFEST.txt:

```bash
docker volume create docker_neo4j-data
zstd -dc databases/neo4j.tar.zst \
  | docker run --rm -i -v docker_neo4j-data:/data alpine tar -C /data -xf -
```

## Code

`code/red-arch-km-2/` is a plain working tree. The `.gitbundle` holds full
history and clones standalone:

```bash
git clone red-arch-km-2.gitbundle red-arch-km-2
```

Virtualenvs and `node_modules` were excluded — rebuild with `uv sync` / `npm install`.

## Audio

Plain trees. If `audio/derived-clips/` exists, the per-corpus tarballs replace
`outputs/train/*/clips`; they are regenerable from `samples/phase2/audio/` via
`scripts/slice_audio_cha.py`.

## Note

`.env` files are included so the stack restores as-is — this backup contains
live credentials alongside child speech recordings.
EOF
}

main() {
    parse_args "$@"
    preflight
    backup_databases
    backup_code
    backup_audio
    write_manifest
    write_restore_doc
    log "done -> ${REMOTE:+$REMOTE:}$DEST"
}

main "$@"
