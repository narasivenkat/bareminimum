#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# setup.sh - Add this folder to the user PATH so runmin can be run from
# anywhere by simply typing:  runmin
# ---------------------------------------------------------------------------

set -euo pipefail

# Folder that contains this script (and runmin).
SOURCE="${BASH_SOURCE[0]:-$0}"
while [ -h "$SOURCE" ]; do
    DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
    SOURCE="$(readlink "$SOURCE")"
    [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
done
SCRIPT_DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"

if [ ! -f "$SCRIPT_DIR/runmin" ] && [ ! -f "$SCRIPT_DIR/runmin.sh" ]; then
    echo "[ERROR] runmin was not found in \"$SCRIPT_DIR\"." >&2
    exit 1
fi

# Ensure scripts are executable
chmod +x "$SCRIPT_DIR/runmin" 2>/dev/null || true
if [ -f "$SCRIPT_DIR/runmin.sh" ]; then
    chmod +x "$SCRIPT_DIR/runmin.sh" 2>/dev/null || true
fi
if [ -f "$SCRIPT_DIR/setup" ]; then
    chmod +x "$SCRIPT_DIR/setup" 2>/dev/null || true
fi
if [ -f "$SCRIPT_DIR/setup.sh" ]; then
    chmod +x "$SCRIPT_DIR/setup.sh" 2>/dev/null || true
fi

# Check if already on PATH environment variable
case ":$PATH:" in
    *":$SCRIPT_DIR:"*)
        echo "[setup] Already on PATH: $SCRIPT_DIR"
        echo "[setup] Done. Run:  runmin"
        exit 0
        ;;
esac

# Identify shell profile file(s) to update on macOS / Linux
CONFIG_FILES=()
SHELL_NAME="$(basename "${SHELL:-zsh}")"

if [ "$SHELL_NAME" = "zsh" ] || [ -f "$HOME/.zshrc" ]; then
    CONFIG_FILES+=("$HOME/.zshrc")
fi

if [ "$SHELL_NAME" = "bash" ] || [ -f "$HOME/.bash_profile" ] || [ -f "$HOME/.bashrc" ]; then
    if [ -f "$HOME/.bash_profile" ]; then
        CONFIG_FILES+=("$HOME/.bash_profile")
    elif [ -f "$HOME/.bashrc" ]; then
        CONFIG_FILES+=("$HOME/.bashrc")
    else
        CONFIG_FILES+=("$HOME/.bash_profile")
    fi
fi

if [ ${#CONFIG_FILES[@]} -eq 0 ]; then
    CONFIG_FILES+=("$HOME/.zshrc")
fi

PATH_ENTRY="export PATH=\"$SCRIPT_DIR:\$PATH\""

FOR_EACH_FILE=()
for FILE in "${CONFIG_FILES[@]}"; do
    if [[ " ${FOR_EACH_FILE[*]:-} " =~ " ${FILE} " ]]; then
        continue
    fi
    FOR_EACH_FILE+=("$FILE")

    if [ -f "$FILE" ] && grep -Fq "$SCRIPT_DIR" "$FILE"; then
        echo "[setup] Already on PATH in $FILE"
    else
        echo "" >> "$FILE"
        echo "# Added by pyharness setup" >> "$FILE"
        echo "$PATH_ENTRY" >> "$FILE"
        echo "[setup] Added to PATH in $FILE: $SCRIPT_DIR"
    fi
done

echo "[setup] Done. Open a NEW terminal, then run:  runmin"