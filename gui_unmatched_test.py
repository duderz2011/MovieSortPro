"""End-to-end GUI regression for unmatched files.

Run with: xvfb-run -a python gui_unmatched_test.py (or on Windows desktop).
"""
import csv
import gc
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from MovieSortPro import App


def finish(app):
    deadline = time.monotonic() + 18
    while app.busy and time.monotonic() < deadline:
        app.update()
        time.sleep(.015)
    app.update()
    assert not app.busy, app.status.get()


def run_scan_and_sort(app):
    app.scan()
    finish(app)
    app.organise()
    finish(app)


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    os.environ['LOCALAPPDATA'] = str(tmp / 'prefs')
    src, dest = tmp / 'source', tmp / 'output'
    src.mkdir(); dest.mkdir()
    # One confident match with missing sort fields must NOT be labelled unmatched.
    (src / 'Known.Movie.2021.mkv').write_bytes(b'known movie video')
    (src / 'Known.Movie.2021.nfo').write_text('<movie><title>Known Movie</title><year>2021</year><genre>Action</genre></movie>')
    (src / 'Unknown.Movie.2022.mkv').write_bytes(b'unknown movie video')
    (src / 'Unknown.Movie.2022.en.srt').write_text('sample subtitle')
    (src / 'Other Movie.2008.avi').write_bytes(b'another unknown movie')

    app = App(); app.withdraw()
    app.sources = [src]; app.source_list.insert('end', str(src))
    app.destination.set(str(dest))
    app.mode.set('Copy')
    app.preset.set('Genre / Year')
    app.rename.set(True)
    for provider in app.source_enabled:
        app.source_enabled[provider].set(provider == 'Local NFO')
    with patch('MovieSortPro.messagebox.askyesno', return_value=True), \
         patch('MovieSortPro.messagebox.showinfo'), \
         patch('MovieSortPro.messagebox.showwarning'):
        run_scan_and_sort(app)
        assert len(app.movies) == 3
        assert (dest/'Action'/'2021'/'Known Movie (2021).mkv').is_file()
        assert (dest/'unmatched'/'Unknown.Movie.2022.mkv').is_file()
        assert (dest/'unmatched'/'Unknown.Movie.2022.en.srt').is_file()
        assert (dest/'unmatched'/'Other Movie.2008.avi').is_file()
        assert (src/'Unknown.Movie.2022.mkv').is_file(), 'Copy must preserve original'
        assert '2 unmatched' in app.status.get(), app.status.get()
        report = list(csv.DictReader((Path(os.environ['LOCALAPPDATA'])/'MovieSortPro'/'last_run_report.csv').open(encoding='utf-8-sig')))
        assert sum(r['Status']=='UNMATCHED' for r in report) == 2, report
        # Run twice: existing copies should be skipped instead of creating [2].
        run_scan_and_sort(app)
        assert not list((dest/'unmatched').glob('*[[]2[]]*'))
        assert '3 already present/skipped' in app.status.get(), app.status.get()
        # Disabled routing reverts to previous behaviour (filename-year and unknown genre).
        app.route_unmatched.set(False)
        app.refresh_preview()
        assert all(m.notes != 'Unmatched - needs review' for m in app.movies)
        app.route_unmatched.set(True)
        app.refresh_preview()

    app.destroy(); gc.collect()

    # MOVE action, then re-identify previously unmatched video and re-sort in place.
    src2, dest2 = tmp / 'move_source', tmp / 'move_output'
    src2.mkdir(); dest2.mkdir()
    (src2/'Hidden.Film.2019.mp4').write_bytes(b'hidden film video')
    (src2/'Hidden.Film.2019.en.srt').write_text('captions')
    app2 = App(); app2.withdraw()
    app2.sources=[src2]; app2.destination.set(str(dest2))
    app2.mode.set('Move')
    for provider in app2.source_enabled:
        app2.source_enabled[provider].set(provider == 'Local NFO')
    with patch('MovieSortPro.messagebox.askyesno', return_value=True), \
         patch('MovieSortPro.messagebox.showinfo'), \
         patch('MovieSortPro.messagebox.showwarning'):
        run_scan_and_sort(app2)
        target = dest2/'unmatched'/'Hidden.Film.2019.mp4'
        assert target.is_file()
        assert not (src2/'Hidden.Film.2019.mp4').exists()
        assert (dest2/'unmatched'/'Hidden.Film.2019.en.srt').is_file()
        # Identification after manually adding an NFO should move film out of unmatched.
        (dest2/'unmatched'/'Hidden.Film.2019.nfo').write_text(
            '<movie><title>Hidden Film</title><year>2019</year><genre>Drama</genre></movie>')
        app2.sources=[dest2]
        app2.source_list.delete(0,'end'); app2.source_list.insert('end', str(dest2))
        run_scan_and_sort(app2)
        assert not target.exists()
        assert (dest2/'Drama'/'2019'/'Hidden Film (2019)'/'Hidden Film (2019).mp4').is_file() or \
               (dest2/'Drama'/'2019'/'Hidden Film (2019).mp4').is_file(), app2.status.get()
        assert not list((dest2/'unmatched').glob('*.mp4'))
    app2.destroy(); gc.collect()
print('PASS: unmatched Copy + sidecars, matched sorting, repeat copy, optional routing, Move, and retry after NFO correction')
