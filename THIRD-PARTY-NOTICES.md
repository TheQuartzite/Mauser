# Third-party software and fonts

Mauser's own code is under the MIT License, copyright Amy Amethyst (see LICENSE).
The downloadable app also contains the following, each under its own licence.

## Qt and PySide6

The interface is built with Qt 6 through PySide6 (Qt for Python), both from The
Qt Company and contributors, used under the GNU Lesser General Public License
version 3 (LGPL-3.0). They are included as separate, unmodified libraries: in the
unpacked app they are the files under `_internal/PySide6`, which you may replace
with your own builds. Source code: <https://code.qt.io> and
<https://code.qt.io/cgit/pyside/pyside-setup.git>. Licence text:
<https://www.gnu.org/licenses/lgpl-3.0.html>.

## Python

The app contains the Python interpreter and standard library, under the Python
Software Foundation License. <https://docs.python.org/3/license.html>

## 7-Zip

Archives are unpacked with the 7-Zip command-line program (`7zz`), by Igor
Pavlov, taken unmodified from the `py7zz` package. 7-Zip is under the GNU LGPL,
with parts under the BSD 3-clause licence and the unRAR restriction: the RAR
code may not be used to recreate the RAR compression algorithm.
<https://www.7-zip.org/license.txt>

## PyInstaller bootloader

The app is packaged with PyInstaller, whose bootloader is included under the
GPL with a special exception that allows distributing packaged programs under
any licence. <https://pyinstaller.org/en/stable/license.html>

## Fonts

In `fonts/`, each under the SIL Open Font License 1.1 (texts alongside the
fonts):

- **Barlow** and **Barlow Semi Condensed**, by Jeremy Tribby.
- **Share Tech Mono**, by Carrois Apostrophe.
- **Hind**, by Indian Type Foundry.

## Not affiliated

Mauser is not made by, endorsed by or affiliated with Bethesda Softworks,
ZeniMax, Valve, Nexus Mods, Discord, or the maker of any firearm of the same
name. Fallout is a trademark of its owner.
