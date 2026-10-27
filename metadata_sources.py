"""Additional movie metadata providers for MovieSort Pro (Python stdlib only).

These are opt-in. API keys are supplied in memory, never written to a config file.
Only reasonably confident title/year matches become automatic file moves.
"""
from __future__ import annotations

import csv
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path

PROVIDERS = ("Local NFO", "Local Catalogue", "TMDB", "OMDb", "Wikidata", "Apple Movies", "Trakt")
FIELDS = ("title", "year", "genres", "studios", "actors", "directors", "countries", "collection", "collection_id", "collection_order", "plot", "imdb_id", "tmdb_id", "poster")
LIST_FIELDS = {"genres", "studios", "actors", "directors", "countries"}


def normal(text):
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", str(text or "").lower()).split())


def parse_year(value):
    match = re.search(r"\b(18\d\d|19\d\d|20\d\d|2100)\b", str(value or ""))
    return int(match.group(1)) if match else None


def split_values(value):
    if not value or value == "N/A":
        return []
    if isinstance(value, list):
        return [part for element in value for part in split_values(element)]
    return [x.strip() for x in re.split(r"\s*(?:\|| / |,|;)\s*", str(value)) if x.strip()]


def canonical(raw: dict) -> dict:
    result = {}
    for field in FIELDS:
        value = raw.get(field)
        if not value or value == "N/A":
            continue
        if field in LIST_FIELDS:
            values = split_values(value)
            if values:
                result[field] = values
        elif field == "year":
            y = parse_year(value)
            if y:
                result[field] = y
        else:
            result[field] = value
    return result


def compatible(meta: dict, title: str, year: int | None, strict=True) -> bool:
    if not meta.get("title"):
        return False
    ratio = SequenceMatcher(None, normal(meta["title"]), normal(title)).ratio()
    if ratio < (0.89 if strict else 0.82):
        return False
    myear = parse_year(meta.get("year"))
    if year and myear and abs(year - myear) > 1:
        return False
    return True


def choose_confident_candidate(candidates, title, year=None, threshold=0.91):
    """Return one high-confidence title match; reject ambiguous results and year conflicts."""
    ranked = []
    for meta in candidates:
        if not isinstance(meta, dict) or not meta.get("title"):
            continue
        score = SequenceMatcher(None, normal(title), normal(meta["title"])).ratio()
        candidate_year = parse_year(meta.get("year"))
        if year and candidate_year and abs(year - candidate_year) > 1:
            continue
        if score < threshold:
            continue
        if year and candidate_year == year:
            score += 0.035
        elif year and candidate_year is None:
            score -= 0.015
        ranked.append((score, meta))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    if not ranked:
        return {}
    # A different film with the same name is not automatically an exact match.
    if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.045:
        return {}
    return ranked[0][1]


def can_merge(base: dict, extra: dict) -> bool:
    if base.get("imdb_id") and extra.get("imdb_id"):
        return base["imdb_id"] == extra["imdb_id"]
    if base.get("tmdb_id") and extra.get("tmdb_id"):
        return str(base["tmdb_id"]) == str(extra["tmdb_id"])
    return compatible(extra, base.get("title", ""), parse_year(base.get("year")), strict=True)


def merge_missing(base: dict, extra: dict) -> dict:
    result = dict(base)
    for field, value in extra.items():
        if field not in result or result[field] in (None, "", []):
            result[field] = value
    return result


def json_get(url: str, headers=None, retries=1):
    headers = {"User-Agent": "MovieSortPro/2.0 (Windows desktop library organizer)", "Accept": "application/json", **(headers or {})}
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 502, 503) and attempt < retries:
                time.sleep(2)
                continue
            raise RuntimeError(f"HTTP {exc.code} from metadata provider") from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            if attempt < retries:
                time.sleep(1)
                continue
            raise RuntimeError("Metadata provider could not be reached") from exc


