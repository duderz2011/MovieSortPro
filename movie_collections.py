"""Pure collection grouping / inventory logic for MovieSort Pro V3.

A collection is a set of different *locally scanned* films, not duplicate encodes
of one title. A missing title is only asserted when an authoritative list is
available and the title cannot be matched against this scan's inventory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import re
import unicodedata
from typing import Callable, Iterable


def key_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def owned_key(movie) -> str:
    md = movie.metadata
    if md.get("tmdb_id"):
        return "tmdb:" + str(md["tmdb_id"])
    if md.get("imdb_id"):
        return "imdb:" + str(md["imdb_id"])
    return f"title:{key_name(md.get('title') or movie.title)}:{md.get('year') or movie.year or '?'}"


def release_year(part) -> int | None:
    release = str(part.get("release_date") or "")
    try:
        return int(release[:4])
    except ValueError:
        return None


def release_key(part):
    # TMDB collection results don't expose narrative/official sequel order.
    # Respect a deliberate override; otherwise use release date then title.
    order = part.get("collection_order")
    try:
        if order is not None and str(order).strip():
            return (0, int(order), "", "")
    except (TypeError, ValueError):
        pass
    return (1, 0, str(part.get("release_date") or "9999-99-99"), key_name(part.get("title") or ""))


def is_owned(part: dict, library_movies: list) -> bool:
    tmdb_id = str(part.get("id") or "")
    part_name = key_name(part.get("title") or "")
    year = release_year(part)
    for movie in library_movies:
        md = movie.metadata
        if tmdb_id and md.get("tmdb_id") and str(md["tmdb_id"]) == tmdb_id:
            return True
        if part_name and key_name(md.get("title") or movie.title) == part_name:
            myear = md.get("year") or movie.year
            # Only identify same-title records when both years agree closely.
            # If IDs conflict they must not be grouped as the same movie.
            if md.get("tmdb_id") and tmdb_id and str(md["tmdb_id"]) != tmdb_id:
                continue
            if year and myear and abs(int(myear) - year) <= 1:
                return True
    return False


@dataclass
class Collection:
    name: str
    collection_id: str | None
    movies: list = field(default_factory=list)
    owned: list = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    upcoming: list[dict] = field(default_factory=list)
    catalogue_status: str = "Not checked (TMDB API key required)"
    ordered_parts: list[dict] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.owned)


def collect(movies: Iterable, min_owned: int = 2, fetch_parts: Callable | None = None,
            today: date | None = None) -> list[Collection]:
    today = today or date.today()
    all_movies = list(movies)
    named: dict[str, list] = {}
    for movie in all_movies:
        md = movie.metadata
        name = str(md.get("collection") or "").strip()
        if name:
            named.setdefault(key_name(name), []).append(movie)
    results = []
    for _, members in named.items():
        # One ID per name is needed for dependable franchise inventory.
        ids = {str(m.metadata.get("collection_id")) for m in members if m.metadata.get("collection_id")}
        # Avoid mixing unrelated series sharing a similar title, if IDs differ.
        buckets = []
        if len(ids) > 1:
            for col_id in sorted(ids):
                buckets.append((col_id, [m for m in members if str(m.metadata.get("collection_id") or "") == col_id]))
            leftovers = [m for m in members if not m.metadata.get("collection_id")]
            if leftovers:
                buckets.append((None, leftovers))
        else:
            buckets.append((next(iter(ids)) if ids else None, members))
        for collection_id, subset in buckets:
            unique = {}
            for movie in subset:
                md = movie.metadata
                title = key_name(md.get("title") or movie.title)
                year = md.get("year") or movie.year
                signature = f"title:{title}:{year}" if title and year else owned_key(movie)
                unique.setdefault(signature, movie)
            if len(unique) < min_owned:
                continue
            first = subset[0]
            item = Collection(first.metadata["collection"], collection_id, subset, list(unique.values()))
            if collection_id and fetch_parts:
                try:
                    parts = fetch_parts(collection_id)
                    if not isinstance(parts, list):
                        raise ValueError("Invalid collection response")
                    parts = [p for p in parts if isinstance(p, dict) and p.get("title") and p.get("id")]
                    item.ordered_parts = sorted(parts, key=release_key)
                    if not parts:
                        item.catalogue_status = "Collection catalogue unavailable"
                    else:
                        item.catalogue_status = "Checked against TMDB"
                        for part in item.ordered_parts:
                            if is_owned(part, all_movies):
                                continue
                            release = str(part.get("release_date") or "")
                            try:
                                released = date.fromisoformat(release) <= today
                            except ValueError:
                                released = False
                            if released:
                                item.missing.append(part)
                            else:
                                item.upcoming.append(part)
                except (OSError, RuntimeError, ValueError) as exc:
                    item.catalogue_status = "Catalogue lookup unavailable: " + str(exc)
            elif collection_id and not fetch_parts:
                item.catalogue_status = "Not checked (enable TMDB and enter an API key)"
            elif not collection_id:
                item.catalogue_status = "Grouping from local metadata; TMDB collection ID unavailable"
            results.append(item)
    return sorted(results, key=lambda c: key_name(c.name))


def movie_order(movie, collection: Collection) -> tuple:
    """Explicit local sequence wins; else order by TMDB collection release order."""
    seq = movie.metadata.get("collection_order")
    try:
        if seq is not None and str(seq).strip():
            return (0, int(seq), 0, "")
    except (ValueError, TypeError):
        pass
    for index, part in enumerate(collection.ordered_parts):
        if is_owned(part, [movie]):
            return (1, index, 0, "")
    return (2, movie.metadata.get("year") or movie.year or 9999, 0,
            key_name(movie.metadata.get("title") or movie.title))
