"""Regression tests for repeated scans and sorting already-organised libraries.

Run: xvfb-run -a python test_reruns_gui.py (Linux). On Windows: python test_reruns_gui.py
"""
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from MovieSortPro import App


def wait_for(app, seconds=10):
    cutoff = time.monotonic() + seconds
    while app.busy and time.monotonic() < cutoff:
        app.update(); time.sleep(.02)
    app.update()
    assert not app.busy, 'Timed out: '+app.status.get()


def setup_app(source, dest):
    app=App(); app.withdraw()
    app.sources=[source]
    app.source_list.delete(0,'end'); app.source_list.insert('end',str(source))
    app.destination.set(str(dest))
    for name in ('TMDB', 'OMDb', 'Wikidata', 'Local Catalogue'):
        app.source_enabled[name].set(False)
    return app


def demo_movie(root):
    movie=root/'The.Matrix.1999.mkv';movie.write_bytes(b'test-video-file-2026')
    (root/'The.Matrix.1999.nfo').write_text('<movie><title>The Matrix</title><year>1999</year><genre>Action</genre><studio>Warner Bros</studio><actor><name>Keanu Reeves</name></actor></movie>',encoding='utf-8')
    return movie

with tempfile.TemporaryDirectory() as d:
    os.environ['LOCALAPPDATA']=str(Path(d)/'LocalAppData')
    root=Path(d)
    src=root/'inputs';src.mkdir()
    dst=root/'library';dst.mkdir()
    source_movie=demo_movie(src)
    app=setup_app(src,dst)
    app.mode.set('Move')
    with patch('MovieSortPro.messagebox.askyesno',return_value=True),patch('MovieSortPro.messagebox.showinfo'):
        app.scan();wait_for(app)
        assert len(app.movies)==1
        app.organise();wait_for(app)
        organised=dst/'Action'/'1999'/'The Matrix (1999)'/'The Matrix (1999).mkv'
        assert organised.exists(), organized if False else 'Missing moved movie'
        assert not source_movie.exists()
        assert app.go_btn.instate(['disabled']), 'Should disable stale organize after Move'
        # Repeating with an emptied original source should give clear explanation.
        app.scan();wait_for(app)
        assert len(app.movies)==0
        assert 'MOVE' in app.status.get()
        # Destination as source and destination (in-place re-sort) should scan.
        app.sources=[dst];app.source_list.delete(0,'end');app.source_list.insert('end',str(dst))
        app.scan();wait_for(app)
        assert len(app.movies)==1, app.status.get()
        assert app.movies[0].path==organised
        app.organise();wait_for(app)
        assert '1 already present/skipped' in app.status.get(),app.status.get()
        assert organised.exists()
        assert not list(dst.rglob('*[2]*.mkv'))
        # Choose a new destination folder scheme, re-sort in-place.
        app.levels[0].set('Year');app.levels[1].set('Genre')
        app.refresh_preview()
        app.organise();wait_for(app)
        reorganised=dst/'1999'/'Action'/'The Matrix (1999)'/'The Matrix (1999).mkv'
        assert reorganised.exists(),app.status.get()
        assert not organised.exists()
        app.save_preferences()
        app.destroy()
    # Relaunch should preserve folder selection and sorting rules.
    reloaded=App();reloaded.withdraw()
    assert reloaded.sources==[dst],reloaded.sources
    assert Path(reloaded.destination.get())==dst
    assert [x.get() for x in reloaded.levels[:2]]==['Year','Genre']
    assert reloaded.mode.get()=='Move'
    reloaded.destroy()

with tempfile.TemporaryDirectory() as d:
    os.environ['LOCALAPPDATA']=str(Path(d)/'LocalAppData')
    root=Path(d)
    src=root/'movies';src.mkdir()
    dst=src/'sorted';dst.mkdir()
    orig=demo_movie(src)
    app=setup_app(src,dst)
    app.mode.set('Copy')
    with patch('MovieSortPro.messagebox.askyesno',return_value=True),patch('MovieSortPro.messagebox.showinfo'):
        app.scan();wait_for(app)
        assert len(app.movies)==1
        app.organise();wait_for(app)
        first=list(dst.rglob('*.mkv'));assert len(first)==1
        app.scan();wait_for(app)
        assert len(app.movies)==1,'Output subtree was included on next scan'
        app.organise();wait_for(app)
        assert '1 already present/skipped' in app.status.get(), app.status.get()
        assert len(list(dst.rglob('*.mkv')))==1, 'Rerun created a duplicate'
        assert orig.exists(), 'Copy should preserve the original'
    app.destroy()

print('PASS: Move → empty source → in-place sort → changed sorting scheme → saved settings; Copy → repeated scan without duplicates')