def get_local_nfo(video: Path) -> dict:
    candidates = [video.with_suffix(".nfo")]
    # movie.nfo is only unambiguous in a folder holding one movie file.
    if sum(1 for f in video.parent.iterdir() if f.is_file() and f.suffix.lower() in
           {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".iso", ".m2ts", ".ts", ".webm", ".mpg", ".mpeg"}) == 1:
        candidates.append(video.parent / "movie.nfo")
    for file in candidates:
        if not file.is_file():
            continue
        try:
            if file.stat().st_size > 2 * 1024 * 1024:
                continue
            root = ET.fromstring(file.read_bytes())
            if root.tag.lower() != "movie":
                continue
            def txt(tag):
                return (root.findtext(tag) or "").strip()
            def many(tag):
                return [node.text.strip() for node in root.findall(tag) if node.text and node.text.strip()]
            actors = [(node.findtext("name") or "").strip() for node in root.findall("actor")]
            md = canonical({
                "title": txt("title"), "year": txt("year") or txt("premiered"),
                "genres": many("genre"), "studios": many("studio"),
                "actors": [x for x in actors if x], "directors": many("director"),
                "countries": many("country"), "plot": txt("plot"),
                "collection": txt("set/name"), "collection_id": txt("set/tmdbid"),
                "collection_order": txt("collection_order"),
                "imdb_id": next(((e.text or "").strip() for e in root.findall("uniqueid") if e.get("type") == "imdb"), "") or txt("imdbid"),
                "tmdb_id": next(((e.text or "").strip() for e in root.findall("uniqueid") if e.get("type") == "tmdb"), ""),
            })
            if md.get("title"):
                return md
        except (OSError, ET.ParseError, UnicodeError):
            continue
    return {}


class LocalCatalogue:
    """CSV/JSON movie catalogue (columns: title/year/filename/path/genre/studio/actor etc)."""
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.rows = []
        if not self.path.is_file():
            return
        if self.path.suffix.lower() == ".json":
            obj = json.loads(self.path.read_text(encoding="utf-8-sig"))
            raw = obj.get("movies", []) if isinstance(obj, dict) else obj
        elif self.path.suffix.lower() == ".csv":
            with self.path.open(encoding="utf-8-sig", newline="") as f:
                raw = list(csv.DictReader(f))
        else:
            raise ValueError("Catalogue must be a CSV or JSON file")
        if not isinstance(raw, list):
            raise ValueError("Catalogue contains no list of movies")
        for item in raw:
            if not isinstance(item, dict):
                continue
            low = {str(k).strip().lower(): v for k, v in item.items()}
            md = canonical({
                "title": low.get("title") or low.get("name"),
                "year": low.get("year") or low.get("release_year"),
                "genres": low.get("genres") or low.get("genre"),
                "studios": low.get("studios") or low.get("studio") or low.get("production"),
                "actors": low.get("actors") or low.get("actor") or low.get("cast"),
                "directors": low.get("directors") or low.get("director"),
                "countries": low.get("countries") or low.get("country"),
                "collection": low.get("collection"), "collection_id": low.get("collection_id"),
                "collection_order": low.get("collection_order") or low.get("series_order"), "plot": low.get("plot"),
                "imdb_id": low.get("imdb_id") or low.get("imdbid"),
                "tmdb_id": low.get("tmdb_id"),
            })
            if md.get("title"):
                self.rows.append((str(low.get("filename") or low.get("path") or "").strip(), md))

    def lookup(self, video: Path, title: str, year: int | None) -> dict:
        matches = []
        for filename, md in self.rows:
            if filename and (Path(filename).name.lower() == video.name.lower() or str(video).lower() == filename.lower()):
                if year and md.get("year") and abs(year - md["year"]) > 1:
                    continue
                return md
            if compatible(md, title, year):
                matches.append(md)
        return matches[0] if len(matches) == 1 else {}


class OMDbProvider:
    def __init__(self, key: str):
        self.key = key.strip()

    def request(self, args):
        return json_get("https://www.omdbapi.com/?" + urllib.parse.urlencode({"apikey": self.key, "type": "movie", **args}))

    @staticmethod
    def parse(data):
        return canonical({
            "title": data.get("Title"), "year": data.get("Year"),
            "genres": data.get("Genre"), "studios": data.get("Production"),
            "actors": data.get("Actors"), "directors": data.get("Director"),
            "countries": data.get("Country"), "plot": data.get("Plot"),
            "imdb_id": data.get("imdbID"), "poster": data.get("Poster"),
        })

    def lookup(self, title, year=None, imdb_id=None):
        if not self.key:
            return {}
        if imdb_id and re.fullmatch(r"tt\d{7,10}", str(imdb_id)):
            response = self.request({"i": imdb_id, "plot": "short"})
            if response.get("Response") != "False":
                md = self.parse(response)
                if compatible(md, title, year, strict=False):
                    return md
        # Direct lookup is cheap and contains richer metadata.
        queries = [{"t": title, "y": year, "plot": "short"}] if year else []
        queries.append({"t": title, "plot": "short"})
        for query in queries:
            response = self.request(query)
            if response.get("Response") != "False":
                md = self.parse(response)
                if compatible(md, title, year):
                    return md
        # 's' searches by approximate title; validate candidate IDs before trusting.
        response = self.request({"s": title, "page": 1})
        candidates = [self.parse(item) for item in response.get("Search", [])] if response.get("Response") != "False" else []
        selected = choose_confident_candidate(candidates, title, year, threshold=0.88)
        if not selected or not selected.get("imdb_id"):
            return {}
        details = self.request({"i": selected["imdb_id"], "plot": "short"})
        md = self.parse(details) if details.get("Response") != "False" else {}
        return md if compatible(md, title, year, strict=False) else {}


