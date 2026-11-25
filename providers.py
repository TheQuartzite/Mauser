"""Mod sources the built-in browser can search and install from.

Each provider turns a site's API into the same small set of objects (ModSummary,
ModDetails, ModFile), so the browser screen does not care which site it shows.

Nexus Mods notes:
  * The documented v1 API needs the user's personal API key for everything.
  * It only hands out download addresses directly to Premium members. Everyone
    else gets them through the site's "Mod manager download" button, which
    produces an nxm:// link carrying a short-lived key.
  * Search uses the newer GraphQL endpoint, which Nexus has not documented. The
    code asks the endpoint which fields exist before building a query, and falls
    back to the documented lists if that fails.
"""
from __future__ import annotations

import html
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Optional

import net
from common import APP_NAME, APP_VERSION

GAME_DOMAIN = "fallout4"
NEXUS_GAME_ID = 1151
ARCHIVE_EXTS = (".zip", ".7z", ".rar")


class ProviderError(Exception):
    pass


class NeedsKey(ProviderError):
    """The provider needs an API key that has not been entered yet."""


class NeedsWebsite(ProviderError):
    """The file can only be fetched by pressing a button on the site."""

    def __init__(self, url: str, message: str):
        super().__init__(message)
        self.url = url


@dataclass
class ModSummary:
    provider: str
    id: str
    name: str
    summary: str = ""
    author: str = ""
    version: str = ""
    thumbnail: str = ""
    picture: str = ""
    endorsements: int = 0
    downloads: int = 0
    updated: str = ""
    adult: bool = False
    url: str = ""
    category: str = ""
    category_id: str = ""
    uploader: str = ""            # account that published it (the author field is free text)
    uploader_id: str = ""


@dataclass
class ModFile:
    id: str
    name: str
    file_name: str = ""
    version: str = ""
    category: str = ""
    size: int = 0                 # bytes
    description: str = ""
    uploaded: str = ""
    url: str = ""                 # direct address when the provider has one


@dataclass
class Requirement:
    name: str
    url: str = ""
    notes: str = ""
    mod_id: str = ""              # set when it is another mod on the same site
    kind: str = "mod"             # "mod", "dlc" or "external"

    @property
    def optional(self) -> bool:
        """What the author's note says. No note, or one that does not say, counts as required."""
        return requirement_is_optional(self.notes)


_REQ_HARD = re.compile(r"\b(hard\s+requirement|mandatory|must\s+have|always\s+required|is\s+required|required\s*[!.\]]*$)", re.I)
_REQ_SOFT = re.compile(r"\b(optional\w*|recommend\w*|soft\s+requirement|not\s+required|only\s+(?:needed|required|if|for|when)|"
                       r"if\s+you\s+(?:use|want|have|are\s+using)|(?:needed|required)\s+(?:only\s+)?(?:for|if|when)|"
                       r"for\s+the\s+\w+\s+(?:patch|version|option)|suggested|nice\s+to\s+have)", re.I)


def requirement_is_optional(notes: str) -> bool:
    notes = notes or ""
    if _REQ_HARD.search(notes) and not re.search(r"\boptional", notes, re.I):
        return False
    return bool(_REQ_SOFT.search(notes))


@dataclass
class ModDetails:
    summary: ModSummary
    description_html: str = ""
    files: list = field(default_factory=list)
    requirements: list = field(default_factory=list)
    requirements_note: str = ""   # why the list is empty, when it could not be read
    extra: list = field(default_factory=list)      # (label, value) rows for the info panel


@dataclass
class NxmLink:
    game: str
    mod_id: str
    file_id: str
    key: str = ""
    expires: str = ""


def parse_nxm(url: str) -> Optional[NxmLink]:
    """nxm://fallout4/mods/123/files/456?key=...&expires=..."""
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() != "nxm":
        return None
    m = re.fullmatch(r"/mods/(\d+)/files/(\d+)/?", parts.path)
    if not m or not parts.netloc:
        return None
    q = urllib.parse.parse_qs(parts.query)
    return NxmLink(parts.netloc.lower(), m.group(1), m.group(2), (q.get("key") or [""])[0],
                   (q.get("expires") or [""])[0])


def _date(value) -> str:
    return str(value or "")[:10]


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


_BB_SIMPLE = {"b": "b", "i": "i", "u": "u", "s": "s", "center": "center", "quote": "blockquote",
              "code": "pre", "li": "li"}


