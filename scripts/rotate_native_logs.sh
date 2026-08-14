#!/usr/bin/env bash
# Rotate the append-redirect logs that nothing else rotates.
#
# Orion's structured logs (orion.log, orion_errors.log, orion_YYYY-MM-DD.log)
# are already rotated by empire_core's RotatingFileHandler. These are NOT:
#
#   - the wrapper redirects  (`exec python -m orion.main_execution >> execution_native.log`)
#   - the launchd redirects  (StandardOutPath / StandardErrorPath)
#
# Both are plain O_APPEND handles held open for the life of the process, so they
# grow without bound. On 2026-08-13 execution_native.log had reached 6.5 GB and
# the logs dir 20 GB.
#
# ROTATION IS TRUNCATE-IN-PLACE, NOT RENAME. A running service holds an open fd;
# renaming the file leaves it writing to the renamed inode, so the "rotated"
# file keeps growing and the new one stays empty. Copy -> compress -> truncate
# keeps the fd valid and frees the space immediately. Safe to run while every
# service is live; no restart needed.
#
# LOAD-BEARING ASSUMPTION: every target is opened O_APPEND (bash `>>` and
# launchd StandardOutPath both are). A writer holding its own offset instead
# would re-inflate the file as a sparse one on its next write.
#
# DATA-LOSS WINDOW (accepted): bytes appended between gzip reaching EOF and the
# truncate land in neither the archive nor the live file. Milliseconds' worth,
# and these files are stdout/stderr duplicates of the structured logs, which
# remain the canonical record. gzip FAILING never loses anything (the source is
# left intact); only gzip succeeding has this tail window.
#
# Usage:
#   scripts/rotate_native_logs.sh              # rotate + prune
#   scripts/rotate_native_logs.sh --dry-run    # report only, change nothing
#   scripts/rotate_native_logs.sh --self-check # verify the logic, touch nothing real

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ORION_LOG_DIR:-${PROJECT_ROOT}/logs}"

# Rotate once a file exceeds this. Small enough to keep the dir tidy, large
# enough that rotation is rare and a single file still holds useful context.
THRESHOLD_MB="${ROTATE_THRESHOLD_MB:-100}"
# Compressed archives older than this are deleted. These are stdout/stderr
# duplicates of the structured logs, so they are the cheapest thing to lose.
RETENTION_DAYS="${ROTATE_RETENTION_DAYS:-14}"
# Refuse to rotate when free space is under this. copy->compress->truncate needs
# room for the archive BEFORE it frees the source, so a genuinely full disk is
# exactly when this script cannot help — fail loudly instead of silently.
MIN_FREE_MB="${ROTATE_MIN_FREE_MB:-2048}"

# EXPLICIT allowlist. Deliberately not "every *.log": the structlog-managed
# files must never be truncated out from under their own rotating handler.
PATTERNS=(
    "*_native.log"
    "*.stdout.log"
    "*.stderr.log"
    "launchd_health.log"
    "market_open_dataflow_check.log"
    "cron_retrain.log"
)

# This job's OWN stdout/stderr match the allowlist above. Rotating a log while
# writing to it would archive a partial self-referential slice and drop the
# run's own result lines.
EXCLUDE_PREFIX="log_rotation."

DRY_RUN=0
MODE="rotate"
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --self-check) MODE="self-check" ;;
        *) echo "unknown argument: $arg" >&2; exit 2 ;;
    esac
done
# --dry-run would make every self-check post-condition fail for the wrong reason.
if [[ "$MODE" == "self-check" && $DRY_RUN -eq 1 ]]; then
    echo "--self-check and --dry-run are mutually exclusive" >&2
    exit 2
fi

[[ "$THRESHOLD_MB" =~ ^[0-9]+$ ]] || { echo "ROTATE_THRESHOLD_MB must be numeric" >&2; exit 2; }
[[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]] || { echo "ROTATE_RETENTION_DAYS must be numeric" >&2; exit 2; }
[[ "$MIN_FREE_MB" =~ ^[0-9]+$ ]] || { echo "ROTATE_MIN_FREE_MB must be numeric" >&2; exit 2; }
command -v gzip >/dev/null || { echo "gzip is required" >&2; exit 2; }

log_json() {
    printf '{"event_type":"%s","file":"%s","detail":"%s","ts":"%s"}\n' \
        "$1" "$2" "$3" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
}

free_mb() {
    df -m "$1" 2>/dev/null | awk 'NR==2 {print $4}'
}

