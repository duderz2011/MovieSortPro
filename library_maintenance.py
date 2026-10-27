"""Non-destructive scan cleanup and conservative movie duplicate review.

Never remove non-video content during an automatic scan. Explicit recycle actions
require the optional send2trash package and a second, interactive confirmation.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

# Formats that the organiser may not sort yet, but must never classify as non-movie data.
VIDEO_PROTECTION_EXTENSIONS = {".vob", ".divx", ".flv", ".3gp", ".mts", ".m2v", ".asf", ".ogv", ".rmvb", ".mxf", ".strm"}


@dataclass(frozen=True)
class NoVideoFolder:
    path: Path
    contains_other_files: bool


def no_video_folders(roots: Iterable[Path], destination: Path | None, video_extensions: set[str]) -> list[NoVideoFolder]:
    """Return maximal safe subtrees with no videos anywhere beneath them.

    Directories with symlinks, unreadable directories, source roots, or output
    trees are excluded; directory symlinks are never followed.
    """
    video_extensions = set(video_extensions) | VIDEO_PROTECTION_EXTENSIONS
    roots = {Path(p).resolve() for p in roots}
    output = Path(destination).resolve() if destination else None
    protected = set(roots)
    if output:
        protected.add(output)
    candidates = {}
    for root in sorted(roots, key=str):
        if not root.is_dir() or root.is_symlink():
            continue
        records = {}
        unreadable = set()
        def onerror(exc):
            if getattr(exc, "filename", None):
                unreadable.add(Path(exc.filename))
        for here, names, files in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
            current = Path(here)
            # Exclude all selected roots (including a second source nested inside this one)
            # and output trees, not just the top-level destination folder.
            names[:] = [name for name in names if not (current/name).is_symlink() and
                        (current/name) not in protected and
                        not (output and (current/name).is_relative_to(output))]
            if current == output or (output and current.is_relative_to(output)):
                names[:] = []
                continue
            records[current] = (list(names), list(files))
        # No child can be classified video-free if some descendant failed traversal.
        flagged = set()
        for here in sorted(records, key=lambda p: len(p.parts), reverse=True):
            children, files = records[here]
            childpaths = [here / child for child in children]
            uncertain = any(here == p or p.is_relative_to(here) for p in unreadable)
            contains_video = any(Path(f).suffix.lower() in video_extensions for f in files)
            contains_video |= any(p in flagged for p in childpaths)
            if contains_video or uncertain:
                flagged.add(here)
                continue
            if here not in protected and here != root and not here.is_symlink():
                candidates[here] = NoVideoFolder(here, bool(files) or any(p in candidates and candidates[p].contains_other_files for p in childpaths))
        # Avoid child candidates when their parent is also video-free.
    paths = set(candidates)
    return sorted((c for p,c in candidates.items() if not any(a in paths for a in p.parents)), key=lambda x: str(x.path).lower())


def prune_empty_tree(folder: Path, roots: Iterable[Path], destination: Path | None = None) -> int:
    """Remove ONLY genuinely empty folders inside an identified video-free tree."""
    roots = {Path(p).resolve() for p in roots}
    output = Path(destination).resolve() if destination else None
    folder = Path(folder)
    if (not folder.exists() or folder.is_symlink() or folder.resolve() in roots
            or not any(folder.resolve().is_relative_to(r) for r in roots)
            or any(r.is_relative_to(folder.resolve()) for r in roots)
            or (output and (folder.resolve() == output or folder.resolve().is_relative_to(output)
                            or output.is_relative_to(folder.resolve())))):
        return 0
    deleted = 0
    for here, dirs, files in os.walk(folder, topdown=False, followlinks=False):
        target = Path(here)
        if target.is_symlink() or target.resolve() in roots:
            continue
        try:
            target.rmdir()  # Will fail if files, symlinks, or unvisited dirs remain.
            deleted += 1
        except OSError:
            pass
    return deleted


def safe_to_recycle(path: Path, roots: Iterable[Path], destination: Path | None, video_extensions: set[str]) -> bool:
    """Recheck a candidate immediately before recycling it; fail closed."""
    video_extensions = set(video_extensions) | VIDEO_PROTECTION_EXTENSIONS
    path = Path(path)
    roots = {Path(p).resolve() for p in roots}
    target = path.resolve()
    output = Path(destination).resolve() if destination else None
    if path.is_symlink() or not path.is_dir() or target in roots or any(r.is_relative_to(target) for r in roots):
        return False
    if output and (target == output or output.is_relative_to(target) or target.is_relative_to(output)):
        return False
    if not any(target.is_relative_to(r) for r in roots):
        return False
    for here, dirs, files in os.walk(path, followlinks=False):
        if any((Path(here) / name).is_symlink() for name in dirs + files):
            return False
        if any(Path(f).suffix.lower() in video_extensions for f in files):
            return False
    return True


def recycle(path: Path):
    try:
        from send2trash import send2trash
    except ImportError as e:
        raise RuntimeError("Recycle Bin support requires: python -m pip install send2trash") from e
    send2trash(str(path))


@dataclass(frozen=True)
class VideoQuality:
    width: int
    height: int
    codec: str
    bitrate: int
    hdr: str


def probe_video(path: Path) -> VideoQuality | None:
    """Use actual codec/frame data, not filenames. Unknown quality is unranked."""
    exe = shutil.which('ffprobe')
    if not exe:
        return None
    try:
        p = subprocess.run([exe, '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height,codec_name,bit_rate,color_transfer,color_primaries:format=bit_rate',
            '-of', 'json', str(path)], capture_output=True, text=True, timeout=20, check=True)
        result = json.loads(p.stdout)
        video = (result.get('streams') or [{}])[0]
        return VideoQuality(int(video.get('width') or 0), int(video.get('height') or 0),
            str(video.get('codec_name') or '').lower(),
            int(video.get('bit_rate') or (result.get('format') or {}).get('bit_rate') or 0),
            str(video.get('color_transfer') or '').lower())
    except (subprocess.SubprocessError, OSError, ValueError, TypeError, IndexError, json.JSONDecodeError):
        return None


def movie_identity(movie) -> str | None:
    """Require a trusted provider match and stable identity, never guess from filenames."""
    state = str(getattr(movie,'match_state',''))
    md = getattr(movie,'metadata',{}) or {}
    if not (state == 'Matched' or state.startswith('Matched:')):
        return None
    if md.get('tmdb_id'):
        return 'tmdb:' + str(md['tmdb_id'])
    if md.get('imdb_id'):
        return 'imdb:' + str(md['imdb_id'])
    # Without a stable provider ID, title/year can conflate unrelated films.
    return None


def edition(path: Path) -> str:
    """Different editions/cuts never qualify for automatic quality deletion."""
    import re
    found = re.findall(r'(?i)\b(director.?s cut|extended|unrated|theatrical|remastered|criterion|3d|imax)\b', path.stem.replace('_',' ').replace('.',' '))
    return '+'.join(sorted(set(x.lower() for x in found)))


def clearly_lower(lower: VideoQuality | None, higher: VideoQuality | None) -> bool:
    """Conservative quality comparison; never delete based on bitrate across codecs."""
    if not lower or not higher or not min(lower.width,lower.height,higher.width,higher.height):
        return False
    if not lower.hdr or not higher.hdr or lower.hdr != higher.hdr:
        return False  # HDR vs SDR requires human review.
    lower_pixels, higher_pixels = lower.width*lower.height, higher.width*higher.height
    if higher_pixels >= 1.9*lower_pixels:
        return True
    if lower_pixels != higher_pixels or lower.codec != higher.codec:
        return False
    return lower.bitrate > 0 and higher.bitrate > lower.bitrate*1.35


def lower_quality_duplicates(movies, probe=probe_video) -> list[tuple[Path,Path,VideoQuality,VideoQuality]]:
    """Suggest lower quality duplicates. Never remove anything without explicit approval."""
    groups=defaultdict(list)
    for movie in movies:
        key=movie_identity(movie)
        if key and Path(movie.path).is_file():
            groups[(key, edition(Path(movie.path)))].append(movie)
    suggestions=[]
    for group in groups.values():
        if len(group)<2:
            continue
        qualities={m.path:probe(Path(m.path)) for m in group}
        for m in group:
            candidates=[other for other in group if other.path!=m.path and
                        clearly_lower(qualities[m.path], qualities[other.path])]
            if candidates:
                winner=max(candidates,key=lambda x:(qualities[x.path].width*qualities[x.path].height,
                                                   qualities[x.path].bitrate))
                suggestions.append((Path(m.path),Path(winner.path),qualities[m.path],qualities[winner.path]))
    return suggestions
