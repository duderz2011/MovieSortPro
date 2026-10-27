"""MovieSort Pro: Windows desktop movie catalogue/organiser (Python 3.10+)."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import queue
import re
import shutil
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path
from tkinter import Tk, Toplevel, StringVar, BooleanVar, Listbox, END, filedialog, messagebox, simpledialog
from tkinter import ttk
from typing import Optional
from metadata_sources import PROVIDERS, MetadataResolver
from movie_collections import collect, movie_order, key_name
from preferences import read_preferences, write_preferences, seal, unseal
from library_maintenance import no_video_folders, prune_empty_tree, safe_to_recycle, recycle, lower_quality_duplicates

APP_NAME = "MovieSort Pro"
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".wmv", ".mov", ".mpg", ".mpeg", ".webm", ".ts", ".m2ts", ".iso"}
SIDECAR_EXTENSIONS = {".srt", ".ass", ".ssa", ".sub", ".idx", ".nfo", ".jpg", ".jpeg", ".png", ".webp"}
NOISE = re.compile(r"(?i)\b(?:2160p|1080p|720p|480p|4k|uhd|hdr10\+?|dv|dolby[ ._-]?vision|bluray|blu-ray|brrip|bdrip|webrip|web[-. ]?dl|hdtv|x264|x265|h264|h265|hevc|aac|dts|ddp?5[ ._-]?1|atmos|remux|proper|repack|extended|unrated|yify|rarbg)\b")
YEARS = re.compile(r"(?<!\d)(19\d{2}|20\d{2})(?!\d)")
WINDOWS_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
PRESETS = ["Genre", "Studio", "Lead Actor", "A-P / Q-Z", "A-Z Letters", "Year", "Decade", "Genre / Year", "Year / Genre", "Studio / A-Z Letters", "Lead Actor / Year"]
CUSTOM_LEVELS = ["None", "Genre", "Studio", "Lead Actor", "Selected Actor", "Director", "Country", "Collection", "A-P / Q-Z", "A-Z Letters", "Year", "Decade", "Movie Title"]


def app_dir() -> Path:
    path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "MovieSortPro"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_name(value: str) -> str:
    s = WINDOWS_INVALID.sub("_", (value or "Unknown")).rstrip(" .").strip()
    if s.split(".")[0].upper() in WINDOWS_RESERVED:
        s = "_" + s
    return s[:110] or "Unknown"


def normalize(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r"[^a-z0-9 ]+", " ", s.lower())
    return " ".join(s.split())


def filename_title_year(path: Path) -> tuple[str, Optional[int]]:
    name = re.sub(r"[._]+", " ", path.stem)
    name = re.sub(r"\[[^]]*\]|\{[^}]*\}", " ", name)
    candidates = list(YEARS.finditer(name))
    found = candidates[-1] if candidates else None
    # A film title can itself be a year (e.g. 1917, 1984 or 2001).
    if len(candidates) == 1 and candidates[0].start() == 0:
        found = None
    year = int(found.group(1)) if found else None
    if found:
        name = name[:found.start()]
    name = NOISE.split(name)[0]
    name = re.sub(r"\s+", " ", name).strip(" -_() ")
    if len(name) < 2:
        name = path.stem
    return name, year


def first_letter(title: str) -> str:
    title = re.sub(r"(?i)^(the|an|a)\s+", "", title).strip()
    for c in normalize(title):
        if c.isalnum():
            return c.upper() if c.isalpha() else "#"
    return "#"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def cleanup_empty_source_dirs(moved_origins, source_roots, destination=None) -> int:
    """Remove only source subdirectories made empty by successful file moves.

    NEVER delete a user-selected source root, the destination root, symlinks,
    or folders with any files/other directories. Existing files are not touched.
    """
    roots = {Path(p).resolve() for p in source_roots}
    dest = Path(destination).resolve() if destination else None
    if not roots:
        return 0
    candidates = set()
    for original in moved_origins:
        directory = Path(original).parent
        for root in roots:
            # Use the path's symlink-free ancestry, and never walk above a root.
            if directory == root or not directory.is_relative_to(root):
                continue
            while directory != root:
                if directory.is_symlink():
                    break
                if dest is None or directory != dest:
                    candidates.add(directory)
                directory = directory.parent
    count = 0
    for folder in sorted(candidates, key=lambda x: len(x.parts), reverse=True):
        if folder in roots or (dest is not None and folder == dest) or folder.is_symlink():
            continue
        try:
            folder.rmdir()  # Atomic: fails if even a hidden file or subfolder remains.
            count += 1
        except OSError:
            pass
    return count


@dataclass
class Movie:
    path: Path
    title: str
    year: Optional[int]
    metadata: dict = field(default_factory=dict)
    match_state: str = "Filename only"
    target: Optional[Path] = None
    notes: str = ""


class TMDb:
    def __init__(self, token: str):
        self.token = token.strip()
        self.conn = sqlite3.connect(app_dir() / "metadata_cache.db", timeout=30)
        self.conn.execute("CREATE TABLE IF NOT EXISTS movies (lookup_key TEXT PRIMARY KEY, value TEXT, stored_at REAL)")
        self.conn.commit()

    def get(self, endpoint: str, params: Optional[dict] = None) -> dict:
        url = "https://api.themoviedb.org/3/" + endpoint
        if params:
            url += "?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers={"Authorization": "Bearer " + self.token, "Accept": "application/json", "User-Agent": "MovieSortPro/1.0"})
        for retry in range(3):
            try:
                with urllib.request.urlopen(request, timeout=14) as response:
                    return json.load(response)
            except urllib.error.HTTPError as e:
                if e.code == 429 and retry < 2:
                    time.sleep(min(float(e.headers.get("Retry-After") or 3), 15))
                    continue
                raise RuntimeError(f"TMDB returned HTTP {e.code}") from e
            except (urllib.error.URLError, TimeoutError) as e:
                if retry < 2:
                    time.sleep(2)
                    continue
                raise RuntimeError("Cannot connect to TMDB") from e
        raise RuntimeError("TMDB lookup failed")

    def lookup(self, title: str, year: Optional[int]) -> tuple[dict, str]:
        key = f"v3.2|{normalize(title)}|{year or ''}"
        cached = self.conn.execute("SELECT value FROM movies WHERE lookup_key=?", (key,)).fetchone()
        if cached:
            saved = json.loads(cached[0])
            prior = saved.get("metadata") or {}
            # V2.1 caches store collection names without TMDB collection IDs.
            # Refresh that entry on demand so V3 can detect absent sequels.
            if not (prior.get("collection") and not prior.get("collection_id") and prior.get("tmdb_id")):
                return prior, saved["state"]
        # Try a year-filtered search first. If it fails, broaden without the year,
        # because original/theatrical/UK release years can differ.
        queries = [{"query": title, "include_adult": "false", "language": "en-GB"}]
        if year:
            queries.insert(0, {**queries[0], "year": year})
        found = {}
        scored = []
        for params in queries:
            results = self.get("search/movie", params).get("results", [])
            for result in results:
                movie_id = result.get("id")
                if not movie_id:
                    continue
                options = [result.get("title") or "", result.get("original_title") or ""]
                title_score = max((SequenceMatcher(None, normalize(title), normalize(t)).ratio()
                                   for t in options if t), default=0)
                release_year = int((result.get("release_date") or "0000")[:4] or 0)
                # Never automatically substitute a similarly named remake from a different year.
                if year and release_year and abs(release_year - year) > 1:
                    continue
                score = title_score + (0.04 if year and release_year == year else 0)
                if year and not release_year:
                    score -= 0.03
                if score > found.get(movie_id, (-1, {}))[0]:
                    found[movie_id] = (score, result)
            scored = sorted(found.values(), key=lambda pair: pair[0], reverse=True)
            threshold = 0.88 if year else 0.92
            if scored and scored[0][0] >= threshold and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.045):
                break
        if not scored or scored[0][0] < (0.88 if year else 0.92):
            return {}, "Needs review: no confident match"
        if len(scored) > 1 and scored[0][0] - scored[1][0] < 0.045:
            return {}, "Needs review: ambiguous match"
        result = scored[0][1]
        details = self.get(f"movie/{result['id']}", {"append_to_response": "credits", "language": "en-GB"})
        companies = details.get("production_companies") or []
        cast = (details.get("credits") or {}).get("cast") or []
        crew = (details.get("credits") or {}).get("crew") or []
        meta = {
            "title": details.get("title") or title,
            "year": int((details.get("release_date") or "0000")[:4] or 0) or year,
            "genres": [x["name"] for x in details.get("genres") or []],
            "studios": [x["name"] for x in companies],
            "actors": [x["name"] for x in cast[:100]],
            "directors": list(dict.fromkeys(x["name"] for x in crew if x.get("job") == "Director")),
            "countries": [x.get("name") for x in (details.get("production_countries") or []) if x.get("name")],
            "collection": (details.get("belongs_to_collection") or {}).get("name"),
            "collection_id": (details.get("belongs_to_collection") or {}).get("id"),
            "plot": details.get("overview"),
            "imdb_id": details.get("imdb_id"),
            "tmdb_id": details["id"],
        }
        self.conn.execute("INSERT OR REPLACE INTO movies VALUES (?, ?, ?)", (key, json.dumps({"metadata": meta, "state": "Matched"}), time.time()))
        self.conn.commit()
        time.sleep(0.20)
        return meta, "Matched"

    def collection_parts(self, collection_id: str) -> list[dict]:
        """Load full TMDB collection inventory with a 30-day local cache."""
        if not str(collection_id).isdigit():
            raise ValueError("Invalid TMDB collection ID")
        key = f"collection-v3:{collection_id}"
        self.conn.execute("CREATE TABLE IF NOT EXISTS collections (lookup_key TEXT PRIMARY KEY, value TEXT, stored_at REAL)")
        found = self.conn.execute("SELECT value, stored_at FROM collections WHERE lookup_key=?", (key,)).fetchone()
        if found and time.time() - found[1] < 30 * 86400:
            return json.loads(found[0])
        try:
            details = self.get(f"collection/{collection_id}", {"language": "en-GB"})
            parts = details.get("parts")
            if not isinstance(parts, list):
                raise ValueError("TMDB did not return a collection catalogue")
            self.conn.execute("INSERT OR REPLACE INTO collections VALUES (?, ?, ?)",
                              (key, json.dumps(parts), time.time()))
            self.conn.commit()
            return parts
        except (ValueError, RuntimeError):
            if found:
                return json.loads(found[0])
            raise

    def close(self):
        self.conn.close()


def target_parts(movie: Movie, preset: str) -> list[str]:
    md = movie.metadata
    title = md.get("title") or movie.title
    year = md.get("year") or movie.year
    genre = (md.get("genres") or ["Unknown Genre"])[0]
    studio = (md.get("studios") or ["Unknown Studio"])[0]
    actor = (md.get("actors") or ["Unknown Actor"])[0]
    letter = first_letter(title)
    values = {
        "Genre": [genre], "Studio": [studio], "Lead Actor": [actor],
        "A-P / Q-Z": ["A-P" if "A" <= letter <= "P" else "Q-Z" if "Q" <= letter <= "Z" else "0-9"],
        "A-Z Letters": [letter], "Year": [str(year or "Unknown Year")],
        "Decade": [str((int(year)//10)*10) + "s" if year else "Unknown Decade"],
        "Genre / Year": [genre, str(year or "Unknown Year")],
        "Year / Genre": [str(year or "Unknown Year"), genre],
        "Studio / A-Z Letters": [studio, letter],
        "Lead Actor / Year": [actor, str(year or "Unknown Year")],
    }
    return [safe_name(x) for x in values[preset]]


def custom_target_parts(movie: Movie, levels: list[str], selected_actor: str = "") -> list[str]:
    """Return one deterministic destination path; never create multiple movie copies."""
    md = movie.metadata
    title = md.get("title") or movie.title
    year = md.get("year") or movie.year
    letter = first_letter(title)
    chosen_actor = selected_actor.strip()
    actors = md.get("actors") or []
    values = {
        "Genre": (md.get("genres") or ["Unknown Genre"])[0],
        "Studio": (md.get("studios") or ["Unknown Studio"])[0],
        "Lead Actor": actors[0] if actors else "Unknown Actor",
        "Selected Actor": next((a for a in actors if normalize(a) == normalize(chosen_actor)), "Unknown Actor") if chosen_actor else "Unknown Actor",
        "Director": (md.get("directors") or ["Unknown Director"])[0],
        "Country": (md.get("countries") or ["Unknown Country"])[0],
        "Collection": md.get("collection") or "No Collection",
        "A-P / Q-Z": "A-P" if "A" <= letter <= "P" else "Q-Z" if "Q" <= letter <= "Z" else "0-9",
        "A-Z Letters": letter,
        "Year": str(year) if year else "Unknown Year",
        "Decade": f"{(int(year) // 10) * 10}s" if year else "Unknown Decade",
        "Movie Title": f"{title} ({year})" if year else title,
    }
    return [safe_name(values[level]) for level in levels if level != "None"]


def main_target_custom(movie: Movie, destination: Path, levels: list[str], movie_folder: bool, rename: bool, selected_actor: str = "") -> Path:
    parent = destination.joinpath(*custom_target_parts(movie, levels, selected_actor))
    title = safe_name(movie.metadata.get("title") or movie.title)
    year = movie.metadata.get("year") or movie.year
    standard = f"{title} ({year})" if year else title
    if movie_folder and "Movie Title" not in levels:
        parent /= safe_name(standard)
    stem = safe_name(standard) if rename else safe_name(movie.path.stem)
    return parent / (stem + movie.path.suffix)


def main_target(movie: Movie, destination: Path, preset: str, movie_folder: bool, rename: bool) -> Path:
    parent = destination.joinpath(*target_parts(movie, preset))
    title = safe_name(movie.metadata.get("title") or movie.title)
    year = movie.metadata.get("year") or movie.year
    standard = f"{title} ({year})" if year else title
    if movie_folder:
        parent /= safe_name(standard)
    stem = safe_name(standard) if rename else safe_name(movie.path.stem)
    return parent / (stem + movie.path.suffix)


def confidently_identified(movie: Movie) -> bool:
    """Only a confirmed metadata-provider match can enter ordinary sorting.

    A filename-derived title/year is a guess, not a successful match. Likewise,
    a metadata outage is not evidence that the filename names the right movie.
    """
    state = movie.match_state.strip()
    return bool(movie.metadata.get("title")) and (state == "Matched" or state.startswith("Matched:"))


def unmatched_target(movie: Movie, destination: Path) -> Path:
    """Preserve filenames for later manual correction and safe rescanning."""
    return destination / "unmatched" / movie.path.name


def sidecars(movie_file: Path) -> list[Path]:
    try:
        files = [p for p in movie_file.parent.iterdir() if p.is_file()]
        # Do not move folder-level artwork if multiple films share a folder.
        lone_movie = sum(1 for p in files if p.suffix.lower() in VIDEO_EXTENSIONS) == 1
        shared_names = {"movie.nfo", "poster.jpg", "poster.png", "fanart.jpg", "fanart.png", "folder.jpg", "clearlogo.png", "banner.jpg"}
        return [p for p in files if p != movie_file and p.suffix.lower() in SIDECAR_EXTENSIONS and (
            p.stem.lower() == movie_file.stem.lower() or
            p.name.lower().startswith(movie_file.stem.lower() + ".") or
            (lone_movie and p.name.lower() in shared_names)
        )]
    except OSError:
        return []


def companion_target(original: Path, source_video: Path, destination_video: Path, renamed: bool) -> Path:
    if not renamed:
        return destination_video.parent / original.name
    shared_names = {"movie.nfo", "poster.jpg", "poster.png", "fanart.jpg", "fanart.png", "folder.jpg", "clearlogo.png", "banner.jpg"}
    if original.name.lower() in shared_names:
        return destination_video.parent / original.name
    suffix = original.name[len(source_video.stem):] if original.name.lower().startswith(source_video.stem.lower()) else original.suffix
    return destination_video.parent / (destination_video.stem + suffix)


def choose_target(source: Path, desired: Path, reserved: set[str]) -> tuple[Optional[Path], str]:
    # An already-sorted file must not be renamed to "Film [2].mkv" on rerun.
    if source.resolve() == desired.resolve():
        return None, "Already in the correct destination folder: skip"
    candidate = desired
    for n in range(1, 10000):
        key = str(candidate).lower()
        if key not in reserved and not candidate.exists():
            reserved.add(key)
            return candidate, (f"Renamed to avoid name collision ({n})" if n > 1 else "")
        if candidate.exists() and candidate.is_file() and source.stat().st_size == candidate.stat().st_size:
            # Full hash only on possible collisions; no unsafe size-only deduplication.
            if sha256(source) == sha256(candidate):
                return None, "Identical destination file exists: skip"
        candidate = desired.with_name(f"{desired.stem} [{n+1}]{desired.suffix}")
    return None, "Cannot find a free filename"


class App(Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME + " V3.3 — Smarter Metadata & Library Cleanup")
        self.geometry("1220x790")
        self.minsize(950, 650)
        self.configure(bg="#151a24")
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("TFrame", background="#151a24")
        style.configure("TLabel", background="#151a24", foreground="#e8ecf5")
        style.configure("TLabelframe", background="#151a24", foreground="#eef3fb")
        style.configure("TLabelframe.Label", background="#151a24", foreground="#eef3fb", font=("Segoe UI", 10, "bold"))
        style.configure("TCheckbutton", background="#151a24", foreground="#e8ecf5")
        style.configure("TButton", padding=(12, 6))
        style.configure("Treeview", rowheight=26, background="#202838", fieldbackground="#202838", foreground="#f1f3f8")
        style.configure("Treeview.Heading", background="#303b4e", foreground="white")
        style.map("Treeview", background=[("selected", "#35629b")])
        style.configure("TProgressbar", thickness=12)
        self.sources: list[Path] = []
        self.movies: list[Movie] = []
        self.collection_groups = []
        self.collection_membership = {}
        self.events = queue.Queue()
        self.busy = False
        self.destination = StringVar()
        self.token = StringVar()
        self.omdb_key = StringVar()
        self.trakt_key = StringVar()
        self.catalogue_path = StringVar()
        self.source_priority = list(PROVIDERS)
        self.source_enabled = {name: BooleanVar(value=True) for name in PROVIDERS}
        self.merge_metadata = BooleanVar(value=False)
        self.preset = StringVar(value="Custom levels")
        self.levels = [StringVar(value="Genre"), StringVar(value="Year"), StringVar(value="Movie Title")]
        self.selected_actor = StringVar()
        self.mode = StringVar(value="Copy")
        self.rename = BooleanVar(value=True)
        self.movie_folder = BooleanVar(value=False)
        self.include_sidecars = BooleanVar(value=True)
        self.route_unmatched = BooleanVar(value=True)
        self.remove_empty_folders = BooleanVar(value=True)
        self.scan_empty_folders = BooleanVar(value=True)
        self.no_video_candidates = []
        self.duplicate_suggestions = []
        self.auto_collections = BooleanVar(value=True)
        self.collection_minimum = StringVar(value="2")
        self.check_missing = BooleanVar(value=True)
        self.status = StringVar(value="Add one or more source folders and an output folder to begin.")
        self.restore_preferences()
        self._draw()
        for folder in self.sources:
            self.source_list.insert(END, str(folder))
        self.update_source_summary()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._event_timer = self.after(120, self.drain_events)

    def restore_preferences(self):
        """Remember library layout across launches, including reruns after a Move."""
        saved = read_preferences(app_dir() / "preferences.json")
        self.sources = [Path(p) for p in saved.get("sources", []) if isinstance(p, str)]
        for name, var in (("destination", self.destination), ("preset", self.preset),
                          ("selected_actor", self.selected_actor), ("catalogue_path", self.catalogue_path)):
            value = saved.get(name)
            if isinstance(value, str):
                var.set(value)
        if saved.get("mode") in ("Copy", "Move"):
            self.mode.set(saved["mode"])
        for name, var in (("rename", self.rename), ("movie_folder", self.movie_folder),
                          ("include_sidecars", self.include_sidecars), ("route_unmatched", self.route_unmatched),
                          ("remove_empty_folders", self.remove_empty_folders), ("scan_empty_folders", self.scan_empty_folders),
                          ("merge_metadata", self.merge_metadata),
                          ("auto_collections", self.auto_collections), ("check_missing", self.check_missing)):
            if isinstance(saved.get(name), bool):
                var.set(saved[name])
        if str(saved.get("collection_minimum", "2")) in ("2", "3", "4"):
            self.collection_minimum.set(str(saved.get("collection_minimum", "2")))
        if isinstance(saved.get("levels"), list):
            for var, level in zip(self.levels, saved["levels"]):
                if level in CUSTOM_LEVELS:
                    var.set(level)
        priority = saved.get("source_priority", [])
        if isinstance(priority, list):
            # Preserve existing user priorities when new providers are added.
            filtered = [name for name in priority if name in PROVIDERS]
            if len(set(filtered)) == len(filtered):
                self.source_priority = filtered + [p for p in PROVIDERS if p not in filtered]
        enabled = saved.get("source_enabled", {})
        if isinstance(enabled, dict):
            for provider in PROVIDERS:
                if isinstance(enabled.get(provider), bool):
                    self.source_enabled[provider].set(enabled[provider])
        # On Windows, credentials are encrypted with the signed-in user's DPAPI key.
        self.token.set(unseal(saved.get("tmdb_protected", "")))
        self.omdb_key.set(unseal(saved.get("omdb_protected", "")))
        self.trakt_key.set(unseal(saved.get("trakt_protected", "")))

    def save_preferences(self):
        prefs = {
            "sources": [str(p) for p in self.sources],
            "destination": self.destination.get(), "preset": self.preset.get(),
            "levels": [var.get() for var in self.levels],
            "selected_actor": self.selected_actor.get(), "mode": self.mode.get(),
            "rename": self.rename.get(), "movie_folder": self.movie_folder.get(),
            "include_sidecars": self.include_sidecars.get(),
            "route_unmatched": self.route_unmatched.get(),
            "remove_empty_folders": self.remove_empty_folders.get(),
            "scan_empty_folders": self.scan_empty_folders.get(),
            "auto_collections": self.auto_collections.get(),
            "collection_minimum": self.collection_minimum.get(),
            "check_missing": self.check_missing.get(),
            "source_priority": list(self.source_priority),
            "source_enabled": {k: var.get() for k, var in self.source_enabled.items()},
            "merge_metadata": self.merge_metadata.get(),
            "catalogue_path": self.catalogue_path.get(),
            "tmdb_protected": seal(self.token.get()),
            "omdb_protected": seal(self.omdb_key.get()),
            "trakt_protected": seal(self.trakt_key.get()),
        }
        try:
            write_preferences(app_dir() / "preferences.json", prefs)
        except OSError as e:
            self.status.set(f"Could not save preferences: {e}")

    def on_close(self):
        if self.busy and not messagebox.askyesno(APP_NAME, "A task is still running. Closing now may interrupt it. Close anyway?"):
            return
        self.save_preferences()
        self.destroy()

    def show_about(self):
        """Display metadata provider credits without exposing user API credentials."""
        messagebox.showinfo(
            "About MovieSort Pro / Credits",
            "MovieSort Pro V3.2\nFree and open-source movie organiser (MIT).\n\n"
            "This product uses the TMDB API but is not endorsed or certified by TMDB.\n"
            "TMDB: https://www.themoviedb.org\n\n"
            "Other optional sources: OMDb (https://www.omdbapi.com), "
            "Wikidata (https://www.wikidata.org), Apple Movies (https://www.apple.com), "
            "Trakt (https://trakt.tv), local NFO and CSV/JSON.\n\n"
            "No API keys are included with this application."
        )

    def open_last_report(self):
        report = app_dir() / "last_run_report.csv"
        if not report.exists():
            messagebox.showinfo(APP_NAME, "No file transfer report yet. Organise movies first.")
            return
        try:
            if os.name == "nt":
                os.startfile(str(report))
            else:
                messagebox.showinfo(APP_NAME, f"Your report is at:\n{report}")
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Could not open report:\n{e}\n\n{report}")

    def destroy(self):
        # Cleanly cancel pending Tk callbacks (also when test code calls destroy).
        timer = getattr(self, "_event_timer", None)
        if timer is not None:
            try:
                self.after_cancel(timer)
            except Exception:
                pass
            self._event_timer = None
        super().destroy()

    def _draw(self):
        heading = ttk.Frame(self, padding=(20, 18, 20, 8))
        heading.pack(fill="x")
        ttk.Label(heading, text="MOVIESORT PRO", font=("Segoe UI", 23, "bold")).pack(anchor="w")
        ttk.Label(heading, text="Identify films • Preview destination folders • Organise safely", font=("Segoe UI", 10)).pack(anchor="w")
        controls = ttk.Frame(self, padding=(20, 10, 20, 4))
        controls.pack(fill="x")
        left = ttk.LabelFrame(controls, text="  1. Source folders  ", padding=10)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.source_list = Listbox(left, height=4, bg="#202838", fg="white", selectbackground="#35629b", borderwidth=0)
        self.source_list.pack(fill="both", expand=True)
        bar = ttk.Frame(left)
        bar.pack(fill="x", pady=(7, 0))
        ttk.Button(bar, text="+ Add Folder", command=self.add_source).pack(side="left")
        ttk.Button(bar, text="Remove Selected", command=self.remove_source).pack(side="left", padx=6)
        right = ttk.LabelFrame(controls, text="  2. Destination and metadata sources  ", padding=10)
        right.pack(side="left", fill="both", expand=True)
        destbar = ttk.Frame(right)
        destbar.pack(fill="x")
        ttk.Entry(destbar, textvariable=self.destination).pack(side="left", fill="x", expand=True)
        ttk.Button(destbar, text="Browse…", command=self.pick_destination).pack(side="left", padx=(6, 0))
        sourcebar = ttk.Frame(right)
        sourcebar.pack(fill="x", pady=(9, 2))
        ttk.Button(sourcebar, text="Configure Metadata Sources", command=self.metadata_settings).pack(side="left")
        ttk.Button(sourcebar, text="About / Credits", command=self.show_about).pack(side="right")
        self.source_summary = StringVar()
        ttk.Label(right, textvariable=self.source_summary, wraplength=480, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))
        self.update_source_summary()
        settings = ttk.LabelFrame(self, text="  3. Sorting rules  ", padding=10)
        settings.pack(fill="x", padx=20, pady=(8, 4))
        row = ttk.Frame(settings)
        row.pack(fill="x")
        ttk.Label(row, text="Create folders by:").pack(side="left")
        sortbox = ttk.Combobox(row, textvariable=self.preset, values=["Custom levels"] + PRESETS, width=24, state="readonly")
        sortbox.pack(side="left", padx=9)
        sortbox.bind("<<ComboboxSelected>>", lambda _: self.refresh_preview())
        ttk.Label(row, text="Action:").pack(side="left", padx=(16, 5))
        modes = ttk.Combobox(row, textvariable=self.mode, values=["Move", "Copy"], state="readonly", width=9)
        modes.pack(side="left")
        ttk.Checkbutton(row, text="Rename to Title (Year)", variable=self.rename, command=self.refresh_preview).pack(side="left", padx=(17, 0))
        levels = ttk.Frame(settings)
        levels.pack(fill="x", pady=(7, 0))
        ttk.Label(levels, text="Custom path:").pack(side="left")
        for i, level in enumerate(self.levels, 1):
            ttk.Label(levels, text=f"{i}.").pack(side="left", padx=(10, 2))
            cb = ttk.Combobox(levels, textvariable=level, values=CUSTOM_LEVELS, width=18, state="readonly")
            cb.pack(side="left")
            cb.bind("<<ComboboxSelected>>", lambda event: self.custom_changed())
        ttk.Label(levels, text="Actor name:").pack(side="left", padx=(10, 3))
        actor_entry = ttk.Entry(levels, textvariable=self.selected_actor, width=18)
        actor_entry.pack(side="left")
        actor_entry.bind("<FocusOut>", lambda e: self.refresh_preview())
        row2 = ttk.Frame(settings)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(row2, text="Extra movie folder (ignored if Movie Title is a level)", variable=self.movie_folder, command=self.refresh_preview).pack(side="left")
        ttk.Checkbutton(row2, text="Bring matching subtitles and artwork", variable=self.include_sidecars).pack(side="left", padx=(19, 0))
        ttk.Checkbutton(row2, text="Send unidentified films to unmatched", variable=self.route_unmatched,
                        command=self.refresh_preview).pack(side="left", padx=(19, 0))
        ttk.Checkbutton(row2, text="Delete EMPTY source subfolders after Move",
                        variable=self.remove_empty_folders).pack(side="left", padx=(19, 0))
        cleanup_row = ttk.Frame(settings)
        cleanup_row.pack(fill="x", pady=(6, 0))
        ttk.Checkbutton(cleanup_row, text="Automatically remove completely EMPTY source folders during scan",
                        variable=self.scan_empty_folders).pack(side="left")
        ttk.Label(cleanup_row, text="Non-movie files and lower-quality duplicates require explicit Recycle Bin confirmation.",
                  font=("Segoe UI", 9)).pack(side="left", padx=(12, 0))
        collection_row = ttk.Frame(settings)
        collection_row.pack(fill="x", pady=(8, 0))
        ttk.Checkbutton(collection_row, text="Automatically organise movie collections", variable=self.auto_collections,
                        command=self.refresh_preview).pack(side="left")
        ttk.Label(collection_row, text="Minimum owned titles:").pack(side="left", padx=(15, 5))
        minimum = ttk.Combobox(collection_row, textvariable=self.collection_minimum, values=["2", "3", "4"],
                               width=3, state="readonly")
        minimum.pack(side="left")
        minimum.bind("<<ComboboxSelected>>", lambda _: self.status.set("Collection minimum changed. Scan again to apply the new threshold."))
        ttk.Checkbutton(collection_row, text="Check for missing sequels (TMDB)",
                        variable=self.check_missing).pack(side="left", padx=(17, 0))
        action = ttk.Frame(self, padding=(20, 7, 20, 5))
        action.pack(fill="x")
        self.scan_btn = ttk.Button(action, text="1  SCAN & IDENTIFY", command=self.scan)
        self.scan_btn.pack(side="left")
        self.go_btn = ttk.Button(action, text="2  ORGANISE MOVIES", command=self.organise, state="disabled")
        self.go_btn.pack(side="left", padx=8)
        self.collections_btn = ttk.Button(action, text="Collections / Missing Movies", command=self.show_collections,
                                          state="disabled")
        self.collections_btn.pack(side="left", padx=5)
        self.review_folders_btn = ttk.Button(action, text="Review Movie-Free Folders",
                                              command=self.review_movie_free_folders, state="disabled")
        self.review_folders_btn.pack(side="left", padx=4)
        self.review_dupes_btn = ttk.Button(action, text="Review Duplicates",
                                            command=self.review_duplicates, state="disabled")
        self.review_dupes_btn.pack(side="left", padx=4)
        ttk.Button(action, text="Correct Selected Match", command=self.correct_selected).pack(side="left", padx=(6, 8))
        ttk.Button(action, text="Undo Last Move", command=self.undo_last_move).pack(side="right")
        ttk.Button(action, text="Open Last Report", command=self.open_last_report).pack(side="right", padx=8)
        preview = ttk.LabelFrame(self, text="  Preview — no files change until you click Organise  ", padding=8)
        preview.pack(fill="both", expand=True, padx=20, pady=(6, 4))
        columns = ("original", "identified", "state", "destination")
        self.tree = ttk.Treeview(preview, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("original", text="Original file")
        self.tree.heading("identified", text="Movie title / year")
        self.tree.heading("state", text="Identification")
        self.tree.heading("destination", text="Proposed destination")
        self.tree.column("original", width=220)
        self.tree.column("identified", width=200)
        self.tree.column("state", width=200)
        self.tree.column("destination", width=470)
        scroll = ttk.Scrollbar(preview, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.show_selected_details)
        footer = ttk.Frame(self, padding=(20, 7, 20, 17))
        footer.pack(fill="x")
        self.details = StringVar(value="Select a movie row to see its full metadata (when available).")
        ttk.Label(footer, textvariable=self.details, wraplength=1120).pack(anchor="w", pady=(0, 7))
        self.progress = ttk.Progressbar(footer, mode="determinate", maximum=100)
        self.progress.pack(fill="x")
        ttk.Label(footer, textvariable=self.status).pack(anchor="w", pady=(7, 0))
        ttk.Label(footer, text="Movies needing review stay untouched. Existing files are never overwritten. Logs saved to Local AppData.", font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 0))

    def custom_changed(self):
        self.preset.set("Custom levels")
        self.refresh_preview()

    def update_source_summary(self):
        names = [n for n in self.source_priority if self.source_enabled[n].get()]
        tags = {"TMDB": "TMDB" + (" ✓" if self.token.get() else " (key needed)"),
                "OMDb": "OMDb" + (" ✓" if self.omdb_key.get() else " (key needed)"),
                "Local Catalogue": "CSV/JSON" + (" ✓" if self.catalogue_path.get() else " (file needed)")}
        self.source_summary.set("Priority: " + " → ".join(tags.get(n, n) for n in names))

    def metadata_settings(self):
        if self.busy:
            return
        window = Toplevel(self)
        window.title("Metadata Sources — priority, keys and local files")
        window.geometry("800x550")
        window.minsize(680, 500)
        window.configure(bg="#151a24")
        window.transient(self)
        window.grab_set()
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Enabled metadata sources (highest priority first)", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Select a source, then move it up or down. Disable any provider you don't want queried.").pack(anchor="w", pady=(3, 10))
        source_list = Listbox(frame, height=7, bg="#202838", fg="white", selectbackground="#35629b")
        source_list.pack(fill="x")
        priority = self.source_priority.copy()
        def populate(select=None):
            source_list.delete(0, END)
            for source in priority:
                source_list.insert(END, ("[ON]  " if self.source_enabled[source].get() else "[OFF] ") + source)
            if select is not None:
                source_list.selection_set(select)
        def shift(delta):
            selected = source_list.curselection()
            if not selected:
                return
            i = selected[0]
            j = i + delta
            if 0 <= j < len(priority):
                priority[i], priority[j] = priority[j], priority[i]
                populate(j)
        def toggle():
            selected = source_list.curselection()
            if not selected:
                return
            i = selected[0]
            item = priority[i]
            self.source_enabled[item].set(not self.source_enabled[item].get())
            populate(i)
        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(5, 12))
        ttk.Button(controls, text="Move Up", command=lambda: shift(-1)).pack(side="left")
        ttk.Button(controls, text="Move Down", command=lambda: shift(1)).pack(side="left", padx=5)
        ttk.Button(controls, text="Enable / Disable", command=toggle).pack(side="left", padx=5)
        ttk.Checkbutton(frame, text="Fill missing fields using other matching sources (more API requests)", variable=self.merge_metadata).pack(anchor="w", pady=(0, 12))
        ttk.Label(frame, text="TMDB API Read Access Token").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.token, show="•").pack(fill="x", pady=(3, 10))
        ttk.Label(frame, text="OMDb API Key").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.omdb_key, show="•").pack(fill="x", pady=(3, 10))
        ttk.Label(frame, text="Trakt API client ID (optional, public movie search)").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.trakt_key, show="•").pack(fill="x", pady=(3, 10))
        ttk.Label(frame, text="Local CSV / JSON catalogue (optional)").pack(anchor="w")
        path_frame = ttk.Frame(frame)
        path_frame.pack(fill="x", pady=(3, 4))
        ttk.Entry(path_frame, textvariable=self.catalogue_path).pack(side="left", fill="x", expand=True)
        def browse():
            picked = filedialog.askopenfilename(parent=window, title="Select movie catalogue", filetypes=[("Movie metadata", "*.csv *.json"), ("CSV", "*.csv"), ("JSON", "*.json")])
            if picked:
                self.catalogue_path.set(picked)
        ttk.Button(path_frame, text="Browse…", command=browse).pack(side="left", padx=(6, 0))
        ttk.Label(frame, text="NFO, Wikidata and Apple Movies need no key. TMDB, OMDb and Trakt are opt-in and require credentials. Keys use Windows DPAPI.", font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
        def save():
            self.source_priority = priority
            self.update_source_summary()
            self.save_preferences()
            window.destroy()
        ttk.Button(frame, text="Apply Metadata Settings", command=save).pack(anchor="e", pady=(14, 0))
        window.protocol("WM_DELETE_WINDOW", save)

    def add_source(self):
        selected = filedialog.askdirectory(title="Choose a movie source folder")
        if selected:
            path = Path(selected).resolve()
            if path not in self.sources:
                self.sources.append(path)
                self.source_list.insert(END, str(path))

    def remove_source(self):
        for index in reversed(self.source_list.curselection()):
            self.source_list.delete(index)
            self.sources.pop(index)

    def pick_destination(self):
        selected = filedialog.askdirectory(title="Choose destination (new folders will be created inside)")
        if selected:
            self.destination.set(selected)
            self.refresh_preview()

    def queue_message(self, kind, data=None):
        self.events.put((kind, data))

    def set_busy(self, busy):
        self.busy = busy
        self.scan_btn.configure(state="disabled" if busy else "normal")
        self.go_btn.configure(state="disabled" if busy or not any(m.target and m.path.exists() for m in self.movies) else "normal")
        self.review_folders_btn.configure(state="disabled" if busy or not self.no_video_candidates else "normal")
        self.review_dupes_btn.configure(state="disabled" if busy or not self.duplicate_suggestions else "normal")

    def scan(self):
        if self.busy:
            return
        if not self.sources:
            messagebox.showwarning(APP_NAME, "First select one or more source folders.")
            return
        if not self.destination.get().strip():
            messagebox.showwarning(APP_NAME, "Please choose a destination folder.")
            return
        dest = Path(self.destination.get()).resolve()
        # Re-sorting an existing collection in place is supported. Files already
        # at their target path are skipped rather than renamed to [2] copies.
        self.save_preferences()
        self.movies = []
        self.collection_groups = []
        self.collection_membership = {}
        self.no_video_candidates = []
        self.duplicate_suggestions = []
        self.collections_btn.configure(state="disabled")
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.progress.configure(value=0)
        self.set_busy(True)
        configuration = dict(priority=list(self.source_priority), enabled={k: v.get() for k, v in self.source_enabled.items()},
                             tmdb_token=self.token.get(), omdb_key=self.omdb_key.get(), trakt_key=self.trakt_key.get(),
                             catalogue_path=self.catalogue_path.get(), merge=self.merge_metadata.get())
        collection_options = (self.auto_collections.get(), int(self.collection_minimum.get()),
                              self.check_missing.get())
        threading.Thread(target=self.scan_thread, args=(dest, configuration, collection_options, self.scan_empty_folders.get()), daemon=True).start()

    def scan_thread(self, destination: Path, configuration: dict, collection_options=(True, 2, True), prune_on_scan=False):
        matches = set()
        paths = []
        try:
            for root in self.sources:
                root = root.resolve()
                # Only ignore output when it is a PROPER child of this source.
                # If source == destination, scan everything to allow in-place sorting.
                output_is_child = destination != root and destination.is_relative_to(root)
                for here, directories, files in os.walk(root, followlinks=False):
                    here_path = Path(here)
                    directories[:] = [d for d in directories if not (here_path / d).is_symlink() and
                                     not (output_is_child and (here_path / d).resolve().is_relative_to(destination))]
                    if output_is_child and here_path.resolve().is_relative_to(destination):
                        continue
                    for name in files:
                        file = here_path / name
                        if file.suffix.lower() in VIDEO_EXTENSIONS and not file.is_symlink():
                            key = str(file.resolve()).lower()
                            if key not in matches:
                                matches.add(key)
                                paths.append(file)
            self.queue_message("scan_total", len(paths))
            resolver = MetadataResolver(**configuration, tmdb_factory=TMDb)
            discovered = []
            try:
                for i, path in enumerate(sorted(paths, key=lambda p: str(p).lower()), 1):
                    title, year = filename_title_year(path)
                    movie = Movie(path, title, year)
                    try:
                        movie.metadata, movie.match_state = resolver.lookup(path, title, year)
                    except Exception as e:
                        movie.match_state = "Lookup failed / needs review"
                    discovered.append(movie)
                    self.queue_message("movie", (movie, i, len(paths)))
            finally:
                resolver.close()
            auto, minimum, missing_enabled = collection_options
            if auto:
                self.queue_message("collection_progress", None)
                tmdb = None
                try:
                    if configuration["enabled"].get("TMDB") and configuration.get("tmdb_token"):
                        tmdb = TMDb(configuration["tmdb_token"])
                        # Enrich confident non-TMDB matches with franchise ID, but do
                        # not overwrite their trusted cast/genre/studio metadata.
                        for movie in discovered:
                            if movie.match_state.startswith("Matched:") and not movie.metadata.get("collection_id"):
                                try:
                                    md, state = tmdb.lookup(movie.metadata.get("title") or movie.title,
                                                            movie.metadata.get("year") or movie.year)
                                except (RuntimeError, ValueError, OSError):
                                    # An API outage must not prevent offline sorting.
                                    continue
                                if state == "Matched" and md.get("collection_id"):
                                    old_name = movie.metadata.get("collection")
                                    if not old_name or key_name(old_name) == key_name(md.get("collection")):
                                        for k in ("collection", "collection_id", "tmdb_id"):
                                            if not movie.metadata.get(k):
                                                movie.metadata[k] = md.get(k)
                    groups = collect(discovered, minimum,
                                     tmdb.collection_parts if tmdb and missing_enabled else None)
                finally:
                    if tmdb:
                        tmdb.close()
                self.queue_message("collections", groups)
            suggestions = lower_quality_duplicates(discovered)
            self.queue_message("duplicates", suggestions)
            # Scan entire source subtrees, rather than treating a folder of movie
            # artwork/subtitles as empty. Never delete non-movie files on a scan.
            folders = no_video_folders(self.sources, destination, VIDEO_EXTENSIONS)
            removed_empty = 0
            if prune_on_scan:
                for candidate in folders:
                    removed_empty += prune_empty_tree(candidate.path, self.sources, destination)
                folders = no_video_folders(self.sources, destination, VIDEO_EXTENSIONS)
            self.queue_message("movie_free_folders", (folders, removed_empty))
            self.queue_message("scan_done", None)
        except Exception as e:
            self.queue_message("error", str(e))

    def drain_events(self):
        processed = 0
        while processed < 100:
            try:
                kind, data = self.events.get_nowait()
            except queue.Empty:
                break
            processed += 1
            if kind == "scan_total":
                self.status.set(f"Found {data} movie files. Identifying…")
            elif kind == "movie":
                movie, i, total = data
                self.movies.append(movie)
                self.draw_movie(len(self.movies) - 1)
                self.progress.configure(value=int(i * 100 / max(total, 1)))
                self.status.set(f"Identified {i} of {total} files…")
            elif kind == "collection_progress":
                self.status.set("Identified video files. Looking for related films and checking collection catalogues…")
            elif kind == "collections":
                self.collection_groups = data
                self.collection_membership = {str(movie.path): group
                                              for group in data for movie in group.movies}
                self.collections_btn.configure(state="normal" if data else "disabled")
                self.refresh_preview()
            elif kind == "corrected":
                index, title, year, metadata, state = data
                movie = self.movies[index]
                movie.title, movie.year, movie.metadata, movie.match_state = title, year, metadata, state
                self.set_busy(False)
                self.collection_groups = []
                self.collection_membership = {}
                self.collections_btn.configure(state="disabled")
                self.refresh_preview()
                self.status.set(f"Updated {movie.path.name}: {state}. Re-scan to update collections.")
            elif kind == "duplicates":
                self.duplicate_suggestions = data
                self.review_dupes_btn.configure(state="normal" if data else "disabled")
            elif kind == "movie_free_folders":
                folders, removed = data
                self.no_video_candidates = [x for x in folders if x.contains_other_files]
                self.review_folders_btn.configure(state="normal" if self.no_video_candidates else "disabled")
                if removed:
                    self.details.set(f"Scan cleaned {removed} genuinely empty source subfolder(s). Other files were not deleted.")
            elif kind == "cleanup_complete":
                self.set_busy(False)
                self.no_video_candidates = []
                self.review_folders_btn.configure(state="disabled")
                self.status.set(data)
                messagebox.showinfo(APP_NAME, data)
            elif kind == "duplicates_complete":
                self.set_busy(False)
                self.duplicate_suggestions = []
                self.review_dupes_btn.configure(state="disabled")
                self.status.set(data)
                messagebox.showinfo(APP_NAME, data + "\nRescan your library to refresh the preview.")
            elif kind == "scan_done":
                self.set_busy(False)
                self.refresh_preview()
                review_count = sum(not confidently_identified(m) for m in self.movies)
                if not self.movies:
                    self.status.set("No movies found. If you used MOVE before, the originals are now in the destination. Add that destination as a source to re-sort them.")
                    messagebox.showinfo(APP_NAME, "No movie files found in the selected source folders.\n\nIf the first run used MOVE, the files are now in the destination folder. Choose that folder as a source and scan again.\n\nFor testing without removing source files, use COPY.")
                else:
                    found_missing = sum(len(c.missing) for c in self.collection_groups)
                    self.status.set(f"Scan finished: {len(self.movies)} videos • {len(self.collection_groups)} collections"
                                    f" • {found_missing} missing sequels • {review_count} unmatched"
                                    f" • {len(self.duplicate_suggestions)} quality duplicate suggestion(s)"
                                    f" • {len(self.no_video_candidates)} movie-free folder(s) for review.")
            elif kind == "work_progress":
                i, total, outcome = data
                self.progress.configure(value=int(i * 100 / max(total, 1)))
                self.status.set(f"Processed {i} of {total}: {outcome}")
            elif kind == "work_done":
                self.set_busy(False)
                self.refresh_preview()
                self.status.set(data)
                messagebox.showinfo(APP_NAME, data)
            elif kind == "undo_done":
                self.set_busy(False)
                self.status.set(data)
                messagebox.showinfo(APP_NAME, data)
            elif kind == "error":
                self.set_busy(False)
                self.status.set("Error: " + data)
                messagebox.showerror(APP_NAME, data)
        self._event_timer = self.after(120, self.drain_events)

    def plan_for(self, movie: Movie) -> tuple[Optional[Path], str]:
        if not self.destination.get().strip():
            return None, "Choose destination"
        # Never make up a genre/studio/actor from an unverified filename.
        # Match failures (including provider outages) are quarantined for review.
        # Copy/Move still follows the single global Action setting.
        destination = Path(self.destination.get())
        if self.route_unmatched.get() and not confidently_identified(movie):
            return unmatched_target(movie, destination), "Unmatched - needs review"
        preset = self.preset.get()
        levels = [x.get() for x in self.levels] if preset == "Custom levels" else []
        if not any(level != "None" for level in levels) and preset == "Custom levels":
            return None, "Select a sorting rule"
        effective = levels if preset == "Custom levels" else {
            "Genre": ["Genre"], "Studio": ["Studio"], "Lead Actor": ["Lead Actor"],
            "A-P / Q-Z": ["A-P / Q-Z"], "A-Z Letters": ["A-Z Letters"],
            "Year": ["Year"], "Decade": ["Decade"], "Genre / Year": ["Genre", "Year"],
            "Year / Genre": ["Year", "Genre"], "Studio / A-Z Letters": ["Studio", "A-Z Letters"],
            "Lead Actor / Year": ["Lead Actor", "Year"],
        }.get(preset, [])
        md = movie.metadata
        if "Selected Actor" in effective:
            actor = self.selected_actor.get().strip()
            if not actor or not any(normalize(actor) == normalize(a) for a in md.get("actors") or []):
                return None, "Selected actor not found in movie cast"
        field_checks = {"Genre": "genres", "Studio": "studios", "Lead Actor": "actors",
                        "Director": "directors", "Country": "countries", "Collection": "collection"}
        # Collection routing has priority over the ordinary genre/year rules.
        # It must still work for an NFO that only provides title + set name.
        destination = Path(self.destination.get())
        group = self.collection_membership.get(str(movie.path)) if self.auto_collections.get() else None
        if group:
            title = safe_name(md.get("title") or movie.title)
            year = md.get("year") or movie.year
            standard = f"{title} ({year})" if year else title
            folder = destination / "Collections" / safe_name(group.name)
            if self.movie_folder.get():
                folder /= safe_name(standard)
            stem = safe_name(standard) if self.rename.get() else safe_name(movie.path.stem)
            return folder / (stem + movie.path.suffix), "Collection"
        for level in effective:
            if level in field_checks and not md.get(field_checks[level]):
                return None, f"Needs metadata: {level}"
            if level in ("Year", "Decade") and not (md.get("year") or movie.year):
                return None, "Year missing"
        destination = Path(self.destination.get())
        if preset == "Custom levels":
            return main_target_custom(movie, destination, effective, self.movie_folder.get(), self.rename.get(), self.selected_actor.get()), "Ready"
        return main_target(movie, destination, preset, self.movie_folder.get(), self.rename.get()), "Ready"

    def draw_movie(self, index: int):
        movie = self.movies[index]
        target, note = self.plan_for(movie)
        movie.target = target
        movie.notes = note
        md = movie.metadata
        title = md.get("title") or movie.title
        year = md.get("year") or movie.year
        state = movie.match_state + (" → unmatched" if note.startswith("Unmatched") else "")
        values = (movie.path.name, f"{title} ({year})" if year else title, state, str(target) if target else note)
        self.tree.insert("", "end", iid=str(index), values=values)

    def refresh_preview(self):
        for index, movie in enumerate(self.movies):
            target, note = self.plan_for(movie)
            movie.target, movie.notes = target, note
            md = movie.metadata
            title, year = md.get("title") or movie.title, md.get("year") or movie.year
            state = movie.match_state + (" → unmatched" if note.startswith("Unmatched") else "")
            values = (movie.path.name, f"{title} ({year})" if year else title, state, str(target) if target else note)
            if self.tree.exists(str(index)):
                self.tree.item(str(index), values=values)
        if not self.busy:
            self.go_btn.configure(state="normal" if any(m.target and m.path.exists() for m in self.movies) else "disabled")

    def show_selected_details(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        movie = self.movies[int(selected[0])]
        md = movie.metadata
        self.details.set(
            "Genre: " + (", ".join(md.get("genres") or []) or "Unknown") +
            "    |    Studio: " + (", ".join(md.get("studios") or []) or "Unknown") +
            "    |    Actors: " + (", ".join((md.get("actors") or [])[:5]) or "Unknown") +
            "    |    Directors: " + (", ".join(md.get("directors") or []) or "Unknown") +
            "    |    Sources: " + (", ".join(md.get("sources") or []) or "Filename")
        )

    def show_collections(self):
        """Read-only collection and missing-movie dashboard; no file changes."""
        if not self.collection_groups:
            messagebox.showinfo(APP_NAME, "No collections with enough distinct owned titles were found in this scan.")
            return
        window = Toplevel(self)
        window.title("MovieSort Pro V3.1 — Collections & Missing Movies")
        window.geometry("1040x680")
        window.minsize(780, 480)
        window.configure(bg="#151a24")
        frame = ttk.Frame(window, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="Movie collections", font=("Segoe UI", 17, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Missing = released films not identified anywhere in your selected source folders. "
                  "Unknown catalogue status is NOT a complete collection.", wraplength=980).pack(anchor="w", pady=(4, 10))
        columns = ("name", "owned", "missing", "upcoming", "status")
        summaries = ttk.Treeview(frame, columns=columns, show="headings", height=8)
        for name, caption, width in (("name", "Collection", 225), ("owned", "Owned", 65),
                                     ("missing", "Missing", 80), ("upcoming", "Unreleased / undated", 130),
                                     ("status", "Catalogue status", 380)):
            summaries.heading(name, text=caption)
            summaries.column(name, width=width, stretch=name in ("name", "status"))
        summaries.pack(fill="x")
        details_label = StringVar(value="Select a collection to see its films in series order.")
        ttk.Label(frame, textvariable=details_label, wraplength=960).pack(anchor="w", pady=(12, 5))
        detail_cols = ("order", "movie", "released", "state")
        details = ttk.Treeview(frame, columns=detail_cols, show="headings")
        for name, label, width in (("order", "#", 50), ("movie", "Film", 430),
                                   ("released", "Year / Release", 155), ("state", "Status", 250)):
            details.heading(name, text=label)
            details.column(name, width=width, stretch=name == "movie")
        details.pack(fill="both", expand=True)
        missing_total = sum(len(group.missing) for group in self.collection_groups)
        ttk.Label(frame, text=f"{len(self.collection_groups)} collections detected; {missing_total} released film(s) not found in scanned folders.").pack(anchor="w", pady=8)
        for index, group in enumerate(self.collection_groups):
            checked = group.catalogue_status == "Checked against TMDB"
            summaries.insert("", "end", iid=str(index), values=(
                group.name, group.count, len(group.missing) if checked else "?",
                len(group.upcoming) if checked else "?", group.catalogue_status))

        def selected_group():
            selected = summaries.selection()
            if not selected:
                return None
            return self.collection_groups[int(selected[0])]

        def show_group(_event=None):
            group = selected_group()
            if group is None:
                return
            for item in details.get_children():
                details.delete(item)
            details_label.set(group.name + " — " + group.catalogue_status)
            # Use the TMDB release-order catalogue where available. Explicit
            # collection_order from the user's catalogue wins over release order.
            sorted_owned = sorted(group.owned, key=lambda movie: movie_order(movie, group))
            for index, movie in enumerate(sorted_owned, 1):
                md = movie.metadata
                title = md.get("title") or movie.title
                year = md.get("year") or movie.year or "Unknown"
                details.insert("", "end", values=(index, title, str(year), "In scanned library"))
            for part in group.ordered_parts:
                if part not in group.missing and part not in group.upcoming:
                    continue
                missing = part in group.missing
                details.insert("", "end", values=("—", part.get("title", ""),
                                  part.get("release_date") or "Date unknown",
                                  "MISSING (released)" if missing else "Upcoming / release date unconfirmed"))
        summaries.bind("<<TreeviewSelect>>", show_group)
        if self.collection_groups:
            summaries.selection_set("0")
            show_group()

        def export_missing():
            target = filedialog.asksaveasfilename(parent=window, title="Export missing movies",
                        defaultextension=".csv", initialfile="MovieSortPro-Missing-Movies.csv",
                        filetypes=[("CSV", "*.csv"), ("Text file", "*.txt")])
            if not target:
                return
            rows = [(group.name, part.get("title", ""), part.get("release_date", ""),
                     "Missing from scanned folders")
                    for group in self.collection_groups for part in group.missing]
            try:
                if Path(target).suffix.lower() == ".txt":
                    with open(target, "w", encoding="utf-8") as f:
                        if not rows:
                            f.write("No confirmed missing released titles among checked collections.\n")
                        for collection, title, release, status in rows:
                            f.write(f"{collection}: {title} ({release}) — {status}\n")
                else:
                    with open(target, "w", newline="", encoding="utf-8-sig") as f:
                        writer = csv.writer(f)
                        writer.writerow(("Collection", "Movie", "Release Date", "Status"))
                        writer.writerows(rows)
                messagebox.showinfo(APP_NAME, f"Missing movie report saved:\n{target}", parent=window)
            except OSError as exc:
                messagebox.showerror(APP_NAME, f"Could not export report: {exc}", parent=window)
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(8, 0))
        ttk.Button(bar, text="Export missing list (CSV/TXT)", command=export_missing).pack(side="left")
        ttk.Button(bar, text="Close", command=window.destroy).pack(side="right")

    def review_movie_free_folders(self):
        """Explicitly approve sending NON-EMPTY, movie-free folders to Recycle Bin."""
        if self.busy or not self.no_video_candidates:
            messagebox.showinfo(APP_NAME, "No non-empty movie-free folders require review.")
            return
        candidates = [x for x in self.no_video_candidates if safe_to_recycle(
            x.path, self.sources, Path(self.destination.get()), VIDEO_EXTENSIONS)]
        if not candidates:
            messagebox.showwarning(APP_NAME, "Folder contents changed; rescan before continuing.")
            return
        lines = "\n".join(str(x.path) for x in candidates)
        if not messagebox.askyesno(APP_NAME,
                f"Send {len(candidates)} movie-free folder(s) AND ALL THEIR CONTENTS to the Recycle Bin?"
                "\n\nThese folders contain non-movie files (e.g. images, text or subtitles)."
                " They will NOT be permanently deleted. Only proceed if you do not need them."
                f"\n\n{lines[:2800]}\n\nThis action is separate from Copy/Move and can't be undone by MovieSort Pro."):
            return
        self.set_busy(True)
        def worker():
            done, skipped = 0, 0
            for item in candidates:
                if not safe_to_recycle(item.path, self.sources, Path(self.destination.get()), VIDEO_EXTENSIONS):
                    skipped += 1
                    continue
                try:
                    recycle(item.path)
                    done += 1
                except (OSError, RuntimeError) as exc:
                    skipped += 1
                    self.queue_message("work_progress", (done+skipped, len(candidates), f"Recycle failed: {exc}"))
            self.queue_message("cleanup_complete", f"Sent {done} movie-free folder(s) to the Recycle Bin; {skipped} skipped/failed.")
        threading.Thread(target=worker, daemon=True).start()

    def review_duplicates(self):
        """Conservatively identified, lower-quality versions require confirmation."""
        if self.busy or not self.duplicate_suggestions:
            messagebox.showinfo(APP_NAME, "No confidently ranked lower-quality duplicates were found."
                                " Install FFmpeg/ffprobe if you want codec-based comparisons.")
            return
        candidates = self.duplicate_suggestions
        summary = "\n".join(f"Lower: {a.name} ({qa.width}x{qa.height})  |  Keep: {b.name} ({qb.width}x{qb.height})"
                            for a,b,qa,qb in candidates)
        if not messagebox.askyesno(APP_NAME,
                f"Send {len(candidates)} lower-quality movie(s) to the Recycle Bin?"
                "\n\nOnly confidently matched identical films/editions are compared."
                " This does not check audio languages or subtitle preferences."
                " Examine these pairs carefully before continuing."
                f"\n\n{summary[:2800]}\n\nThe retained file must still exist."):
            return
        self.set_busy(True)
        def worker():
            deleted = skipped = 0
            for low, best, lower_quality, higher_quality in candidates:
                try:
                    if (not low.is_file() or not best.is_file() or low.resolve()==best.resolve() or
                            not any(low.resolve().is_relative_to(root.resolve()) for root in self.sources)):
                        skipped += 1
                        continue
                    # A video may have been edited since the scan: re-probe before deletion.
                    from library_maintenance import probe_video, clearly_lower
                    if not clearly_lower(probe_video(low), probe_video(best)):
                        skipped += 1
                        continue
                    recycle(low)
                    deleted += 1
                except (OSError, RuntimeError) as exc:
                    skipped += 1
                    self.queue_message("work_progress", (deleted+skipped, len(candidates), f"Recycle failed: {exc}"))
            self.queue_message("duplicates_complete", f"Sent {deleted} lower-quality duplicate(s) to Recycle Bin; {skipped} skipped/failed.")
        threading.Thread(target=worker, daemon=True).start()

    def correct_selected(self):
        if self.busy:
            return
        selected = self.tree.selection()
        if not selected:
            messagebox.showinfo(APP_NAME, "Select a film in the preview first.")
            return
        if not any((self.token.get().strip(), self.omdb_key.get().strip(), self.source_enabled["Wikidata"].get())):
            messagebox.showinfo(APP_NAME, "Enable at least one online metadata source or add its API key.")
            return
        movie = self.movies[int(selected[0])]
        entered = simpledialog.askstring("Correct movie title", "Film title to search for:", initialvalue=movie.title, parent=self)
        if not entered:
            return
        year = simpledialog.askinteger("Release year", "Release year (Cancel if unknown):", initialvalue=movie.year, minvalue=1888, maxvalue=2100, parent=self)
        configuration = dict(priority=list(self.source_priority), enabled={k: v.get() for k, v in self.source_enabled.items()},
                             tmdb_token=self.token.get(), omdb_key=self.omdb_key.get(),
                             catalogue_path=self.catalogue_path.get(), merge=self.merge_metadata.get())
        self.set_busy(True)
        def worker():
            resolver = None
            try:
                resolver = MetadataResolver(**configuration, tmdb_factory=TMDb)
                md, state = resolver.lookup(movie.path, entered.strip(), year)
                self.queue_message("corrected", (int(selected[0]), entered.strip(), year, md, state))
            except Exception as e:
                self.queue_message("error", str(e))
            finally:
                if resolver:
                    resolver.close()
        threading.Thread(target=worker, daemon=True).start()

    def organise(self):
        if self.busy:
            return
        self.refresh_preview()
        ready = [(m.path, m.target, m.notes.startswith("Unmatched"))
                 for m in self.movies if m.target and m.path.exists()]
        skipped = len(self.movies) - len(ready)
        if not ready:
            reason = ("Files may already have been MOVED. Scan the destination as your new source to re-sort the library."
                      if self.movies and all(not m.path.exists() for m in self.movies)
                      else "No films are ready. Review metadata, source paths or sorting method.")
            messagebox.showwarning(APP_NAME, reason)
            return
        self.save_preferences()
        unmatched = sum(is_unmatched for _, _, is_unmatched in ready)
        if not messagebox.askyesno(APP_NAME, f"{self.mode.get()} {len(ready)} movie(s) to their previewed destinations?"
                                     f"\n\n{unmatched} unidentified movie(s) will go to the unmatched folder."
                                     f"\n{skipped} file(s) will be untouched. Existing files won't be overwritten."
                                     "\n\nConfirm to proceed."):
            return
        self.set_busy(True)
        self.progress.configure(value=0)
        args = (ready, self.mode.get(), self.rename.get(), self.include_sidecars.get(),
                self.remove_empty_folders.get(), list(self.sources), Path(self.destination.get()).resolve())
        threading.Thread(target=self.organise_thread, args=args, daemon=True).start()

    def organise_thread(self, ready, mode, rename, with_sidecars, cleanup_folders=False, source_roots=None, destination=None):
        reserved = set()
        journal = []
        success = skip = failures = unmatched_success = 0
        report = []
        moved_origins = []
        log_path = app_dir() / "last_run.json"
        is_move = mode == "Move"
        try:
            for i, (source, planned, is_unmatched) in enumerate(ready, 1):
                try:
                    if not source.exists():
                        raise FileNotFoundError(f"Source has disappeared: {source}")
                    target, note = choose_target(source, planned, reserved)
                    if target is None:
                        skip += 1
                        report.append((str(source), str(planned), "SKIPPED", note))
                        self.queue_message("work_progress", (i, len(ready), note))
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    extra = sidecars(source) if with_sidecars else []
                    if is_move:
                        shutil.move(str(source), str(target))
                        journal.append({"from": str(source), "to": str(target)})
                        moved_origins.append(source)
                        log_path.write_text(json.dumps({"mode": "Move", "changes": journal}, indent=2), encoding="utf-8")
                    else:
                        shutil.copy2(source, target)
                    success += 1
                    if is_unmatched:
                        unmatched_success += 1
                    report.append((str(source), str(target), "UNMATCHED" if is_unmatched else "OK",
                                   ("Unidentified; saved for review" if is_unmatched else note)))
                    for asset in extra:
                        try:
                            desired_asset = companion_target(asset, source, target, rename and not is_unmatched)
                            asset_target, _ = choose_target(asset, desired_asset, reserved)
                            if asset_target is None:
                                continue
                            asset_target.parent.mkdir(parents=True, exist_ok=True)
                            if is_move:
                                shutil.move(str(asset), str(asset_target))
                                journal.append({"from": str(asset), "to": str(asset_target)})
                                moved_origins.append(asset)
                                log_path.write_text(json.dumps({"mode": "Move", "changes": journal}, indent=2), encoding="utf-8")
                            else:
                                shutil.copy2(asset, asset_target)
                        except Exception as e:
                            # Never undo the successfully processed movie because a sidecar failed.
                            failures += 1
                            report.append((str(asset), str(target.parent), "ERROR", str(e)))
                    self.queue_message("work_progress", (i, len(ready), source.name))
                except Exception as e:
                    failures += 1
                    report.append((str(source), str(planned), "ERROR", str(e)))
                    self.queue_message("work_progress", (i, len(ready), f"Error processing {source.name}: {e}"))
            deleted_folders = 0
            if is_move and cleanup_folders and moved_origins:
                deleted_folders = cleanup_empty_source_dirs(moved_origins, source_roots or [], destination)
            with (app_dir() / "last_run_report.csv").open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(("Source", "Destination", "Status", "Details"))
                writer.writerows(report)
            summary = (f"Finished: {success} movie(s) {('moved' if is_move else 'copied')}"
                       f" ({unmatched_success} unmatched), {skip} already present/skipped, {failures} errors.\n\n"
                       f"Report: {app_dir() / 'last_run_report.csv'}")
            if is_move and cleanup_folders:
                summary += f"\n{deleted_folders} empty source subfolder(s) removed (source roots retained)."
            if is_move and success:
                summary += "\n\nThe original files were moved, so the old source may now be empty. To sort them again, use the destination as the source."
            elif skip and not success:
                summary += "\n\nNothing new was transferred: the files may already be in their correct locations."
            self.queue_message("work_done", summary)
        except Exception as e:
            self.queue_message("error", str(e))

    def undo_last_move(self):
        if self.busy:
            return
        path = app_dir() / "last_run.json"
        if not path.exists():
            messagebox.showinfo(APP_NAME, "No recorded move to undo.")
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
        changes = payload.get("changes") or []
        if payload.get("mode") != "Move" or not changes:
            messagebox.showinfo(APP_NAME, "No recorded move to undo.")
            return
        if not messagebox.askyesno(APP_NAME, f"Attempt to restore {len(changes)} moved file(s)?\nOnly paths whose originals are empty will be restored. No files are overwritten."):
            return
        self.set_busy(True)
        threading.Thread(target=self.undo_thread, args=(changes, path), daemon=True).start()

    def undo_thread(self, changes, path):
        restored = 0
        remaining = []
        for item in reversed(changes):
            previous, current = Path(item["from"]), Path(item["to"])
            try:
                if current.is_file() and not previous.exists():
                    previous.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(current), str(previous))
                    restored += 1
                else:
                    remaining.append(item)
            except OSError:
                remaining.append(item)
        path.write_text(json.dumps({"mode": "Move", "changes": list(reversed(remaining))}, indent=2), encoding="utf-8")
        self.queue_message("undo_done", f"Restored {restored} file(s). {len(remaining)} could not be restored or had already changed.")


if __name__ == "__main__":
    App().mainloop()
