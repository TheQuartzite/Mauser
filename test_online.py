"""Tests for the mod browser's providers. Run with:  python3 test_online.py

Nothing here talks to the real sites: a small stand-in server on this machine
imitates the Nexus Mods and GitHub answers, including the Premium-only rule.
"""
import io
import json
import os
import re
import sys
import tempfile
import threading
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODS = {
    101: {"name": "Better Pistols", "summary": "Sharper pistols.", "author": "Ana", "version": "1.2", "endorsement_count": 900,
          "mod_unique_downloads": 5000, "contains_adult_content": False, "description": "[b]Hello[/b]<br />[url=https://example.org/x]site[/url]"},
    102: {"name": "Pistol Sounds", "summary": "Louder.", "author": "Ben", "version": "0.9", "endorsement_count": 50,
          "mod_unique_downloads": 300, "contains_adult_content": False, "description": "Plain"},
    103: {"name": "Grown-up Pistols", "summary": "Adult.", "author": "Cy", "version": "1", "endorsement_count": 10,
          "mod_unique_downloads": 20, "contains_adult_content": True, "description": ""},
    104: {"name": "Hidden Thing", "summary": "", "author": "D", "version": "1", "available": False},
}
FILES = {101: [{"file_id": 11, "name": "Better Pistols Main", "file_name": "Better Pistols-101-1-2-1700000000.zip", "version": "1.2",
                "category_name": "MAIN", "size_kb": 3, "size_in_bytes": 3000, "uploaded_time": "2026-10-01T10:00:00.000+00:00",
                "description": "Main file"},
               {"file_id": 12, "name": "4K patch", "file_name": "patch.zip", "version": "1.0", "category_name": "OPTIONAL",
                "size_kb": 1, "uploaded_time": "2026-10-02T10:00:00.000+00:00", "description": ""},
               {"file_id": 13, "name": "Old", "file_name": "old.zip", "version": "0.1", "category_name": "OLD_VERSION", "size_kb": 1},
               {"file_id": 14, "name": "Gone", "file_name": "gone.zip", "version": "0.1", "category_name": None, "size_kb": 1}],
         102: [{"file_id": 21, "name": "Sounds", "file_name": "sounds.zip", "version": "0.9", "category_name": "MAIN", "size_kb": 2,
                "uploaded_time": "2026-01-01T10:00:00.000+00:00", "description": "For game version 1.10.163"},
               {"file_id": 22, "name": "Sounds NG", "file_name": "sounds_ng.zip", "version": "1.0", "category_name": "MAIN", "size_kb": 2,
                "uploaded_time": "2026-06-01T10:00:00.000+00:00", "description": "Next-Gen update only"}]}


def mod_zip(name="BetterPistols.esp"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, b"TES4" + b"\0" * 20)
        z.writestr("Textures/pistol.dds", b"tex")
    return buf.getvalue()


