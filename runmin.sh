#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Agentic coding harness - macOS / Linux setup & run script
# Creates a virtual environment, installs dependencies, and runs run.py
# ---------------------------------------------------------------------------

set -euo pipefail

# --- Remember where the user invoked this script from. run.py must operate
#     on THIS folder (the caller's working directory), not the harness
#     install folder. ---
CALLER_DIR="$(pwd)"

# --- Locate directory containing this script ---
SOURCE="${BASH_SOURCE[0]:-$0}"
while [ -h "$SOURCE" ]; do
    DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"

# --- Switch to the harness folder only for environment setup. ---
cd "$SCRIPT_DIR"

# --- Locate a Python interpreter ---
PYTHON_CMD=""
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
fi

if [ -z "$PYTHON_CMD" ]; then
    echo "[ERROR] Python was not found on PATH. Install Python 3.13+ and retry." >&2
    exit 1
fi

# --- Create the virtual environment if missing ---
if [ ! -f ".venv/bin/python" ]; then
    echo "[setup] Creating virtual environment in .venv ..."
    "$PYTHON_CMD" -m venv .venv
    if [ $? -ne 0 ]; then
        echo "[ERROR] Failed to create the virtual environment." >&2
        exit 1
    fi
fi

VENV_PY="$SCRIPT_DIR/.venv/bin/python"

# --- Upgrade pip (quietly; only warnings/errors are shown) ---
if ! "$VENV_PY" -m pip install --quiet --upgrade pip; then
    echo "[ERROR] Failed to upgrade pip." >&2
    exit 1
fi

# --- Install dependencies, capturing output so we can stay quiet when
#     everything is already satisfied and only report real installs ---
PIP_LOG="${TMPDIR:-/tmp}/pyharness_pip_$$.log"
if ! "$VENV_PY" -m pip install -r requirements.txt > "$PIP_LOG" 2>&1; then
    echo "[ERROR] Failed to install dependencies." >&2
    cat "$PIP_LOG"
    rm -f "$PIP_LOG"
    exit 1
fi

# Only announce and show details when packages were actually installed.
if grep -q "Successfully installed" "$PIP_LOG"; then
    echo "[setup] Installing dependencies from requirements.txt ..."
    grep -v "Requirement already satisfied" "$PIP_LOG" || true
fi
rm -f "$PIP_LOG"

# --- Run the program in the context of the caller's folder ---
# The harness tools resolve paths against HARNESS_WORKSPACE (see
# tools/_common.py). Default it to the caller's directory and switch the
# working directory there so the agent acts where runmin was launched from.
if [ -z "${HARNESS_WORKSPACE:-}" ]; then
    export HARNESS_WORKSPACE="$CALLER_DIR"
fi

if [ -z "${BAREMINIMUM_DIR:-}" ]; then
    export BAREMINIMUM_DIR="$SCRIPT_DIR"
fi

cd "$CALLER_DIR"
echo "[setup] Starting run.py in \"$CALLER_DIR\" ..."

set +e
"$VENV_PY" "$SCRIPT_DIR/run.py" "$@"
EXIT_CODE=$?
exit $EXIT_CODE
