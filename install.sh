#!/usr/bin/env bash
# One-time setup for Mauser: installs its libraries into this folder and
# adds it to the desktop's application menu. Safe to run again after updates.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ENTRY="$APPS/mauser.desktop"

case "$DIR" in
    *\"*|*\`*|*\$*|*\\*)
        echo "This folder's path contains a character (\" \` \$ or \\) that menu entries cannot handle."
        echo "Move the folder somewhere without it and run this again."
        exit 1 ;;
esac

if [ ! -x "$DIR/.venv/bin/python" ]; then
    echo "Creating the Python environment..."
    python3 -m venv "$DIR/.venv"
fi
echo "Installing libraries (the first time this downloads a few hundred MB)..."
"$DIR/.venv/bin/python" -m pip install --quiet --upgrade pip
"$DIR/.venv/bin/python" -m pip install --quiet --only-binary=:all: -r "$DIR/requirements.txt"

# Optional: a ready-built copy of 7-Zip for .7z and .rar archives. Only the program
# inside the package is used, so its own Python dependencies are skipped. Nothing
# is compiled; if this fails the app still works for .zip files.
if "$DIR/.venv/bin/python" -m pip install --quiet --only-binary=:all: --no-deps py7zz; then
    echo "7-Zip added for .7z and .rar archives."
else
    echo "Note: could not add 7-Zip. Zip files work; unpack .7z/.rar yourself and use 'Install from folder'."
fi

chmod +x "$DIR/launch.sh"
mkdir -p "$APPS"
cat > "$ENTRY" <<ENTRY_EOF
[Desktop Entry]
Type=Application
Name=Mauser
Comment=Install and manage Fallout 4 mods
Exec="$DIR/launch.sh" %u
Path=$DIR
Icon=$DIR/icon.svg
Terminal=false
Categories=Game;Utility;
StartupWMClass=mauser
ENTRY_EOF
chmod +x "$ENTRY"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$APPS" >/dev/null 2>&1 || true
fi
echo
echo "Done. Mauser is now in your application menu (search for \"Mauser\")."
echo "To remove the menu entry later: rm \"$ENTRY\""