class Fake(BaseHTTPRequestHandler):
    search_style = "any"           # "any", "plain" (wildcard stars find nothing) or "dead" (name filter finds nothing)
    graphql_mode = "ok"            # "ok", "alt" (different field names) or "broken"
    category_filter = True         # whether the search interface offers a category filter
    author_filters = ["uploaderId", "author"]
    requests = []

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _v1_mod(self, mid):
        m = dict(MODS[mid])
        m.update({"uploaded_by": "ana_account" if mid in (101, 103) else "ben_account",
                  "user": {"member_id": 77 if mid in (101, 103) else 88, "name": "ana_account" if mid in (101, 103) else "ben_account"},
                  "category_id": {101: 12, 102: 5, 103: 12}.get(mid, 9), "mod_id": mid, "picture_url": "http://%s/img/%d.png" % (self.headers["Host"], mid),
                  "updated_time": "2026-11-0%dT00:00:00.000+00:00" % (mid % 9 + 1), "available": m.get("available", True)})
        return m

    def do_GET(self):
        Fake.requests.append(self.path)
        url = urllib.parse.urlsplit(self.path)
        path, query = url.path, urllib.parse.parse_qs(url.query)
        key = self.headers.get("apikey", "")
        if path.startswith("/v1/"):
            if key not in ("FREE", "PREMIUM"):
                return self._send(401, {"message": "Please provide a valid API Key"})
            if path == "/v1/users/validate.json":
                return self._send(200, {"name": "tester", "is_premium": key == "PREMIUM", "user_id": 7})
            if path == "/v1/games/fallout4.json":
                return self._send(200, {"id": 1151, "name": "Fallout 4", "categories": [
                    {"category_id": 1, "name": "Fallout 4", "parent_category": False},
                    {"category_id": 12, "name": "Weapons", "parent_category": 1},
                    {"category_id": 5, "name": "Audio - SFX", "parent_category": 1},
                    {"category_id": 9, "name": "Armour", "parent_category": 1}]})
            m = re.fullmatch(r"/v1/games/fallout4/mods/(trending|latest_added|latest_updated)\.json", path)
            if m:
                return self._send(200, [self._v1_mod(i) for i in MODS])
            m = re.fullmatch(r"/v1/games/fallout4/mods/(\d+)\.json", path)
            if m:
                mid = int(m.group(1))
                return self._send(200, self._v1_mod(mid)) if mid in MODS else self._send(404, {"message": "No Mod Found"})
            m = re.fullmatch(r"/v1/games/fallout4/mods/(\d+)/files\.json", path)
            if m:
                return self._send(200, {"files": FILES.get(int(m.group(1)), []), "file_updates": [{"old_file_id": 13, "new_file_id": 11}] if int(m.group(1)) == 101 else []})
            m = re.fullmatch(r"/v1/games/fallout4/mods/(\d+)/files/(\d+)\.json", path)
            if m:
                for f in FILES.get(int(m.group(1)), []):
                    if f["file_id"] == int(m.group(2)):
                        return self._send(200, f)
                return self._send(404, {"message": "File not found"})
            m = re.fullmatch(r"/v1/games/fallout4/mods/(\d+)/files/(\d+)/download_link\.json", path)
            if m:
                allowed = key == "PREMIUM" or (query.get("key") == ["GOODKEY"] and query.get("expires") == ["999"])
                if not allowed:
                    return self._send(403, {"message": "You don't have permission to get download links from the API "
                                                       "without visiting nexusmods.com - this is for premium users only."})
                return self._send(200, [{"name": "CDN", "short_name": "cdn",
                                         "URI": "http://%s/cdn/%s/%s.zip" % (self.headers["Host"], m.group(1), m.group(2))}])
            return self._send(404, {"message": "not found"})
        if path.startswith("/cdn/"):
            return self._send(200, mod_zip(), "application/zip")
        if path.startswith("/img/"):
            import struct
            import zlib

            def chunk(kind, data):
                return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
            rows = b"".join(b"\0" + bytes([200, 120, 40]) * 16 for _ in range(9))
            png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 16, 9, 8, 2, 0, 0, 0))
                   + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))
            return self._send(200, png, "image/png")
        if path == "/gh/search/repositories":
            q = query.get("q", [""])[0]
            bait = {"full_name": "x/fallout-4-hack-toolkit", "name": "fallout-4-hack-toolkit", "description": "Free download",
                    "stargazers_count": 999, "owner": {"login": "x"}}
            items = [bait, {"full_name": "ana/f4se-thing", "name": "f4se-thing", "description": "A plugin", "stargazers_count": 42,
                      "pushed_at": "2026-09-09T00:00:00Z", "html_url": "https://github.com/ana/f4se-thing",
                      "owner": {"login": "ana", "avatar_url": "http://%s/img/1.png" % self.headers["Host"]}}]
            return self._send(200, {"total_count": 1, "items": items if "nothing" not in q else []})
        if path == "/gh/repos/ana/f4se-thing/releases":
            host = self.headers["Host"]
            return self._send(200, [
                {"tag_name": "v2.0", "body": "Notes <b>raw</b>", "published_at": "2026-09-01T00:00:00Z", "prerelease": False,
                 "assets": [{"id": 5, "name": "thing.zip", "size": 1234, "browser_download_url": "https://%s/cdn/x/y.zip" % host},
                            {"id": 6, "name": "source.tar.gz", "size": 9, "browser_download_url": "https://%s/cdn/x/s" % host}]},
                {"tag_name": "v1.0", "draft": True, "assets": [{"id": 1, "name": "draft.zip", "browser_download_url": "https://x/y"}]}])
        self._send(404, {"message": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        doc = json.loads(self.rfile.read(length) or b"{}")
        query, variables = doc.get("query", ""), doc.get("variables") or {}
        Fake.requests.append("POST " + query[:40])
        mode = Fake.graphql_mode
        if mode == "broken":
            return self._send(200, {"errors": [{"message": "Field 'mods' doesn't exist on type 'Query'"}]})
        game_key = "gameDomainName" if mode == "ok" else "gameDomain"
        if "__type" in query:
            scalar = lambda n: {"name": n, "type": {"kind": "SCALAR", "name": "String", "ofType": None}}
            fields = [scalar(n) for n in ("modId", "name", "summary", "version", "pictureUrl", "thumbnailUrl",
                                          "endorsements", "downloads", "updatedAt", "adultContent")]
            fields.append(scalar("author") if mode == "ok" else
                          {"name": "author", "type": {"kind": "OBJECT", "name": "User", "ofType": None}})
            return self._send(200, {"data": {"mod": {"fields": fields},
                                             "filter": {"inputFields": [{"name": game_key}, {"name": "name"}] + [
                                                 {"name": n} for n in Fake.author_filters] + (
                                                 [{"name": "categoryName"}] if Fake.category_filter else [])},
                                             "sort": {"inputFields": [{"name": n} for n in (
                                                 "endorsements", "downloads", "createdAt", "updatedAt")]}}})
        if "modRequirements" in query:
            if variables.get("modId") != "101":
                return self._send(200, {"data": {"mod": {"modRequirements": {"nexusRequirements": {"nodes": []},
                                                                           "dlcRequirements": []}}}})
            return self._send(200, {"data": {"mod": {"modRequirements": {
                "nexusRequirements": {"nodes": [{"modId": "42147", "modName": "F4SE", "url": "", "notes": "0.6.23+"},
                                                {"modId": "102", "modName": "Pistol Sounds", "url": "", "notes": "[HARD REQUIREMENT]"},
                                                {"modId": "103", "modName": "Grown-up Pistols", "url": "", "notes": "[Optional] only for the patch"},
                                                {"modId": None, "modName": "Something external", "url": "https://example.org/e", "notes": ""}]},
                "dlcRequirements": [{"gameExpansion": {"name": "Far Harbor"}, "notes": ""}]}}}})
        flt = variables.get("filter") or {}
        if game_key not in flt or flt[game_key][0]["value"] != "fallout4":
            return self._send(200, {"errors": [{"message": "Unknown filter field"}]})
        if mode == "alt" and "author { name }" not in query:
            return self._send(200, {"errors": [{"message": "Field 'author' must have selections"}]})
        term = ""
        if "name" in flt:
            if flt["name"][0]["op"] != "WILDCARD":
                return self._send(200, {"errors": [{"message": "bad op"}]})
            term = flt["name"][0]["value"].lower()
            if Fake.search_style == "dead" or (Fake.search_style == "plain" and "*" in term):
                term = "\0"                       # matches nothing
            term = term.strip("*")
        sort = list((variables.get("sort") or [{"endorsements": {}}])[0])[0]
        sort = {"createdAt": "modId", "updatedAt": "modId"}.get(sort, sort)
        wanted_category = flt["categoryName"][0]["value"] if "categoryName" in flt else ""
        if wanted_category and not Fake.category_filter:
            return self._send(200, {"errors": [{"message": "Unknown filter field categoryName"}]})
        names = {101: "Weapons", 102: "Audio - SFX", 103: "Weapons"}
        nodes = []
        for mid, m in MODS.items():
            if m.get("available") is False or term not in m["name"].lower():
                continue
            if wanted_category and names.get(mid) != wanted_category:
                continue
            if "uploaderId" in flt and flt["uploaderId"][0]["value"] != ("77" if mid in (101, 103) else "88"):
                continue
            if "author" in flt and flt["author"][0]["value"] != m["author"]:
                continue
            nodes.append({"modId": mid, "name": m["name"], "summary": m["summary"], "version": m["version"],
                          "author": m["author"] if mode == "ok" else {"name": m["author"]},
                          "thumbnailUrl": "http://%s/img/%d.png" % (self.headers["Host"], mid), "pictureUrl": "",
                          "endorsements": m["endorsement_count"], "downloads": m["mod_unique_downloads"],
                          "updatedAt": "2026-11-01T00:00:00Z", "adultContent": m["contains_adult_content"]})
        nodes.sort(key=lambda n: n[sort], reverse=True)
        off, count = variables.get("offset") or 0, variables.get("count") or 20
        self._send(200, {"data": {"mods": {"totalCount": len(nodes), "nodes": nodes[off:off + count]}}})


def start_fake():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % server.server_address[1]
    os.environ["MODMANN_NEXUS_API"] = base + "/v1"
    os.environ["MODMANN_NEXUS_GRAPHQL"] = base + "/v2/graphql"
    os.environ["MODMANN_NEXUS_SITE"] = base + "/site"
    os.environ["MODMANN_GITHUB_API"] = base + "/gh"
    return server, base


def run():
    server, base = start_fake()
    import net
    import providers as p

    # links and descriptions
    link = p.parse_nxm("nxm://Fallout4/mods/101/files/11?key=GOODKEY&expires=999&user_id=7")
    assert (link.game, link.mod_id, link.file_id, link.key, link.expires) == ("fallout4", "101", "11", "GOODKEY", "999")
    assert p.parse_nxm("nxm://fallout4/mods/1/files/x") is None and p.parse_nxm("http://fallout4/mods/1/files/2") is None
    h = p.bbcode_to_html('[b]B[/b]<script>alert(1)</script>[url=javascript:x]bad[/url][url=https://ok.example/a?b=1&c=2]ok[/url][img]http://i/x.png[/img]')
    assert "<script" not in h and "javascript:" not in h and '<a href="https://ok.example/a?b=1&amp;c=2">ok</a>' in h and "x.png" not in h
    assert p.safe_file_name("../../etc/pass wd?.zip") == "pass wd_.zip" and p.safe_file_name("...") == "download.zip"

    # no key, bad key
    try:
        p.NexusProvider("").browse("trending")
        raise AssertionError("worked without a key")
    except p.NeedsKey:
        pass
    try:
        p.NexusProvider("WRONG").validate()
        raise AssertionError("bad key accepted")
    except p.ProviderError as e:
        assert "rejected" in str(e)

    for mode in ("ok", "alt"):
        Fake.graphql_mode = mode
        nx = p.NexusProvider("FREE")
        assert nx.validate()["name"] == "tester" and not nx.is_premium()
        trending = nx.browse("trending")
        assert [m.name for m in trending] == ["Better Pistols", "Pistol Sounds", "Grown-up Pistols"]      # hidden one dropped
        assert trending[2].adult and trending[0].url.endswith("/site/fallout4/mods/101") and nx.browse("trending", 1) == []
        found = nx.search("pistol")
        assert [m.name for m in found] == ["Better Pistols", "Pistol Sounds", "Grown-up Pistols"], (mode, nx.last_error)
        assert found[0].author == "Ana" and found[0].endorsements == 900 and nx.search_note == ""
        assert [m.name for m in nx.search("sounds")] == ["Pistol Sounds"] and nx.search("zzz") == []
        assert [m.name for m in nx.browse("downloads")][0] == "Better Pistols"
        assert [m.name for m in nx.search("https://www.nexusmods.com/fallout4/mods/102?tab=files")] == ["Pistol Sounds"]
        assert [m.id for m in nx.search("101")] == ["101"]

    # categories
    Fake.graphql_mode = "ok"
    nx = p.NexusProvider("FREE")
    cats = nx.categories()
    assert cats == [("9", "Armour"), ("5", "Audio - SFX"), ("12", "Weapons")], cats      # umbrella entry dropped, sorted
    assert [m.name for m in nx.browse("endorsements", 0, "12")] == ["Better Pistols", "Grown-up Pistols"]
    assert [m.name for m in nx.browse("trending", 0, "5")] == ["Pistol Sounds"] and "most endorsed" in nx.search_note
    assert [m.name for m in nx.browse("latest_added", 0, "12")] == ["Grown-up Pistols", "Better Pistols"] and nx.search_note == ""
    assert [m.name for m in nx.search("pistol", 0, "5")] == ["Pistol Sounds"] and nx.search("pistol", 0, "9") == []
    assert nx.browse("trending")[0].category == "Weapons"
    for style in ("plain", "any"):
        Fake.search_style = style
        assert len(nx.search("pistol")) == 3, style
        assert [m.name for m in nx.search("sounds", 0, "5")] == ["Pistol Sounds"], style
    Fake.search_style = "dead"                     # name filter useless: inside a category we pick matches ourselves
    assert nx.search("pistol") == [] and "0" in nx.search_trace
    assert [m.name for m in nx.search("sounds", 0, "5")] == ["Pistol Sounds"] and "picked" in nx.search_trace
    assert nx.search("sounds", 0, "12") == []
    Fake.search_style = "any"
    Fake.category_filter = False                 # the interface has no category filter: fall back, and say so
    nx = p.NexusProvider("FREE")
    nx.categories()
    assert [m.name for m in nx.browse("endorsements", 0, "12")] == ["Better Pistols", "Grown-up Pistols"]
    assert "unavailable" in nx.search_note and "no category field" in nx.last_error
    assert [m.name for m in nx.search("sounds", 0, "5")] == ["Pistol Sounds"] and nx.search("sounds", 0, "12") == []
    assert len(nx.search("pistol")) == 3 and nx.search_note == ""
    Fake.category_filter = True

    # an author's other mods: by account when possible, else by the author text
    nx = p.NexusProvider("FREE")
    full = nx.details(nx.search("better")[0]).summary
    assert (full.author, full.uploader, full.uploader_id) == ("Ana", "ana_account", "77")
    assert [m.name for m in nx.by_author(full)] == ["Better Pistols", "Grown-up Pistols"]      # same account, other author text
    Fake.author_filters = ["author"]
    nx = p.NexusProvider("FREE")
    assert [m.name for m in nx.by_author(full)] == ["Better Pistols"]
    Fake.author_filters = []
    nx = p.NexusProvider("FREE")
    try:
        nx.by_author(full)
        raise AssertionError("author list without a filter")
    except p.ProviderError as e:
        assert "cannot list" in str(e) and "no author field" in nx.last_error
    Fake.author_filters = ["uploaderId", "author"]

    nx = p.NexusProvider("FREE")
    assert nx.newer_file("101", "13").id == "11" and nx.newer_file("101", "11") is None and nx.newer_file("101", "12") is None
    assert nx.newer_file("101", "999") is None and nx.newer_file("102", "21") is None
    det = nx.details(nx.search("better")[0])
    assert [f.name for f in det.files] == ["Better Pistols Main", "4K patch"] and det.files[0].size == 3000 and det.files[1].size == 1024
    assert det.files[0].category == "Main" and det.files[0].uploaded == "2026-10-01"
    assert [(r.name, r.kind, r.mod_id, r.optional) for r in det.requirements] == [
        ("DLC: Far Harbor", "dlc", "", False), ("F4SE", "mod", "42147", False), ("Pistol Sounds", "mod", "102", False),
        ("Grown-up Pistols", "mod", "103", True), ("Something external", "external", "", False)]
    assert det.requirements[1].url.endswith("/fallout4/mods/42147") and det.requirements[4].url == "https://example.org/e"
    assert "<b>Hello</b>" in det.description_html and ("Version", "1.2") in det.extra and ("Category", "Weapons") in det.extra

    # free accounts are sent to the site; the link they get there works once
    try:
        nx.download_url(det.summary, det.files[0])
        raise AssertionError("free account got a direct download")
    except p.NeedsWebsite as e:
        assert e.url == base + "/site/fallout4/mods/101?tab=files&file_id=11&nmm=1"
    mod, f = nx.file_info("101", "11")
    assert mod.name == "Better Pistols" and f.file_name.endswith(".zip") and f.category == "Main"
    url = nx.download_url(mod, f, "GOODKEY", "999")
    assert url == base + "/cdn/101/11.zip"
    try:
        nx.download_url(mod, f, "OLDKEY", "1")
        raise AssertionError("expired link accepted")
    except p.ProviderError as e:
        assert "expired" in str(e)
    premium = p.NexusProvider("PREMIUM")
    assert premium.is_premium() and premium.download_url(mod, f) == url

    # downloading
    tmp = tempfile.mkdtemp()
    seen = []
    dest = net.download(url, os.path.join(tmp, "d", "a.zip"), lambda d, t: seen.append((d, t)))
    assert zipfile.ZipFile(dest).namelist() == ["BetterPistols.esp", "Textures/pistol.dds"] and seen[-1][0] == seen[-1][1] > 0
    # download servers hand out names with spaces, apostrophes and brackets, unencoded
    awkward = base + "/cdn/1151/15/Caliente's Beautiful Bodies - v2.7.2 (1)-15-2-7-2.7z?expires=1&md5=aB_c-d&user_id=7"
    assert net.clean_url(awkward) == base + "/cdn/1151/15/Caliente's%20Beautiful%20Bodies%20-%20v2.7.2%20(1)-15-2-7-2.7z?expires=1&md5=aB_c-d&user_id=7"
    assert net.clean_url(net.clean_url(awkward)) == net.clean_url(awkward)          # already-encoded stays as it is
    got = net.download(awkward, os.path.join(tmp, "d", "awkward.7z"))
    assert os.path.getsize(got) > 0 and "%20Beautiful%20" in Fake.requests[-1]
    os.unlink(got)
    os.unlink(dest)
    os.rmdir(os.path.join(tmp, "d"))
    os.makedirs(os.path.join(tmp, "d"))
    try:
        net.download(url, os.path.join(tmp, "b.zip"), cancelled=lambda: True)
        raise AssertionError("cancel ignored")
    except net.NetError:
        assert os.listdir(tmp) == ["d"]
    for bad in ("file:///etc/passwd", "ftp://x/y"):
        try:
            net.download(bad, os.path.join(tmp, "c.zip"))
            raise AssertionError("opened %s" % bad)
        except net.NetError:
            pass
    try:
        net.get_json(base + "/nope")
        raise AssertionError("404 accepted")
    except net.NetError as e:
        assert e.status == 404

    # search falls back when the undocumented endpoint misbehaves
    Fake.graphql_mode = "broken"
    nx = p.NexusProvider("FREE")
    assert [m.name for m in nx.search("sounds")] == ["Pistol Sounds"] and "unavailable" in nx.search_note
    assert "doesn't exist" in nx.last_error
    assert len(nx.browse("endorsements")) == 3 and "unavailable" in nx.search_note
    det = nx.details(nx.search("102")[0])
    assert det.requirements == [] and "could not be read" in det.requirements_note and len(det.files) == 2

    # GitHub
    gh = p.GitHubProvider()
    repos = gh.browse("f4se")
    assert [(r.id, r.endorsements, r.author) for r in repos] == [("ana/f4se-thing", 42, "ana")] and gh.search("nothing") == []
    det = gh.details(repos[0])
    assert [(f.name, f.version, f.category, f.size) for f in det.files] == [("thing.zip", "v2.0", "Release", 1234)]
    assert "&lt;b&gt;raw" in det.description_html and gh.download_url(repos[0], det.files[0]).endswith("/cdn/x/y.zip")
    try:
        gh.details(p.ModSummary("github", "../../x", "x"))
        raise AssertionError("bad repo name accepted")
    except p.ProviderError:
        pass
    server.shutdown()
    print("providers ok")


if __name__ == "__main__":
    run()
    sys.exit(0)
