# Mauser

A mod manager for Fallout 4 on Linux, for the Steam version running through
Proton. It runs natively: no Windows mod manager under Wine, no protontricks.

Version 1.0. So far it has been used on one distribution (Bazzite, KDE,
Wayland). Other distributions, Flatpak Steam and the Steam Deck are untested.

## What it does

- **Installs mods** from `.zip`, `.7z` and `.rar` files or folders, including
  mods with an installer (FOMOD). Drop a file on the window or use
  **Install mod**.
- **Keeps the game folder clean.** Mods live in their own library and are
  linked into the game. Everything it places is recorded and can be removed
  again in one step; files it replaces are backed up and put back.
- **Mod order and load order**, by dragging. Shows which mod wins each
  conflicting file, counts plugins and archives against the game's limits,
  and flags plugins whose requirements are missing, with a way to fix them.
- **Profiles**: separate sets of switched-on mods, order, load order and game
  settings files, sharing one mod library.
- **Mod lists**: save your setup to a small file and rebuild it elsewhere.
- **Save check**: lists the plugins a save expects and which are missing.
- **Save-risk tags**: marks mods that become part of a save.
- **Game settings** without the launcher: resolution, window mode, vsync,
  field of view.
- **F4SE**: one switch makes Steam's Play button start the game through the
  script extender.
- **Crash reports**: notices when the game appears to have crashed and writes
  a report with likely causes, your mods, load order and logs.
- **Tools**: runs Windows modding programs (FO4Edit, BodySlide) in the game's
  Proton setup and collects what they generate into one mod.
- **Discord Rich Presence** (optional, off by default).
- Four themes, and Vinny the station mouse.

## Nexus Mods features are experimental and off by default

Mauser can browse Nexus Mods, install with one click, follow requirement
links and check for updates. These features are **switched off unless you
turn them on in Settings**, because they work with your own personal Nexus
API key. Nexus Mods' API rules allow personal keys for personal use and
testing, and ask public apps to be registered with them. Mauser is not
registered yet. If you switch the features on, you do so knowing they may stop
working. Search and categories also rely on a Nexus interface that is not
documented and can change.

Everything else works without Nexus: download a mod's file in your web
browser and install it from the file.

## Install

1. Download `Mauser` from the releases page.
2. Make it executable: right-click, Properties, Permissions, or
   `chmod +x Mauser`.
3. Run it. The first start unpacks the app to `~/.local/share/mauser-app`
   (about 180 MB) and takes a few seconds; later starts are quick. It adds
   itself to your application menu.

Start Fallout 4 from Steam once, as far as the main menu, before first use, so
the game has created its settings folder.

Needs a 64-bit Linux with glibc 2.39 or newer, and `tar` with xz support.

## Where things are kept

| What | Where |
|---|---|
| Your mod library, settings, crash reports | `~/.local/share/mauser` |
| The unpacked app | `~/.local/share/mauser-app` |
| Menu entry | `~/.local/share/applications/mauser.desktop` |

Your Nexus API key, if you enter one, is stored in `state.json` in the first
folder, readable only by you. It is never sent anywhere but Nexus Mods.

To remove Mauser completely: use **Settings, Remove all mod files from the
game**, then delete the three items above.

## Run from source

    git clone <this repository>
    cd <folder>
    bash install.sh

That creates a `.venv` with PySide6 and adds a menu entry. Or by hand:

    python -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python modmanager.py

Useful commands: `modmanager.py --diagnose` prints what it detects about your
game; `modmanager.py --selftest` checks the app's own parts.

Tests that need no game and no network:

    python test_modmanager.py
    python test_online.py

## Build the downloadable file

See the top of `packaging/build.py`. In short: a Python environment with
PySide6-Essentials, PyInstaller and py7zz, plus gcc, then
`python packaging/build.py`. The result is `dist/Mauser`.

## Licences

Mauser's own code is under the [MIT License](LICENSE), copyright Amy Amethyst.

Third-party software and fonts included in the app are listed in
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md); the same text is under
**Settings, About** in the app.

Mauser is not made by, endorsed by or affiliated with Bethesda Softworks,
ZeniMax, Valve, Nexus Mods or Discord.