def bbcode_to_html(text: str) -> str:
    """Convert the BBCode used in Nexus descriptions to simple, safe HTML.
    Everything is escaped first; only known tags are turned back into markup."""
    text = re.sub(r"<br\s*/?>", "\n", text or "")
    out = html.escape(text, quote=True)
    for bb, tag in _BB_SIMPLE.items():
        out = re.sub(r"\[%s\]" % bb, "<%s>" % tag, out, flags=re.I)
        out = re.sub(r"\[/%s\]" % bb, "</%s>" % tag, out, flags=re.I)

    def make_link(url: str, label: str) -> str:
        url = html.unescape(url).strip().strip("'\"")
        if not url.lower().startswith(("http://", "https://")):
            return label
        return '<a href="%s">%s</a>' % (html.escape(url, quote=True), label)

    out = re.sub(r"\[url=(.*?)\](.*?)\[/url\]", lambda m: make_link(m.group(1), m.group(2)), out, flags=re.I | re.S)
    out = re.sub(r"\[url\](.*?)\[/url\]", lambda m: make_link(m.group(1), m.group(1)), out, flags=re.I | re.S)
    out = re.sub(r"\[img.*?\](.*?)\[/img\]", "", out, flags=re.I | re.S)
    out = re.sub(r"\[youtube\](.*?)\[/youtube\]",
                 lambda m: '<a href="https://www.youtube.com/watch?v=%s">Video</a>'
                           % re.sub(r"[^A-Za-z0-9_-]", "", html.unescape(m.group(1))),
                 out, flags=re.I | re.S)
    out = re.sub(r"\[\*\]", "<br>&bull; ", out)
    out = re.sub(r"\[/?(list|color|font|size|spoiler|line|left|right|heading)(=[^\]]*)?\]", "", out, flags=re.I)
    out = re.sub(r"\[/?[a-z]+(=[^\]\n]{0,80})?\]", "", out, flags=re.I)     # anything else unknown
    out = re.sub(r"\n{3,}", "\n\n", out).replace("\n", "<br>")
    return out


class Provider:
    id = ""
    name = ""
    needs_key = False
    key_help = ""

    def feeds(self) -> list:
        """[(key, label)] of browsable lists."""
        return []

    def categories(self) -> list:
        """[(id, name)] the site sorts mods into; empty if it has none."""
        return []

    def browse(self, feed: str, page: int = 0, category: str = "") -> list:
        raise NotImplementedError

    def search(self, term: str, page: int = 0, category: str = "") -> list:
        raise NotImplementedError

    def by_author(self, mod: ModSummary, page: int = 0) -> list:
        """Other mods by whoever published `mod`."""
        raise ProviderError("%s cannot list an author's other mods." % self.name)

    def details(self, mod: ModSummary) -> ModDetails:
        raise NotImplementedError

    def download_url(self, mod: ModSummary, file: ModFile) -> str:
        """Direct address for a file, or raise NeedsWebsite."""
        raise NotImplementedError


