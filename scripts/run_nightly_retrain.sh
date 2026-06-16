#!/usr/bin/env bash
# Nightly retrain of Orion bucket scorers + exit classifiers.
#
# Sequence:
#   1. Snapshot the current models/*.pkl into models/archive/<ts>/ so a
#      bad training run can be rolled back manually.
#   2. Invoke scripts/run_training.py with ORION_MODEL_DIR pointed at
#      the live models/ directory so freshly trained files overwrite
#      the ones the running orion_execution container loads from.
#   3. On training success, restart orion_execution. The MLScorer hot-
#      reloads model files via mtime check (every 60s in the scoring
#      path), but BucketExitClassifier only loads at __init__, so a
#      restart is required to pick up new exit classifiers.
#
# Heber gold pipelines run at 02:00 (per the user's crontab); this
# script is intended to fire at 03:00 so it sees fresh labels.
#
# Logs are appended to logs/cron_retrain.log under the project root.
# Rotate manually or via logrotate; this script does no rotation itself.

set -euo pipefail

PROJECT_ROOT="/Users/jacobmcmillan/Empire/Orion"
MODELS_DIR="${PROJECT_ROOT}/models"
ARCHIVE_ROOT="${MODELS_DIR}/archive"
LOG_DIR="${PROJECT_ROOT}/logs"
LOG_FILE="${LOG_DIR}/cron_retrain.log"

mkdir -p "${LOG_DIR}" "${ARCHIVE_ROOT}"

# Ensure uv + docker are on PATH when invoked from cron (cron's PATH
# is minimal; Docker.app installs /usr/local/bin/docker symlink).
export PATH="/opt/homebrew/bin:/usr/local/bin:${HOME}/.local/bin:${PATH}"

# Override the script's default `artifacts/models/` so freshly trained
# model files overwrite the live ones in `models/` that orion_execution
# loads from (volume-mounted into the container at /app/models).
export ORION_MODEL_DIR="${MODELS_DIR}"
# Heber data root on the host (the container path /Volumes/heber/data
# is the bind-mount of this directory).
export HEBER_DATA_ROOT="${HOME}/.heber-cache/data"

run_id="$(date '+%Y-%m-%dT%H%M%S%z')"
archive_dir="${ARCHIVE_ROOT}/${run_id}"

run_step() {
    local name="$1"
    shift
    echo "--- $(date '+%Y-%m-%dT%H:%M:%S%z') ${name} ---"
    if "$@"; then
        return 0
    else
        local rc=$?
        echo "!!! $(date '+%Y-%m-%dT%H:%M:%S%z') ${name} FAILED rc=${rc}"
        return "${rc}"
    fi
}

archive_current_models() {
    mkdir -p "${archive_dir}"
    # cp -p preserves mtime so the archive reflects when the models were
    # actually trained, not when this script ran.
    if compgen -G "${MODELS_DIR}/*.pkl" > /dev/null; then
        cp -p "${MODELS_DIR}"/*.pkl "${archive_dir}/"
        local count
        count=$(find "${archive_dir}" -maxdepth 1 -name '*.pkl' | wc -l | tr -d ' ')
        echo "archived ${count} model files to ${archive_dir}"
    else
        echo "no existing models/*.pkl to archive"
    fi
}

train_models() {
    cd "${PROJECT_ROOT}"
    uv run python scripts/run_training.py
}

restart_execution() {
    # Skip the restart if the container isn't currently running — the
    # next operator-driven `docker compose up` will load the fresh
    # models on its own.
    if ! docker ps --format '{{.Names}}' | grep -q '^orion_execution$'; then
        echo "orion_execution not running; skipping restart"
        return 0
    fi
    docker restart orion_execution >/dev/null
    echo "orion_execution restart issued"
}

{
    echo
    echo "=== ${run_id} START retrain ==="
    if run_step "archive" archive_current_models \
        && run_step "train" train_models \
        && run_step "restart" restart_execution; then
        echo "=== $(date '+%Y-%m-%dT%H:%M:%S%z') OK retrain complete ==="
    else
        rc=$?
        echo "=== $(date '+%Y-%m-%dT%H:%M:%S%z') FAIL retrain exit=${rc} archive=${archive_dir} ==="
        exit "${rc}"
    fi
} >> "${LOG_FILE}" 2>&1
