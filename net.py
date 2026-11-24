"""Small HTTP helpers on top of the standard library (no extra packages)."""
from __future__ import annotations

import http.client
import json
import os
import ssl
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Optional

USER_AGENT = "Mauser/0.8 (Linux; personal mod manager)"
TIMEOUT = 30


_CA_FILES = ("/etc/pki/tls/certs/ca-bundle.crt",                 # Fedora, Bazzite
             "/etc/ssl/certs/ca-certificates.crt",                # Debian, Ubuntu, Arch
             "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
             "/etc/ssl/cert.pem", "/etc/ssl/ca-bundle.pem")
_context = None


def tls_context() -> ssl.SSLContext:
    """Verifying TLS context that knows where Linux systems keep their certificates.
    A packaged copy of Python may have other default locations built in."""
    global _context
    if _context is None:
        ctx = ssl.create_default_context()
        for path in _CA_FILES:
            if os.path.isfile(path):
                try:
                    ctx.load_verify_locations(cafile=path)
                    break
                except (ssl.SSLError, OSError):
                    continue
        _context = ctx
    return _context


class NetError(Exception):
    def __init__(self, message: str, status: int = 0, body: str = ""):
        super().__init__(message)
        self.status = status
        self.body = body


def clean_url(url: str) -> str:
    """Percent-encode characters a web address may not contain as they are (spaces
    above all: download servers hand out file names like "My Mod - v2.7z" unencoded).
    Anything already encoded is left alone."""
    parts = urllib.parse.urlsplit(url.strip())
    path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=~-._")
    query = urllib.parse.quote(parts.query, safe="/%:@!$&'()*+,;=~-._?")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def _request(url: str, headers: Optional[dict] = None, data: Optional[bytes] = None) -> urllib.request.Request:
    if not url.lower().startswith(("https://", "http://")):
        raise NetError("refusing to open a non-web address: %s" % url[:80])
    try:
        url = clean_url(url)
    except ValueError:
        raise NetError("not a valid web address: %s" % url[:80])
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    h.update(headers or {})
    return urllib.request.Request(url, data=data, headers=h)


def _fail(url: str, e: Exception) -> NetError:
    host = urllib.parse.urlsplit(url).netloc
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = e.read(2000).decode("utf-8", "replace")
        except Exception:
            body = ""
        return NetError("%s answered with error %d" % (host, e.code), e.code, body)
    reason = getattr(e, "reason", e)
    return NetError("could not reach %s: %s" % (host, reason))


def get_bytes(url: str, headers: Optional[dict] = None, limit: int = 32 << 20) -> bytes:
    try:
        with urllib.request.urlopen(_request(url, headers), timeout=TIMEOUT, context=tls_context()) as r:
            data = r.read(limit + 1)
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
        raise _fail(url, e)
    if len(data) > limit:
        raise NetError("response from %s is too large" % urllib.parse.urlsplit(url).netloc)
    return data


def get_json(url: str, headers: Optional[dict] = None):
    raw = get_bytes(url, headers)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise NetError("unexpected (non-JSON) answer from %s" % urllib.parse.urlsplit(url).netloc, 0,
                       raw[:300].decode("utf-8", "replace"))


def post_json(url: str, payload, headers: Optional[dict] = None):
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    try:
        with urllib.request.urlopen(_request(url, h, json.dumps(payload).encode("utf-8")), timeout=TIMEOUT, context=tls_context()) as r:
            raw = r.read(32 << 20)
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
        raise _fail(url, e)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise NetError("unexpected (non-JSON) answer from %s" % urllib.parse.urlsplit(url).netloc, 0,
                       raw[:300].decode("utf-8", "replace"))


def download(url: str, dest: str, progress: Optional[Callable[[int, int], None]] = None,
             cancelled: Optional[Callable[[], bool]] = None, headers: Optional[dict] = None) -> str:
    """Download to dest (via a .part file). progress(done, total) is called as data
    arrives; total is 0 when the server does not say. Returns dest."""
    part = dest + ".part"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    h = {"Accept": "*/*"}
    h.update(headers or {})
    try:
        with urllib.request.urlopen(_request(url, h), timeout=TIMEOUT, context=tls_context()) as r, open(part, "wb") as out:
            total = int(r.headers.get("Content-Length") or 0)
            done = 0
            while True:
                if cancelled is not None and cancelled():
                    raise NetError("download cancelled")
                chunk = r.read(1 << 18)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        if total and done != total:
            raise NetError("download ended early (%d of %d bytes)" % (done, total))
        os.replace(part, dest)
        return dest
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
        raise _fail(url, e)
    finally:
        if os.path.exists(part):
            try:
                os.unlink(part)
            except OSError:
                pass
