<p align="center">
  <img src="icon.svg" width="96" alt="Mauser icon: a letter M with mouse ears and a tail">
</p>

<h1 align="center">Mauser</h1>

<p align="center">A Fallout 4 mod manager that runs on Linux, for people who are tired of pretending to run Windows.</p>

![The Installed tab, showing the mod list on the left and the load order on the right](docs/installed.png)

I mod Fallout 4 on Bazzite. The usual advice is to run a Windows mod manager
through Wine or fight with protontricks, and the native option I tried would
not work with my install. So I made my own. Mauser talks to the Steam copy of
the game inside its Proton prefix directly, and it is one file you download
and run.

It is version 1.0 and it has been used on exactly one machine: mine (Bazzite,
KDE, Wayland). It may well work on yours. If it does not, please open an issue
and tell me what happened.

## What you get

**Installing.** Drop a `.zip`, `.7z` or `.rar` on the window, or pick a
folder. Mods that come with an installer (FOMOD) get their options screen.

**A game folder you can get back.** Mods sit in their own library and are
linked into the game, so nothing is unpacked on top of your install. Mauser
keeps a record of every file it places, backs up anything it replaces, and one
button takes it all out again.

**Order, and what it means.** Drag mods and plugins into order. A conflicts
window shows which mod wins each file. Plugins that need something you do not
have are marked, with a way to fix them. A counter keeps an eye on the
plugin and archive limits.

**Profiles and lists.** Keep separate setups that share one mod library, each
with its own load order and game settings files. Save a setup to a small file
and rebuild it on another machine.

**Save safety.** Each mod gets a risk tag: high if it becomes part of your
save, low if you can add or remove it freely. You can point Mauser at a save
and it will tell you which plugins that save expects and which are missing.

**The bits the launcher used to do.** Resolution, window mode, vsync and
field of view, without opening the launcher. One switch makes Steam's Play
button start the game through F4SE.

**When it goes wrong.** If the game crashes, Mauser notices and writes a
report: likely causes first, then your mods, load order and the logs.

**Extras.** Run FO4Edit or BodySlide inside the game's Proton setup and have
their output collected into its own mod. Discord Rich Presence if you want it.
Four themes. A mouse called Vinny who carries the parcels.

![The first-time setup, with Vinny at the departure board](docs/first-run.png)

## Nexus Mods: built in, switched off

Mauser can browse Nexus Mods, install with one click, follow requirement links
and check for updates. In this release those features are **off until you
turn them on in Settings**.

The reason is boring but important. They work using your own personal Nexus
API key, and Nexus's rules allow personal keys for personal use and testing
while asking public apps to register with them. I have not been through that
yet. If you switch the features on, they may stop working one day. Search and
categories also lean on a part of Nexus that is not documented.

Everything else works without any of it. Download the mod in your browser,
then install it from the file.

## Getting it

1. Download `Mauser` from the [latest release](https://github.com/TheQuartzite/Mauser/releases/latest).
2. Make it executable. In most file managers that is right-click, Properties,
   Permissions. In a terminal: `chmod +x Mauser`
3. Run it.

The first start takes a few seconds while it unpacks itself to
`~/.local/share/mauser-app` (about 180 MB). After that it opens quickly and
shows up in your application menu.

Before the first run, start Fallout 4 from Steam once and get as far as the
main menu, so the game has made its settings folder.

You need 64-bit Linux with glibc 2.39 or newer.

## Where it keeps things

| What | Where |
|---|---|
| Mod library, settings, crash reports | `~/.local/share/mauser` |
| The unpacked app | `~/.local/share/mauser-app` |
| Menu entry | `~/.local/share/applications/mauser.desktop` |

If you enter a Nexus API key it is stored in `state.json` in the first folder,
readable only by you, and it is only ever sent to Nexus Mods.

To remove Mauser completely, use **Settings > Remove all mod files from the
game**, then delete those three things.

## Running from source

```
git clone https://github.com/TheQuartzite/Mauser.git
cd Mauser
bash install.sh
```

That makes a `.venv` with PySide6 in it and adds a menu entry. If you would
rather do it by hand:

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python modmanager.py
```

`modmanager.py --diagnose` prints what Mauser can see of your game, which is
the first thing to paste into a bug report.

There are two test files that need neither the game nor a network:

```
python test_modmanager.py
python test_online.py
```

To build the single downloadable file, read the top of `packaging/build.py`.

## Licence

Mauser's own code is under the [MIT License](LICENSE), copyright Amy Amethyst.
The app also contains Qt, Python, 7-Zip and three fonts, each under its own
licence. Those are listed in [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)
and under **Settings > About** in the app.

Mauser is not made by, endorsed by or affiliated with Bethesda, ZeniMax,
Valve, Nexus Mods or Discord.

## AI-assisted disclosure

Most of Mauser's code was written with the help of an AI coding assistant,
working to my design and direction. I decided what the app does and how it
looks, and I run each build on my own setup before it goes up. Parts of it
have only ever been tested against stand-ins (a fake Nexus server, a made-up
save file), so treat it as young software and keep backups of saves you care
about.
