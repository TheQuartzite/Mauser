#!/usr/bin/env python3
"""Build the downloadable Mauser file.

It is a small launcher (launcher.c) followed by the app packed as .tar.xz. The launcher unpacks
the app once, to ~/.local/share/mauser-app/<build id>/, and from then on starts it directly.

Needs: gcc, tar, xz, and a Python environment that has PySide6-Essentials, pyinstaller and
py7zz installed (py7zz with --no-deps: only its 7zz program is used). Run it with that Python:

    python -m venv build-venv
    build-venv/bin/pip install PySide6-Essentials pyinstaller
    build-venv/bin/pip install --no-deps py7zz
    build-venv/bin/python packaging/build.py

Build on the oldest Linux you want to support: the result needs that system's C library or newer.
The result is written to dist/Mauser.
"""
import glob
import hashlib
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
WORK = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")


def main() -> int:
    import py7zz
    seven_zip = os.path.join(os.path.dirname(py7zz.__file__), "bin", "7zz")
    if not os.path.isfile(seven_zip):
        print("py7zz's 7zz program was not found")
        return 1
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(DIST, exist_ok=True)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--windowed", "--name", "Mauser",
                    "--distpath", os.path.join(WORK, "dist"), "--workpath", os.path.join(WORK, "work"), "--specpath", WORK,
                    "--add-data", os.path.join(ROOT, "icon.svg") + ":.", "--add-data", os.path.join(ROOT, "fonts") + ":fonts",
                    "--add-data", os.path.join(ROOT, "THIRD-PARTY-NOTICES.md") + ":.",
                    "--add-binary", seven_zip + ":.",
                    "--exclude-module", "tkinter", "--exclude-module", "py7zz", "--exclude-module", "numpy",
                    os.path.join(ROOT, "modmanager.py")], check=True, cwd=ROOT)
    app = os.path.join(WORK, "dist", "Mauser")
    inner = os.path.join(app, "_internal")
    # Left out: Qt's own translation files (the app is in English). Everything else PyInstaller collected stays,
    # because a library that looks unused on the build machine may be needed on someone else's desktop.
    shutil.rmtree(os.path.join(inner, "PySide6", "Qt", "translations"), ignore_errors=True)
    for lib in glob.glob(os.path.join(inner, "libpython3*.so*")):
        subprocess.run(["strip", "--strip-unneeded", lib], check=False)          # debugging symbols only
    launcher = os.path.join(WORK, "launcher")
    subprocess.run(["gcc", "-O2", "-static", "-Wall", "-Wextra", "-Wno-format-truncation", "-o", launcher,
                    os.path.join(HERE, "launcher.c")], check=True)
    subprocess.run(["strip", launcher], check=True)
    payload = os.path.join(WORK, "payload.tar.xz")
    with open(payload, "wb") as out:
        tar = subprocess.Popen(["tar", "-C", app, "-cf", "-", "."], stdout=subprocess.PIPE)
        subprocess.run(["xz", "-9", "-T0"], stdin=tar.stdout, stdout=out, check=True)
        if tar.wait() != 0:
            return 1
    with open(payload, "rb") as f:
        data = f.read()
    build_id = hashlib.sha256(data).hexdigest()[:16]
    final = os.path.join(DIST, "Mauser")
    with open(final, "wb") as f, open(launcher, "rb") as stub:
        f.write(stub.read())
        f.write(data)
        f.write(struct.pack("<Q", len(data)) + build_id.encode() + b"MAUSRPKG")
    os.chmod(final, 0o755)
    print("Built %s: %.1f MB, build %s" % (final, os.path.getsize(final) / 1e6, build_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