class NexusProvider(Provider):
    id = "nexus"
    name = "Nexus Mods"
    needs_key = True
    no_key_text = "Nexus Mods needs your personal API key. Add it in Settings."
    key_help = ("Sign in at nexusmods.com, open your account settings, go to \"API Keys\" and copy the "
                "Personal API Key at the bottom of that page.")
    PAGE = 20

    search_trace = ""           # what the last search tried, for the activity board
    def __init__(self, api_key: str = ""):
        self.api_key = api_key.strip()
        self.api = os.environ.get("MODMANN_NEXUS_API", "https://api.nexusmods.com/v1").rstrip("/")
        self.graphql = os.environ.get("MODMANN_NEXUS_GRAPHQL", "https://api.nexusmods.com/v2/graphql")
        self.site = os.environ.get("MODMANN_NEXUS_SITE", "https://www.nexusmods.com").rstrip("/")
        self.user: Optional[dict] = None
        self._schema: Optional[dict] = None
        self._categories: Optional[list] = None
        self.search_note = ""             # set when search had to fall back
        self.last_error = ""              # technical detail of the last GraphQL failure, for bug reports

    # -- plumbing -----------------------------------------------------------

    def _headers(self) -> dict:
        h = {"Application-Name": APP_NAME, "Application-Version": APP_VERSION.split()[0]}
        if self.api_key:
            h["apikey"] = self.api_key
        return h

    def _v1(self, path: str):
        if not self.api_key:
            raise NeedsKey(self.no_key_text)
        try:
            return net.get_json("%s/%s" % (self.api, path.lstrip("/")), self._headers())
        except net.NetError as e:
            if e.status == 401:
                raise ProviderError("Nexus Mods rejected the API key. Check it in Settings.")
            if e.status == 429:
                raise ProviderError("Nexus Mods rate limit reached. Try again in a while.")
            raise ProviderError(str(e))

    def _gql(self, query: str, variables: Optional[dict] = None) -> dict:
        try:
            doc = net.post_json(self.graphql, {"query": query, "variables": variables or {}}, self._headers())
        except net.NetError as e:
            self.last_error = "%s %s" % (e, e.body[:300])
            raise ProviderError(str(e))
        if not isinstance(doc, dict) or doc.get("errors") or not isinstance(doc.get("data"), dict):
            self.last_error = str((doc or {}).get("errors") if isinstance(doc, dict) else doc)[:600]
            raise ProviderError("Nexus search answered with an error")
        return doc["data"]

    def validate(self) -> dict:
        """Check the key; returns the account info (name, is_premium, ...)."""
        self.user = self._v1("users/validate.json")
        if not isinstance(self.user, dict):
            self.user = {}
        return self.user

    def is_premium(self) -> bool:
        if self.user is None and self.api_key:
            try:
                self.validate()
            except ProviderError:
                return False
        return bool(self.user and self.user.get("is_premium"))

    # -- lists --------------------------------------------------------------

    def feeds(self) -> list:
        return [("trending", "Trending"), ("latest_added", "Newest"), ("latest_updated", "Recently updated"),
                ("endorsements", "Most endorsed"), ("downloads", "Most downloaded")]

    def categories(self) -> list:
        """The game's mod categories as Nexus lists them (Weapons, Armour, ...)."""
        if self._categories is None:
            game = self._v1("games/%s.json" % GAME_DOMAIN)
            found = []
            for c in ((game or {}).get("categories") or []) if isinstance(game, dict) else []:
                if isinstance(c, dict) and c.get("category_id") and c.get("name"):
                    found.append((str(c["category_id"]), str(c["name"])))
            # the first entry is the game's own umbrella category on Nexus; it is not a real filter
            if found and found[0][1].strip().lower() in ("fallout 4", GAME_DOMAIN):
                found = found[1:]
            self._categories = sorted(found, key=lambda c: c[1].lower())
        return self._categories

    def _category_name(self, category_id) -> str:
        if not category_id or self._categories is None:
            return ""
        return dict(self._categories).get(str(category_id), "")

    def _from_v1(self, m: dict) -> Optional[ModSummary]:
        if not isinstance(m, dict) or not m.get("mod_id") or m.get("available") is False or not m.get("name"):
            return None
        mod_id = str(m["mod_id"])
        summary = self._from_v1_fields(m, mod_id)
        summary.category = self._category_name(m.get("category_id"))
        summary.category_id = str(m.get("category_id") or "")
        summary.uploader = str(m.get("uploaded_by") or "")
        user = m.get("user") if isinstance(m.get("user"), dict) else {}
        summary.uploader_id = str(user.get("member_id") or "")
        summary.uploader = summary.uploader or str(user.get("name") or "")
        return summary

    def _from_v1_fields(self, m: dict, mod_id: str) -> ModSummary:
        return ModSummary(self.id, mod_id, html.unescape(str(m.get("name"))), html.unescape(str(m.get("summary") or "")),
                          html.unescape(str(m.get("author") or m.get("uploaded_by") or "")), str(m.get("version") or ""),
                          str(m.get("picture_url") or ""), str(m.get("picture_url") or ""),
                          _int(m.get("endorsement_count")), _int(m.get("mod_unique_downloads") or m.get("mod_downloads")),
                          _date(m.get("updated_time")), bool(m.get("contains_adult_content")),
                          "%s/%s/mods/%s" % (self.site, GAME_DOMAIN, mod_id))

    _CATEGORY_SORT = {"trending": "endorsements", "latest_added": "createdAt", "latest_updated": "updatedAt",
                      "endorsements": "endorsements", "downloads": "downloads"}

    def _v1_pool(self, feeds: tuple) -> list:
        seen, out = set(), []
        for feed in feeds:
            data = self._v1("games/%s/mods/%s.json" % (GAME_DOMAIN, feed))
            for m in (data if isinstance(data, list) else []):
                s = self._from_v1(m)
                if s and s.id not in seen:
                    seen.add(s.id)
                    out.append(s)
        return out

    def _fallback(self, words: list, category: str, why: str) -> list:
        """When the search interface fails: look through the three documented lists instead."""
        print("Nexus search fell back to the documented lists. Detail: %s" % self.last_error)
        pool = self._v1_pool(("trending", "latest_updated", "latest_added"))
        out = [s for s in pool
               if all(w in (s.name + " " + s.summary).lower() for w in words)
               and (not category or s.category_id == str(category))]
        self.search_note = why
        return out

    def browse(self, feed: str, page: int = 0, category: str = "") -> list:
        self.search_note = ""
        if category or feed in ("endorsements", "downloads"):
            try:
                found = self._gql_mods("", page, self._CATEGORY_SORT.get(feed, "endorsements"), category)
                if category and feed == "trending":
                    self.search_note = "Nexus has no trending list per category, so this shows the most endorsed."
                return found
            except ProviderError:
                if page > 0:
                    return []
                if category:
                    return self._fallback([], category, "Nexus's category lists are unavailable right now, so this "
                                                        "only looks through the trending and recent mods.")
                self.search_note = "That list is unavailable right now; showing trending mods instead."
                feed = "trending"
        if page > 0:
            return []                       # the documented lists are a single page
        note = self.search_note
        out = self._v1_pool((feed,))
        self.search_note = note
        return out

    def search(self, term: str, page: int = 0, category: str = "") -> list:
        self.search_note = ""
        term = term.strip()
        m = re.search(r"nexusmods\.com/%s/mods/(\d+)" % GAME_DOMAIN, term) or re.fullmatch(r"(\d{1,7})", term)
        if m:                               # a mod page address or id was pasted
            if page > 0:
                return []
            s = self._from_v1(self._v1("games/%s/mods/%s.json" % (GAME_DOMAIN, m.group(1))))
            return [s] if s else []
        try:
            return self._gql_mods(term, page, "endorsements", category)
        except ProviderError:
            if page > 0:
                return []
            return self._fallback(term.lower().split(), category,
                                  "Nexus search is unavailable right now, so this only looks through the trending "
                                  "and recent lists. Pasting a mod page address into the search box always works.")

    # -- GraphQL ------------------------------------------------------------

    def _load_schema(self) -> dict:
        if self._schema is not None:
            return self._schema
        schema = {"mod": {}, "filter": [], "sort": []}
        q = """query { mod: __type(name: "Mod") { fields { name type { kind name ofType { kind name ofType { kind name } } } } }
                       filter: __type(name: "ModsFilter") { inputFields { name } }
                       sort: __type(name: "ModsSort") { inputFields { name } } }"""
        try:
            data = self._gql(q)
            for f in ((data.get("mod") or {}).get("fields") or []):
                t = f.get("type") or {}
                while t.get("ofType"):
                    t = t["ofType"]
                schema["mod"][f["name"]] = t.get("kind") or ""
            schema["filter"] = [f["name"] for f in ((data.get("filter") or {}).get("inputFields") or [])]
            schema["sort"] = [f["name"] for f in ((data.get("sort") or {}).get("inputFields") or [])]
        except (ProviderError, KeyError, TypeError, AttributeError):
            pass
        self._schema = schema
        return schema

    def _selection(self, schema: dict) -> str:
        known = schema["mod"]
        wanted = ["modId", "name", "summary", "version", "author", "pictureUrl", "thumbnailUrl", "endorsements",
                  "downloads", "updatedAt", "createdAt", "adultContent", "modCategory"]
        if not known:
            return "modId name summary version author pictureUrl thumbnailUrl endorsements downloads updatedAt adultContent uploader { name }"
        parts = []
        for f in wanted:
            if f in known:
                parts.append(f if known[f] in ("SCALAR", "ENUM") else "%s { name }" % f)
        if "uploader" in known:
            parts.append("uploader { name }")
        if "modId" not in known or "name" not in known:
            raise ProviderError("Nexus search has changed shape")
        return " ".join(parts)

    def by_author(self, mod: ModSummary, page: int = 0) -> list:
        self.search_note = ""
        fields = self._load_schema()["filter"]
        options = [("uploaderId", mod.uploader_id), ("uploader", mod.uploader), ("author", mod.author)]
        options = [o for o in options if o[1] and (not fields or o[0] in fields)]
        if not options:
            self.last_error = "no author field among filter fields: %s" % ", ".join(fields)
            raise ProviderError("Nexus cannot list this author's mods right now.")
        failed = 0
        found: list = []
        for option in options:              # most exact first; move on if one finds nothing
            try:
                found = self._gql_mods("", page, "endorsements", extra=[option])
            except ProviderError:
                failed += 1
                continue
            if found:
                return found
        if failed == len(options):
            print("Nexus author list failed. Detail: %s" % self.last_error)
            raise ProviderError("Nexus cannot list this author's mods right now.")
        return found

    def _gql_mods(self, term: str, page: int, sort: str, category: str = "", extra: Optional[list] = None) -> list:
        schema = self._load_schema()
        category_filters: list = list(extra) if extra else [None]
        if category:
            name = self._category_name(category) or dict(self.categories()).get(str(category), "")
            options = {"categoryName": name, "categoryId": str(category), "modCategoryId": str(category)}
            known = [k for k in options if k in schema["filter"] and options[k]]
            if schema["filter"] and not known:
                self.last_error = "no category field among filter fields: %s" % ", ".join(schema["filter"])
                raise ProviderError("Nexus category lists are unavailable")
            category_filters = [(k, options[k]) for k in (known or ["categoryName"]) if options[k]]
            if not category_filters:
                raise ProviderError("Unknown category")
        game_keys = [k for k in ("gameDomainName", "gameDomain") if k in schema["filter"]] or \
                    ["gameDomainName", "gameDomain"]
        name_keys = [k for k in ("name", "nameStemmed") if k in schema["filter"]] or ["name"]
        sort_key = sort if (not schema["sort"] or sort in schema["sort"]) else ""
        selection = self._selection(schema)
        query = ("query Mods($filter: ModsFilter, $sort: [ModsSort!], $offset: Int, $count: Int) { "
                 "mods(filter: $filter, sort: $sort, offset: $offset, count: $count) { totalCount nodes { %s } } }"
                 % selection)
        error: Optional[ProviderError] = None
        # The search interface is undocumented, so try the forms it might expect and take the
        # first that finds something: the plain words, the words between wildcards, then an exact-style match.
        forms = [(k, o, v) for k in name_keys
                 for o, v in (("WILDCARD", "%s"), ("WILDCARD", "*%s*"), ("MATCHES", "%s"))] if term else [None]
        attempts = [(g, f, c) for g in game_keys for f in forms for c in category_filters]
        answered = False
        trace = []

        def ask(flt, offset, count):
            return self._gql(query, {"filter": flt, "offset": offset, "count": count,
                                     "sort": [{sort_key: {"direction": "DESC"}}] if sort_key else None})

        for game_key, form, cat in attempts:
            flt = {game_key: [{"value": GAME_DOMAIN, "op": "EQUALS"}]}
            if term:
                flt[form[0]] = [{"value": form[2] % term, "op": form[1]}]
            if cat is not None:
                flt[cat[0]] = [{"value": cat[1], "op": "EQUALS"}]
            label = "%s %s '%s'" % (form[0], form[1], form[2] % term) if term else "list"
            try:
                data = ask(flt, page * self.PAGE, self.PAGE)
            except ProviderError as e:
                error = e
                trace.append("%s: refused" % label)
                continue
            answered = True
            nodes = ((data.get("mods") or {}).get("nodes")) or []
            found = [s for s in (self._from_gql(n) for n in nodes) if s]
            trace.append("%s: %d" % (label, len(found)))
            if found or not term:
                self.search_trace = "; ".join(trace)
                return found
        if answered and term and category and page == 0:
            # Last resort inside a category: read its most endorsed mods and pick out the matching ones here.
            words = term.lower().split()
            found, seen = [], 0
            for game_key in game_keys[:1]:
                for cat in category_filters[:1]:
                    for chunk in range(3):
                        flt = {game_key: [{"value": GAME_DOMAIN, "op": "EQUALS"}], cat[0]: [{"value": cat[1], "op": "EQUALS"}]}
                        try:
                            nodes = ((ask(flt, chunk * 100, 100).get("mods") or {}).get("nodes")) or []
                        except ProviderError:
                            break
                        seen += len(nodes)
                        for s in (self._from_gql(n) for n in nodes):
                            if s and all(w in (s.name + " " + s.summary).lower() for w in words):
                                found.append(s)
                        if len(nodes) < 100:
                            break
            trace.append("picked from the category's top %d: %d" % (seen, len(found)))
            self.search_trace = "; ".join(trace)
            return found
        self.search_trace = "; ".join(trace)
        if answered:
            return []
        raise error or ProviderError("Nexus search failed")

    def _from_gql(self, n: dict) -> Optional[ModSummary]:
        if not isinstance(n, dict) or not n.get("modId") or not n.get("name"):
            return None
        author = n.get("author")
        if isinstance(author, dict):
            author = author.get("name")
        if not author and isinstance(n.get("uploader"), dict):
            author = n["uploader"].get("name")
        mod_id = str(n["modId"])
        category = n.get("modCategory")
        uploader = n.get("uploader") if isinstance(n.get("uploader"), dict) else {}
        return ModSummary(uploader=str(uploader.get("name") or ""), category=str((category or {}).get("name") or "") if isinstance(category, dict) else "",
                          provider=self.id, id=mod_id, name=html.unescape(str(n["name"])),
                          summary=html.unescape(str(n.get("summary") or "")), author=html.unescape(str(author or "")),
                          version=str(n.get("version") or ""),
                          thumbnail=str(n.get("thumbnailUrl") or n.get("pictureUrl") or ""),
                          picture=str(n.get("pictureUrl") or ""), endorsements=_int(n.get("endorsements")),
                          downloads=_int(n.get("downloads")),
                          updated=_date(n.get("updatedAt") or n.get("createdAt")),
                          adult=bool(n.get("adultContent")),
                          url="%s/%s/mods/%s" % (self.site, GAME_DOMAIN, mod_id))

    def _requirements(self, mod_id: str) -> tuple:
        query = ("query Req($modId: ID!, $gameId: ID!) { mod(modId: $modId, gameId: $gameId) { modRequirements { "
                 "nexusRequirements { nodes { modId modName url notes externalRequirement } } "
                 "dlcRequirements { gameExpansion { name } notes } } } }")
        try:
            data = self._gql(query, {"modId": str(mod_id), "gameId": str(NEXUS_GAME_ID)})
        except ProviderError:
            return [], "The requirements list could not be read; check the description or the mod page."
        req = ((data.get("mod") or {}).get("modRequirements")) or {}
        out = []
        for d in (req.get("dlcRequirements") or []):
            name = ((d or {}).get("gameExpansion") or {}).get("name")
            if name:
                out.append(Requirement("DLC: %s" % name, "", str((d or {}).get("notes") or ""), kind="dlc"))
        nodes = ((req.get("nexusRequirements") or {}).get("nodes")) or []
        for n in nodes:
            if not isinstance(n, dict) or not n.get("modName"):
                continue
            url = str(n.get("url") or "")
            mod_id = str(n.get("modId") or "")
            if not mod_id:                    # some entries only carry the page address
                m = re.search(r"nexusmods\.com/%s/mods/(\d+)" % GAME_DOMAIN, url)
                mod_id = m.group(1) if m else ""
            if not url and mod_id:
                url = "%s/%s/mods/%s" % (self.site, GAME_DOMAIN, mod_id)
            out.append(Requirement(html.unescape(str(n["modName"])), url, html.unescape(str(n.get("notes") or "")),
                                   mod_id, "mod" if mod_id else "external"))
        return out, ""

    # -- one mod ------------------------------------------------------------

    def details(self, mod: ModSummary) -> ModDetails:
        m = self._v1("games/%s/mods/%s.json" % (GAME_DOMAIN, mod.id))
        full = self._from_v1(m) or mod
        if not full.thumbnail:
            full.thumbnail = mod.thumbnail
        det = ModDetails(full, bbcode_to_html(str((m or {}).get("description") or "")))
        if not full.category:
            try:
                self.categories()
                full.category = self._category_name((m or {}).get("category_id"))
            except ProviderError:
                pass
        det.extra = [("Category", full.category), ("Version", full.version), ("Updated", full.updated), ("Endorsements", "{:,}".format(full.endorsements)),
                     ("Downloads", "{:,}".format(full.downloads))]
        listing = self._v1("games/%s/mods/%s/files.json" % (GAME_DOMAIN, mod.id))
        order = {"MAIN": 0, "UPDATE": 1, "OPTIONAL": 2, "MISCELLANEOUS": 3}
        files = []
        for f in ((listing or {}).get("files") or []):
            cat = str(f.get("category_name") or "")
            if cat not in order:            # old versions and archived files are hidden
                continue
            size = _int(f.get("size_in_bytes")) or _int(f.get("size_kb") or f.get("size")) * 1024
            files.append(ModFile(str(f.get("file_id")), str(f.get("name") or f.get("file_name") or "File"),
                                 str(f.get("file_name") or ""), str(f.get("version") or ""), cat.capitalize(), size,
                                 re.sub(r"<br\s*/?>", "\n", str(f.get("description") or "")),
                                 _date(f.get("uploaded_time"))))
        files.sort(key=lambda x: (order.get(x.category.upper(), 9), x.uploaded), reverse=False)
        det.files = files
        det.requirements, det.requirements_note = self._requirements(mod.id)
        return det

    def files_page(self, mod_id: str, file_id: str) -> str:
        """Page that shows the download buttons for one file with 'Mod manager download' selected."""
        return "%s/%s/mods/%s?tab=files&file_id=%s&nmm=1" % (self.site, GAME_DOMAIN, mod_id, file_id)

    def download_url(self, mod: ModSummary, file: ModFile, key: str = "", expires: str = "") -> str:
        path = "games/%s/mods/%s/files/%s/download_link.json" % (GAME_DOMAIN, mod.id, file.id)
        if key and expires:
            path += "?" + urllib.parse.urlencode({"key": key, "expires": expires})
        elif not self.is_premium():
            raise NeedsWebsite(self.files_page(mod.id, file.id),
                               "Nexus only gives direct downloads to Premium members. The file's page has been "
                               "opened in your browser: press \"Slow download\" (or \"Mod manager download\") "
                               "there and this app takes over.")
        try:
            links = self._v1(path)
        except ProviderError as e:
            if "error 403" in str(e) and not (key and expires):
                raise NeedsWebsite(self.files_page(mod.id, file.id),
                                   "Nexus would not give a direct download for this account. The file's page "
                                   "has been opened in your browser: press the download button there.")
            if "error 4" in str(e) and key:
                raise ProviderError("That download link has expired or belongs to another account. Press the "
                                    "download button on the Nexus page again.")
            raise
        for link in (links if isinstance(links, list) else []):
            uri = str((link or {}).get("URI") or "")
            if uri.lower().startswith(("https://", "http://")):
                return uri
        raise ProviderError("Nexus did not return a download address for this file.")

    def category_of(self, mod_id: str) -> str:
        """The site's category name for a mod ('' when it has none or cannot be read)."""
        if self._categories is None:
            try:
                self.categories()
            except ProviderError:
                pass
        m = self._v1("games/%s/mods/%s.json" % (GAME_DOMAIN, mod_id)) or {}
        return self._category_name(m.get("category_id")) or ""

    def newer_file(self, mod_id: str, file_id: str) -> Optional[ModFile]:
        """The file that has replaced an installed one on the site, or None when it is still current
        (or nothing can be said). Follows the site's own "this file replaces that one" records."""
        listing = self._v1("games/%s/mods/%s/files.json" % (GAME_DOMAIN, mod_id)) or {}
        files = {}
        for f in listing.get("files") or []:
            size = _int(f.get("size_in_bytes")) or _int(f.get("size_kb") or f.get("size")) * 1024
            files[str(f.get("file_id"))] = ModFile(
                str(f.get("file_id")), str(f.get("name") or f.get("file_name") or "File"), str(f.get("file_name") or ""),
                str(f.get("version") or ""), str(f.get("category_name") or "").capitalize(), size,
                re.sub(r"<br\s*/?>", "\n", str(f.get("description") or "")), _date(f.get("uploaded_time")))
        chain = {str(u.get("old_file_id")): str(u.get("new_file_id")) for u in listing.get("file_updates") or []
                 if isinstance(u, dict)}
        current = ("Main", "Update", "Optional", "Miscellaneous")
        latest, seen = str(file_id), set()
        while latest in chain and latest not in seen:
            seen.add(latest)
            latest = chain[latest]
        if latest != str(file_id) and latest in files and files[latest].category in current:
            return files[latest]
        mine = files.get(str(file_id))
        if mine is None or mine.category not in current:
            # replaced without a record, or deleted: offer the current file of the same name, if there is one
            name = mine.name if mine is not None else ""
            same = [f for f in files.values() if f.category in current and f.name == name and f.id != str(file_id)]
            if same:
                return sorted(same, key=lambda f: f.uploaded)[-1]
        return None

    def file_info(self, mod_id: str, file_id: str) -> tuple:
        """(ModSummary, ModFile) for an nxm:// link."""
        mod = self._from_v1(self._v1("games/%s/mods/%s.json" % (GAME_DOMAIN, mod_id)))
        if mod is None:
            raise ProviderError("That mod is not available on Nexus Mods.")
        f = self._v1("games/%s/mods/%s/files/%s.json" % (GAME_DOMAIN, mod_id, file_id)) or {}
        size = _int(f.get("size_in_bytes")) or _int(f.get("size_kb") or f.get("size")) * 1024
        return mod, ModFile(str(file_id), str(f.get("name") or mod.name), str(f.get("file_name") or ""),
                            str(f.get("version") or ""), str(f.get("category_name") or "").capitalize(), size,
                            re.sub(r"<br\s*/?>", "\n", str(f.get("description") or "")))