class AppleMoviesProvider:
    """No-key Apple Search API movie catalogue, good as a last-resort title/year check.

    Only use documented movie fields; Apple does not reliably supply cast/studios.
    """
    def lookup(self, title, year=None):
        params = {"term": title, "media": "movie", "entity": "movie", "limit": 25, "country": "gb"}
        response = json_get("https://itunes.apple.com/search?" + urllib.parse.urlencode(params))
        candidates = []
        for item in response.get("results", []):
            if item.get("kind") not in ("feature-movie", "movie") and item.get("wrapperType") != "track":
                continue
            movie_title = item.get("trackName") or item.get("collectionName")
            if not movie_title:
                continue
            candidates.append(canonical({"title": movie_title, "year": item.get("releaseDate"),
                                         "genres": [item.get("primaryGenreName")] if item.get("primaryGenreName") else [],
                                         "plot": item.get("longDescription") or item.get("description")}))
        return choose_confident_candidate(candidates, title, year, threshold=0.94)


class TraktProvider:
    """Optional Trakt public movie search using a user-supplied developer client ID."""
    def __init__(self, client_id):
        self.client_id = client_id.strip()

    def lookup(self, title, year=None, imdb_id=None):
        if not self.client_id:
            return {}
        params = {"query": title, "extended": "full", "limit": 25}
        data = json_get("https://api.trakt.tv/search/movie?" + urllib.parse.urlencode(params),
                        headers={"trakt-api-key": self.client_id, "trakt-api-version": "2"})
        candidates = []
        for item in data if isinstance(data, list) else []:
            movie = item.get("movie", {})
            ids = movie.get("ids", {})
            if not movie.get("title"):
                continue
            candidates.append(canonical({"title": movie.get("title"), "year": movie.get("year"),
                                         "genres": movie.get("genres") or [], "plot": movie.get("overview"),
                                         "imdb_id": ids.get("imdb"), "tmdb_id": ids.get("tmdb")}))
        selected = choose_confident_candidate(candidates, title, year, threshold=0.90)
        if imdb_id and selected.get("imdb_id") and imdb_id != selected["imdb_id"]:
            return {}
        return selected


