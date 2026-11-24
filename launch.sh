#!/usr/bin/env bash
# Starts Mauser without a terminal. Messages go to modmanager.log in this folder.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$DIR/modmanager.log"

notify() {
    if command -v kdialog >/dev/null 2>&1; then kdialog --title "Mauser" --error "$1"
    elif command -v zenity >/dev/null 2>&1; then zenity --error --title "Mauser" --text "$1"
    elif command -v notify-send >/dev/null 2>&1; then notify-send "Mauser" "$1"
    fi
}

if [ ! -x "$DIR/.venv/bin/python" ]; then
    notify "Mauser is not set up yet. Run install.sh in $DIR first."
    exit 1
fi
cd "$DIR" || exit 1
# keep the log from growing without limit
if [ -f "$LOG" ] && [ "$(stat -c %s "$LOG" 2>/dev/null || echo 0)" -gt 1000000 ]; then : > "$LOG"; fi
"$DIR/.venv/bin/python" "$DIR/modmanager.py" "$@" >> "$LOG" 2>&1
status=$?
if [ "$status" -ne 0 ]; then
    notify "Mauser stopped with an error. Details are in $LOG"
fi
exit "$status"