_GITHUB_BAIT = ("hack", "cheat", "crack", "keygen", "trainer", "aimbot", "unlocker", "free download", "exploit")


class GitHubProvider(Provider):
    """Projects on GitHub that publish releases. Mostly script-extender plugins and tools."""
    id = "github"
    name = "GitHub"

    def __init__(self):
        self.api = os.environ.get("MODMANN_GITHUB_API", "https://api.github.com").rstrip("/")
        self.search_note = ""
        self.last_error = ""

    def _get(self, path: str, accept: str = "application/vnd.github+json"):
        try:
            return net.get_json("%s/%s" % (self.api, path.lstrip("/")), {"Accept": accept})
        except net.NetError as e:
            self.last_error = "%s %s" % (e, e.body[:300])
            if e.status == 403 or e.status == 429:
                raise ProviderError("GitHub's hourly limit for anonymous requests was reached. Try again later.")
            raise ProviderError(str(e))

    def feeds(self) -> list:
        return [("f4se", "Script extender plugins"), ("fallout4", "Fallout 4 projects")]

    def _repos(self, query: str, page: int) -> list:
        self.search_note = ("GitHub hosts source code and anyone can publish there. Only install from projects "
                            "you recognise; only those with release archives can be installed.")
        q = urllib.parse.urlencode({"q": query, "sort": "stars", "order": "desc", "per_page": 20, "page": page + 1})
        data = self._get("search/repositories?" + q)
        out = []
        for r in ((data or {}).get("items") or []):
            if not isinstance(r, dict) or not r.get("full_name"):
                continue
            # GitHub search also turns up bait projects ("hack", "cheat", "crack" downloads). Leave them out.
            text = ("%s %s" % (r.get("name") or "", r.get("description") or "")).lower()
            if any(word in text for word in _GITHUB_BAIT):
                continue
            owner = r.get("owner") or {}
            out.append(ModSummary(self.id, str(r["full_name"]), str(r.get("name") or r["full_name"]),
                                  str(r.get("description") or ""), str(owner.get("login") or ""), "",
                                  str(owner.get("avatar_url") or ""), str(owner.get("avatar_url") or ""),
                                  _int(r.get("stargazers_count")), 0, _date(r.get("pushed_at")), False,
                                  str(r.get("html_url") or "")))
        return out

    def browse(self, feed: str, page: int = 0, category: str = "") -> list:
        return self._repos("f4se fallout4 in:name,description,topics" if feed == "f4se"
                           else "fallout4 mod in:name,description,topics", page)

    def by_author(self, mod: ModSummary, page: int = 0) -> list:
        if not re.fullmatch(r"[A-Za-z0-9-]+", mod.author or ""):
            raise ProviderError("Unknown GitHub account.")
        found = self._repos("user:%s fallout" % mod.author, page)
        self.search_note = ""
        return found

    def search(self, term: str, page: int = 0, category: str = "") -> list:
        return self._repos("%s fallout4 in:name,description,topics" % term.strip(), page)

    def details(self, mod: ModSummary) -> ModDetails:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", mod.id):
            raise ProviderError("Not a GitHub project name.")
        releases = self._get("repos/%s/releases?per_page=5" % mod.id)
        det = ModDetails(mod)
        det.extra = [("Stars", "{:,}".format(mod.endorsements)), ("Last activity", mod.updated)]
        body = ""
        for rel in (releases if isinstance(releases, list) else []):
            if not isinstance(rel, dict) or rel.get("draft"):
                continue
            tag = str(rel.get("tag_name") or "")
            for a in (rel.get("assets") or []):
                name = str((a or {}).get("name") or "")
                url = str((a or {}).get("browser_download_url") or "")
                if name.lower().endswith(ARCHIVE_EXTS) and url.lower().startswith("https://"):
                    det.files.append(ModFile(str(a.get("id") or name), name, name, tag,
                                             "Pre-release" if rel.get("prerelease") else "Release",
                                             _int(a.get("size")), "", _date(rel.get("published_at")), url))
            if not body:
                body = str(rel.get("body") or "")
                mod.version = mod.version or tag
        det.description_html = "<p>%s</p><pre style='white-space: pre-wrap'>%s</pre>" % (
            html.escape(mod.summary), html.escape(body or "This project has not published any releases."))
        det.requirements_note = "GitHub does not list requirements. Check the project's page."
        return det

    def download_url(self, mod: ModSummary, file: ModFile) -> str:
        if not file.url.lower().startswith("https://"):
            raise ProviderError("This release has no downloadable archive.")
        return file.url


def dormant_nexus() -> "NexusProvider":
    """A Nexus provider that never has a key: every request for it explains that the support is switched off."""
    import common
    nexus = NexusProvider("")
    nexus.key_help = common.NEXUS_UNAVAILABLE_TEXT if not common.NEXUS_AVAILABLE else common.NEXUS_OFF_TEXT
    nexus.no_key_text = "Nexus Mods is not in use."
    return nexus


def all_providers(nexus_key: str = "", nexus_on: bool = True) -> list:
    import common
    if not common.NEXUS_AVAILABLE:
        return [GitHubProvider()]                 # Nexus is not offered at all in this version
    nexus = NexusProvider(nexus_key if nexus_on else "")
    if not nexus_on:
        from common import NEXUS_OFF_TEXT
        nexus.key_help = NEXUS_OFF_TEXT          # what the Browse tab and messages say instead of asking for a key
        nexus.no_key_text = "Nexus Mods is not in use."
    return [nexus, GitHubProvider()]


def safe_file_name(name: str, fallback: str = "download.zip") -> str:
    name = os.path.basename(name.replace("\\", "/")).strip()
    name = re.sub(r"[^A-Za-z0-9 ._()\[\]+-]", "_", name)[:150]
    return name if name.strip(". ") else fallback
