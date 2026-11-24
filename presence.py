"""Discord Rich Presence: shows what you are doing with Mauser on your Discord profile.

Discord's desktop app listens on a local socket. A program says which Discord "application" it
is (an id from Discord's developer site) and then sends the activity to show. Nothing goes over
the internet from here: Discord itself publishes what it is given.

All talking happens on a background thread so a slow or absent Discord never holds up the window.
"""
from __future__ import annotations

import json
import os
import queue
import socket
import struct
import threading
import time
import uuid
from typing import Optional

MIN_GAP = 4.0          # seconds between updates; Discord allows about five per twenty seconds


def socket_paths() -> list:
    """Where Discord's socket may be: the normal app, the Flatpak, the Snap, and other clients that imitate it."""
    bases = []
    for var in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"):
        value = os.environ.get(var)
        if value and value not in bases:
            bases.append(value)
    if "/tmp" not in bases:
        bases.append("/tmp")
    out = []
    for base in bases:
        for sub in ("", "app/com.discordapp.Discord", "app/com.discordapp.DiscordCanary", "app/dev.vencord.Vesktop",
                    "snap.discord", ".flatpak/com.discordapp.Discord/xdg-run", ".flatpak/dev.vencord.Vesktop/xdg-run"):
            for i in range(10):
                out.append(os.path.join(base, sub, "discord-ipc-%d" % i))
    return out


class Presence:
    def __init__(self, client_id: str):
        self.client_id = "".join(c for c in str(client_id) if c.isdigit())
        self.status = "not connected"          # for Settings: what happened last
        self._jobs: queue.Queue = queue.Queue()
        self._sock: Optional[socket.socket] = None
        self._last_sent = 0.0
        self._current: Optional[dict] = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="discord-presence", daemon=True)
        self._thread.start()

    # -- called from the window ---------------------------------------------------

    def show(self, activity: Optional[dict]) -> None:
        """Ask for this activity to be shown (None clears it). Repeats of the same thing are ignored."""
        if activity == self._current:
            return
        self._current = activity
        self._jobs.put(("activity", activity))

    def close(self) -> None:
        self._stop = True
        self._jobs.put(("quit", None))

    # -- the background thread ----------------------------------------------------

    def _run(self) -> None:
        pending = None
        have_pending = False
        while not self._stop:
            try:
                kind, value = self._jobs.get(timeout=1.0 if have_pending else 30.0)
                if kind == "quit":
                    break
                pending, have_pending = value, True
                while True:                     # only the newest request matters
                    try:
                        kind, value = self._jobs.get_nowait()
                        if kind == "quit":
                            self._stop = True
                            break
                        pending = value
                    except queue.Empty:
                        break
            except queue.Empty:
                pass
            if self._stop:
                break
            if have_pending and time.time() - self._last_sent >= MIN_GAP:
                if self._send_activity(pending):
                    have_pending = False
                else:
                    time.sleep(5)               # Discord is not there: try again shortly, without spinning
        self._clear_and_close()

    def _connect(self) -> bool:
        if self._sock is not None:
            return True
        if not self.client_id:
            self.status = "no application id entered"
            return False
        for path in socket_paths():
            if not os.path.exists(path):
                continue
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(3)
            try:
                s.connect(path)
                self._write(s, 0, {"v": 1, "client_id": self.client_id})
                op, reply = self._read(s)
                if op == 2 or reply.get("evt") == "ERROR" or reply.get("code"):
                    self.status = "Discord refused the application id (%s)" % (reply.get("message") or (reply.get("data") or {}).get("message") or "no reason given")
                    s.close()
                    return False
                self._sock = s
                user = ((reply.get("data") or {}).get("user") or {}).get("username", "")
                self.status = "connected to Discord%s" % (" as %s" % user if user else "")
                return True
            except (OSError, ValueError):
                s.close()
        self.status = "Discord is not running (its desktop app is needed; the website cannot do this)"
        return False

    @staticmethod
    def _write(s: socket.socket, op: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        s.sendall(struct.pack("<II", op, len(data)) + data)

    @staticmethod
    def _read(s: socket.socket) -> tuple:
        head = b""
        while len(head) < 8:
            chunk = s.recv(8 - len(head))
            if not chunk:
                raise OSError("closed")
            head += chunk
        op, size = struct.unpack("<II", head)
        if size > 1 << 20:
            raise ValueError("too large")
        body = b""
        while len(body) < size:
            chunk = s.recv(size - len(body))
            if not chunk:
                raise OSError("closed")
            body += chunk
        return op, json.loads(body.decode("utf-8", "replace") or "{}")

    def _send_activity(self, activity: Optional[dict]) -> bool:
        for _attempt in (1, 2):                 # a second go after reconnecting, if Discord was restarted
            if not self._connect():
                return False
            try:
                self._write(self._sock, 1, {"cmd": "SET_ACTIVITY", "nonce": str(uuid.uuid4()),
                                            "args": {"pid": os.getpid(), "activity": activity}})
                self._read(self._sock)
                self._last_sent = time.time()
                return True
            except (OSError, ValueError):
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
        return False

    def _clear_and_close(self) -> None:
        if self._sock is None:
            return
        try:
            self._write(self._sock, 1, {"cmd": "SET_ACTIVITY", "nonce": str(uuid.uuid4()),
                                        "args": {"pid": os.getpid(), "activity": None}})
            self._sock.close()
        except OSError:
            pass
        self._sock = None


def activity(details: str, state: str = "", started: float = 0.0, image: str = "mauser", image_text: str = "Mauser") -> dict:
    """An activity in the shape Discord expects. Texts are cut to its limits (2 to 128 characters)."""
    out: dict = {"details": (details or "Mauser")[:128]}
    if state and len(state) >= 2:
        out["state"] = state[:128]
    if started:
        out["timestamps"] = {"start": int(started)}
    if image:
        out["assets"] = {"large_image": image, "large_text": image_text[:128]}
    return out
