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
from difflib import SequenceMatcher
from pathlib import Path
from tkinter import Tk, Toplevel, StringVar, BooleanVar, Listbox, END, filedialog, messagebox, simpledialog
from tkinter import ttk
from typing import Optional
from metadata_sources import PROVIDERS, MetadataResolver
from preferences import read_preferences, write_preferences, seal, unseal

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
        key = f"v2|{normalize(title)}|{year or ''}"
        cached = self.conn.execute("SELECT value FROM movies WHERE lookup_key=?", (key,)).fetchone()
        if cached:
            saved = json.loads(cached[0])
            return saved["metadata"], saved["state"]
        params = {"query": title, "include_adult": "false", "language": "en-GB"}
        if year:
            params["year"] = year
        results = self.get("search/movie", params).get("results", [])
        if not results and year:
            params.pop("year")
            results = self.get("search/movie", params).get("results", [])
        scored = []
        for result in results:
            options = [result.get("title", ""), result.get("original_title", "")]
            title_score = max(SequenceMatcher(None, normalize(title), normalize(t)).ratio() for t in options)
            release_year = int(result.get("release_date", "0000")[:4] or 0)
            penalty = 0 if not year or release_year == year else (0.08 if abs(release_year - year) == 1 else 0.32)
            scored.append((title_score - penalty, result))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        if not scored or scored[0][0] < 0.76:
            return {}, "Needs review: no confident match"
        if len(scored) > 1 and (scored[0][0] - scored[1][0]) < 0.035:
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
            "plot": details.get("overview"),
            "imdb_id": details.get("imdb_id"),
            "tmdb_id": details["id"],
        }
        self.conn.execute("INSERT OR REPLACE INTO movies VALUES (?, ?, ?)", (key, json.dumps({"metadata": meta, "state": "Matched"}), time.time()))
        self.conn.commit()
        time.sleep(0.20)
        return meta, "Matched"

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
        self.title(APP_NAME + " V2.1 — Movie Library Organiser")
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
        self.events = queue.Queue()
        self.busy = False
        self.destination = StringVar()
        self.token = StringVar()
        self.omdb_key = StringVar()
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
                          ("include_sidecars", self.include_sidecars), ("merge_metadata", self.merge_metadata)):
            if isinstance(saved.get(name), bool):
                var.set(saved[name])
        if isinstance(saved.get("levels"), list):
            for var, level in zip(self.levels, saved["levels"]):
                if level in CUSTOM_LEVELS:
                    var.set(level)
        priority = saved.get("source_priority", [])
        if isinstance(priority, list) and set(priority) == set(PROVIDERS) and len(priority) == len(PROVIDERS):
            self.source_priority = priority
        enabled = saved.get("source_enabled", {})
        if isinstance(enabled, dict):
            for provider in PROVIDERS:
                if isinstance(enabled.get(provider), bool):
                    self.source_enabled[provider].set(enabled[provider])
        # On Windows, credentials are encrypted with the signed-in user's DPAPI key.
        self.token.set(unseal(saved.get("tmdb_protected", "")))
        self.omdb_key.set(unseal(saved.get("omdb_protected", "")))

    def save_preferences(self):
        prefs = {
            "sources": [str(p) for p in self.sources],
            "destination": self.destination.get(), "preset": self.preset.get(),
            "levels": [var.get() for var in self.levels],
            "selected_actor": self.selected_actor.get(), "mode": self.mode.get(),
            "rename": self.rename.get(), "movie_folder": self.movie_folder.get(),
            "include_sidecars": self.include_sidecars.get(),
            "source_priority": list(self.source_priority),
            "source_enabled": {k: var.get() for k, var in self.source_enabled.items()},
            "merge_metadata": self.merge_metadata.get(),
            "catalogue_path": self.catalogue_path.get(),
            "tmdb_protected": seal(self.token.get()),
            "omdb_protected": seal(self.omdb_key.get()),
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
            "MovieSort Pro V2.1\nFree and open-source movie organiser (MIT).\n\n"
            "This product uses the TMDB API but is not endorsed or certified by TMDB.\n"
            "TMDB: https://www.themoviedb.org\n\n"
            "Other optional sources: OMDb (https://www.omdbapi.com), "
            "Wikidata (https://www.wikidata.org), local NFO and CSV/JSON.\n\n"
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
        action = ttk.Frame(self, padding=(20, 7, 20, 5))
        action.pack(fill="x")
        self.scan_btn = ttk.Button(action, text="1  SCAN & IDENTIFY", command=self.scan)
        self.scan_btn.pack(side="left")
        self.go_btn = ttk.Button(action, text="2  ORGANISE MOVIES", command=self.organise, state="disabled")
        self.go_btn.pack(side="left", padx=8)
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
        source_list = Listbox(frame, height=5, bg="#202838", fg="white", selectbackground="#35629b")
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
        ttk.Label(frame, text="Local CSV / JSON catalogue (optional)").pack(anchor="w")
        path_frame = ttk.Frame(frame)
        path_frame.pack(fill="x", pady=(3, 4))
        ttk.Entry(path_frame, textvariable=self.catalogue_path).pack(side="left", fill="x", expand=True)
        def browse():
            picked = filedialog.askopenfilename(parent=window, title="Select movie catalogue", filetypes=[("Movie metadata", "*.csv *.json"), ("CSV", "*.csv"), ("JSON", "*.json")])
            if picked:
                self.catalogue_path.set(picked)
        ttk.Button(path_frame, text="Browse…", command=browse).pack(side="left", padx=(6, 0))
        ttk.Label(frame, text="NFO and Wikidata require no API key. On Windows, API keys are saved using user-bound DPAPI encryption.", font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
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
        for item in self.tree.get_children():
            self.tree.delete(item)
        self.progress.configure(value=0)
        self.set_busy(True)
        configuration = dict(priority=list(self.source_priority), enabled={k: v.get() for k, v in self.source_enabled.items()},
                             tmdb_token=self.token.get(), omdb_key=self.omdb_key.get(),
                             catalogue_path=self.catalogue_path.get(), merge=self.merge_metadata.get())
        threading.Thread(target=self.scan_thread, args=(dest, configuration), daemon=True).start()

    def scan_thread(self, destination: Path, configuration: dict):
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
            try:
                for i, path in enumerate(sorted(paths, key=lambda p: str(p).lower()), 1):
                    title, year = filename_title_year(path)
                    movie = Movie(path, title, year)
                    try:
                        movie.metadata, movie.match_state = resolver.lookup(path, title, year)
                    except Exception as e:
                        movie.match_state = "Lookup failed / needs review"
                    self.queue_message("movie", (movie, i, len(paths)))
            finally:
                resolver.close()
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
            elif kind == "corrected":
                index, title, year, metadata, state = data
                movie = self.movies[index]
                movie.title, movie.year, movie.metadata, movie.match_state = title, year, metadata, state
                self.set_busy(False)
                self.refresh_preview()
                self.status.set(f"Updated {movie.path.name}: {state}.")
            elif kind == "scan_done":
                self.set_busy(False)
                self.refresh_preview()
                review_count = sum(not m.match_state.startswith("Matched:") for m in self.movies)
                if not self.movies:
                    self.status.set("No movies found. If you used MOVE before, the originals are now in the destination. Add that destination as a source to re-sort them.")
                    messagebox.showinfo(APP_NAME, "No movie files found in the selected source folders.\n\nIf the first run used MOVE, the files are now in the destination folder. Choose that folder as a source and scan again.\n\nFor testing without removing source files, use COPY.")
                else:
                    self.status.set(f"Scan finished: {len(self.movies)} videos • {review_count} need metadata review. Check preview before organising.")
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
        values = (movie.path.name, f"{title} ({year})" if year else title, movie.match_state, str(target) if target else note)
        self.tree.insert("", "end", iid=str(index), values=values)

    def refresh_preview(self):
        for index, movie in enumerate(self.movies):
            target, note = self.plan_for(movie)
            movie.target, movie.notes = target, note
            md = movie.metadata
            title, year = md.get("title") or movie.title, md.get("year") or movie.year
            values = (movie.path.name, f"{title} ({year})" if year else title, movie.match_state, str(target) if target else note)
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
        ready = [(m.path, m.target) for m in self.movies if m.target and m.path.exists()]
        skipped = len(self.movies) - len(ready)
        if not ready:
            reason = ("Files may already have been MOVED. Scan the destination as your new source to re-sort the library."
                      if self.movies and all(not m.path.exists() for m in self.movies)
                      else "No films are ready. Review metadata, source paths or sorting method.")
            messagebox.showwarning(APP_NAME, reason)
            return
        self.save_preferences()
        if not messagebox.askyesno(APP_NAME, f"{self.mode.get()} {len(ready)} movie(s) to their previewed destinations?\n\n{skipped} file(s) will be untouched. Existing files won't be overwritten.\n\nConfirm to proceed."):
            return
        self.set_busy(True)
        self.progress.configure(value=0)
        args = (ready, self.mode.get(), self.rename.get(), self.include_sidecars.get())
        threading.Thread(target=self.organise_thread, args=args, daemon=True).start()

    def organise_thread(self, ready, mode, rename, with_sidecars):
        reserved = set()
        journal = []
        success = skip = failures = 0
        report = []
        log_path = app_dir() / "last_run.json"
        is_move = mode == "Move"
        try:
            for i, (source, planned) in enumerate(ready, 1):
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
                        log_path.write_text(json.dumps({"mode": "Move", "changes": journal}, indent=2), encoding="utf-8")
                    else:
                        shutil.copy2(source, target)
                    success += 1
                    report.append((str(source), str(target), "OK", note))
                    for asset in extra:
                        try:
                            desired_asset = companion_target(asset, source, target, rename)
                            asset_target, _ = choose_target(asset, desired_asset, reserved)
                            if asset_target is None:
                                continue
                            asset_target.parent.mkdir(parents=True, exist_ok=True)
                            if is_move:
                                shutil.move(str(asset), str(asset_target))
                                journal.append({"from": str(asset), "to": str(asset_target)})
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
            with (app_dir() / "last_run_report.csv").open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.writer(f)
                writer.writerow(("Source", "Destination", "Status", "Details"))
                writer.writerows(report)
            summary = (f"Finished: {success} movie(s) {('moved' if is_move else 'copied')}, "
                       f"{skip} already present/skipped, {failures} errors.\n\n"
                       f"Report: {app_dir() / 'last_run_report.csv'}")
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