rotate_dir() {
    local dir="$1"
    local threshold_bytes=$(( THRESHOLD_MB * 1024 * 1024 ))
    local stamp rotated=0 pruned=0 free
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"

    free=$(free_mb "$dir")
    if [[ -n "$free" ]] && (( free < MIN_FREE_MB )) && (( DRY_RUN == 0 )); then
        # The one case this script exists for is also the one where it cannot
        # work: it needs space for the archive before it can free the source.
        log_json "ROTATE_ABORTED_LOW_DISK" "$dir" "${free}MB free < ${MIN_FREE_MB}MB required"
        return 1
    fi

    for pattern in "${PATTERNS[@]}"; do
        while IFS= read -r -d '' f; do
            [[ -f "$f" ]] || continue
            local base; base="$(basename "$f")"
            [[ "$base" == "${EXCLUDE_PREFIX}"* ]] && continue

            local size
            size=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f" 2>/dev/null || echo 0)
            (( size > threshold_bytes )) || continue

            if (( DRY_RUN )); then
                log_json "ROTATE_WOULD" "$base" "$(( size / 1024 / 1024 ))MB"
                continue
            fi

            local archive="${f%.log}.${stamp}.log.gz"
            # Compress a COPY first; only truncate once the archive is written,
            # so a gzip failure never destroys the log.
            if gzip -1 -c "$f" > "$archive"; then
                : > "$f"
                log_json "ROTATED" "$base" "$(( size / 1024 / 1024 ))MB -> $(basename "$archive")"
                rotated=$(( rotated + 1 ))
            else
                rm -f "$archive"
                log_json "ROTATE_FAILED" "$base" "gzip failed; log left intact"
            fi
        done < <(find "$dir" -maxdepth 1 -name "$pattern" -type f -print0 2>/dev/null)
    done

    # Prune only archives this script created: `<name>.<UTC stamp>.log.gz`.
    # A bare `*.log.gz` would also delete a human's manual `gzip foo.log`.
    while IFS= read -r -d '' old; do
        if (( DRY_RUN )); then
            log_json "PRUNE_WOULD" "$(basename "$old")" "older than ${RETENTION_DAYS}d"
        else
            # Not `&&` — a failed rm under `set -e` would abort mid-prune.
            rm -f "$old"
            pruned=$(( pruned + 1 ))
        fi
    done < <(find "$dir" -maxdepth 1 -name "*.[0-9]*T[0-9]*Z.log.gz" -type f -mtime "+${RETENTION_DAYS}" -print0 2>/dev/null)

    (( DRY_RUN )) || log_json "ROTATE_DONE" "$dir" "rotated=${rotated} pruned=${pruned}"
}

self_check() {
    # Deliberately NOT `local`: the EXIT trap runs after this function returns,
    # and under `set -u` a function-local would be unbound by then.
    tmp="$(mktemp -d)"
    trap 'rm -rf "${tmp:-}"' EXIT

    dd if=/dev/zero of="$tmp/execution_native.log" bs=1m count=$(( THRESHOLD_MB + 1 )) 2>/dev/null
    # Must be left alone: structlog owns its own rotation.
    echo "structlog content" > "$tmp/orion_errors.log"
    # Must be left alone: this job's own output.
    dd if=/dev/zero of="$tmp/log_rotation.stdout.log" bs=1m count=$(( THRESHOLD_MB + 1 )) 2>/dev/null
    local protected_before self_log_before
    protected_before=$(cat "$tmp/orion_errors.log")
    self_log_before=$(stat -f%z "$tmp/log_rotation.stdout.log" 2>/dev/null || stat -c%s "$tmp/log_rotation.stdout.log")

    # Keep the fd open across rotation, exactly like a live service does.
    exec 9>>"$tmp/execution_native.log"
    rotate_dir "$tmp" >/dev/null
    echo "post-rotation write" >&9
    exec 9>&-

    local size archives self_log_after
    size=$(stat -f%z "$tmp/execution_native.log" 2>/dev/null || stat -c%s "$tmp/execution_native.log")
    self_log_after=$(stat -f%z "$tmp/log_rotation.stdout.log" 2>/dev/null || stat -c%s "$tmp/log_rotation.stdout.log")
    # Glob expansion, not `[[ -f *.gz ]]` — [[ ]] does not glob.
    shopt -s nullglob
    archives=("$tmp"/execution_native.*.log.gz)
    shopt -u nullglob

    (( ${#archives[@]} == 1 )) || { echo "FAIL: expected 1 archive, got ${#archives[@]}"; exit 1; }
    (( size < 1024 * 1024 )) || { echo "FAIL: log not truncated (${size} bytes)"; exit 1; }
    (( size > 0 )) || { echo "FAIL: open fd stopped writing after truncate"; exit 1; }
    [[ "$(cat "$tmp/orion_errors.log")" == "$protected_before" ]] || { echo "FAIL: touched a structlog file"; exit 1; }
    (( self_log_after == self_log_before )) || { echo "FAIL: rotated its own log"; exit 1; }
    gzip -t "${archives[0]}" || { echo "FAIL: archive corrupt"; exit 1; }

    echo "self-check OK: archived, truncated, fd writable, structlog + own log untouched"
}

if [[ "$MODE" == "self-check" ]]; then
    self_check
    exit 0
fi

[[ -d "$LOG_DIR" ]] || { log_json "NO_LOG_DIR" "$LOG_DIR" "missing — check ORION_LOG_DIR"; exit 1; }

# Single-instance guard. macOS has no flock(1); `mkdir` is atomic on every
# filesystem here. A stale lock from a killed run is cleared after 1 hour.
LOCK_DIR="${LOG_DIR}/.rotate.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    if [[ -n "$(find "$LOCK_DIR" -maxdepth 0 -mmin +60 2>/dev/null)" ]]; then
        log_json "LOCK_STALE" "$LOCK_DIR" "older than 60m; taking over"
        rmdir "$LOCK_DIR" 2>/dev/null || true
        mkdir "$LOCK_DIR" 2>/dev/null || { log_json "LOCK_HELD" "$LOCK_DIR" "another run active"; exit 0; }
    else
        log_json "LOCK_HELD" "$LOCK_DIR" "another run active; skipping"
        exit 0
    fi
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT

rotate_dir "$LOG_DIR"