class WikidataProvider:
    """Movie lookups via the Wikidata MediaWiki API, with exact-ish title and year checks."""
    ROOT = "https://www.wikidata.org/w/api.php?"

    def request(self, params):
        return json_get(self.ROOT + urllib.parse.urlencode({"format": "json", **params}))

    @staticmethod
    def ids(entity, property_name):
        found = []
        for statement in entity.get("claims", {}).get(property_name, []):
            if statement.get("rank") == "deprecated":
                continue
            data = statement.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(data, dict) and data.get("id"):
                found.append(data["id"])
        return found

    @staticmethod
    def dates(entity):
        years = []
        for statement in entity.get("claims", {}).get("P577", []):
            value = statement.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(value, dict):
                y = parse_year(value.get("time", ""))
                if y:
                    years.append(y)
        return years

    def lookup(self, title, year=None, imdb_id=None):
        # Search is restricted to a handful of entities to limit provider traffic.
        found = self.request({"action": "wbsearchentities", "search": title, "language": "en", "type": "item", "limit": 8}).get("search", [])
        candidates = [x for x in found if SequenceMatcher(None, normal(title), normal(x.get("label", ""))).ratio() >= 0.93]
        if not candidates:
            return {}
        entities = self.request({"action": "wbgetentities", "ids": "|".join(x["id"] for x in candidates), "props": "claims|labels", "languages": "en"}).get("entities", {})
        good = []
        for c in candidates:
            entity = entities.get(c["id"], {})
            if "Q11424" not in self.ids(entity, "P31"):
                continue
            dates = self.dates(entity)
            if year and (not dates or min(abs(year - y) for y in dates) > 1):
                continue
            good.append((entity, dates))
        if len(good) != 1:
            return {}
        entity, years = good[0]
        names = {"genres": self.ids(entity, "P136")[:10], "studios": self.ids(entity, "P272")[:10],
                 "actors": self.ids(entity, "P161")[:10], "directors": self.ids(entity, "P57")[:6],
                 "countries": self.ids(entity, "P495")[:6]}
        ids = list(dict.fromkeys(x for values in names.values() for x in values))
        labels = {}
        if ids:
            label_data = self.request({"action": "wbgetentities", "ids": "|".join(ids), "props": "labels", "languages": "en"}).get("entities", {})
            labels = {key: val.get("labels", {}).get("en", {}).get("value", "") for key, val in label_data.items()}
        def claim_string(field):
            for stmt in entity.get("claims", {}).get(field, []):
                val = stmt.get("mainsnak", {}).get("datavalue", {}).get("value")
                if isinstance(val, str):
                    return val
            return ""
        result = canonical({"title": entity.get("labels", {}).get("en", {}).get("value", title),
                            "year": min(years, key=lambda y: abs(y - year)) if year and years else min(years) if years else None,
                            **{k: [labels.get(x) for x in v if labels.get(x)] for k, v in names.items()},
                            "imdb_id": claim_string("P345"), "tmdb_id": claim_string("P4947")})
        return result if compatible(result, title, year) else {}


class MetadataResolver:
    """Priority based fallback, optionally filling missing fields across providers."""
    def __init__(self, priority, enabled, tmdb_token="", omdb_key="", catalogue_path="", merge=False, tmdb_factory=None, trakt_key=""):
        self.priority = [name for name in priority if name in PROVIDERS and enabled.get(name, False)]
        self.merge = bool(merge)
        self.errors = []
        self.tmdb = tmdb_factory(tmdb_token) if tmdb_token and tmdb_factory and "TMDB" in self.priority else None
        self.omdb = OMDbProvider(omdb_key) if omdb_key and "OMDb" in self.priority else None
        self.wikidata = WikidataProvider() if "Wikidata" in self.priority else None
        self.apple = AppleMoviesProvider() if "Apple Movies" in self.priority else None
        self.trakt = TraktProvider(trakt_key) if trakt_key and "Trakt" in self.priority else None
        self.catalogue = LocalCatalogue(catalogue_path) if catalogue_path and "Local Catalogue" in self.priority else None

    def lookup(self, video, title, year):
        current = {}
        source_names = []
        self.errors = []
        for provider in self.priority:
            if provider == "TMDB" and not self.tmdb:
                continue
            if provider == "OMDb" and not self.omdb:
                continue
            if provider == "Local Catalogue" and not self.catalogue:
                continue
            if provider == "Trakt" and not self.trakt:
                continue
            try:
                if provider == "Local NFO":
                    value = get_local_nfo(video)
                    if value and not compatible(value, title, year, strict=False):
                        # An NFO can have a custom title but should not silently match a completely different movie.
                        value = {}
                elif provider == "TMDB":
                    value, state = self.tmdb.lookup(title, year)
                    if state != "Matched":
                        value = {}
                elif provider == "OMDb":
                    value = self.omdb.lookup(title, year, current.get("imdb_id"))
                elif provider == "Wikidata":
                    value = self.wikidata.lookup(title, year, current.get("imdb_id"))
                elif provider == "Apple Movies":
                    value = self.apple.lookup(title, year)
                elif provider == "Trakt":
                    value = self.trakt.lookup(title, year, current.get("imdb_id"))
                else:
                    value = self.catalogue.lookup(video, title, year)
                if not value:
                    continue
                if current and not can_merge(current, value):
                    continue
                if not current:
                    current = dict(value)
                else:
                    current = merge_missing(current, value)
                source_names.append(provider)
                if not self.merge:
                    break
            except (OSError, ValueError, RuntimeError, urllib.error.URLError, json.JSONDecodeError) as exc:
                self.errors.append(f"{provider}: {exc}")
        if current:
            current["sources"] = source_names
            return current, "Matched: " + " + ".join(source_names)
        if self.errors:
            return {}, "Lookup failed / needs review"
        return {}, "Needs review: no confident match"

    def close(self):
        if self.tmdb:
            self.tmdb.close()
