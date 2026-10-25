"""Run with xvfb-run -a python gui_collections_test.py on Linux."""
import gc
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from MovieSortPro import App

PARTS = [
    {'id': 11, 'title': 'The Mighty Ducks', 'release_date': '1992-10-02'},
    {'id': 12, 'title': 'D2: The Mighty Ducks', 'release_date': '1994-03-25'},
    {'id': 13, 'title': 'D3: The Mighty Ducks', 'release_date': '1996-10-04'},
]

with tempfile.TemporaryDirectory() as work:
    os.environ['LOCALAPPDATA'] = str(Path(work) / 'settings')
    source, dest = Path(work) / 'source', Path(work) / 'output'
    source.mkdir(); dest.mkdir()
    for name, title, year, id in [
        ('The.Mighty.Ducks.1992', 'The Mighty Ducks', 1992, 11),
        ('D3.The.Mighty.Ducks.1996', 'D3: The Mighty Ducks', 1996, 13),
    ]:
        (source / (name + '.mkv')).write_bytes(name.encode())
        (source / (name + '.nfo')).write_text(
            f'<movie><title>{title}</title><year>{year}</year><set><name>The Mighty Ducks Collection</name><tmdbid>444</tmdbid></set><uniqueid type="tmdb">{id}</uniqueid><genre>Family</genre></movie>',
            encoding='utf-8')
    app = App(); app.withdraw()
    app.sources = [source]; app.destination.set(str(dest))
    app.source_list.insert('end', str(source))
    app.token.set('offline-test-token')
    for name in ('OMDb', 'Wikidata', 'Local Catalogue'):
        app.source_enabled[name].set(False)
    with patch('MovieSortPro.TMDb.collection_parts', return_value=PARTS), \
         patch('MovieSortPro.messagebox.askyesno', return_value=True), \
         patch('MovieSortPro.messagebox.showinfo'):
        app.scan()
        deadline = time.monotonic() + 16
        while app.busy and time.monotonic() < deadline:
            app.update(); time.sleep(0.015)
        app.update()
        assert not app.busy, app.status.get()
        assert len(app.collection_groups) == 1, app.status.get()
        assert [p['id'] for p in app.collection_groups[0].missing] == [12]
        assert all('/Collections/The Mighty Ducks Collection/' in str(m.target).replace('\\','/') for m in app.movies)
        app.show_collections()
        app.update()
        from tkinter import Toplevel
        windows = [w for w in app.winfo_children() if isinstance(w, Toplevel)]
        assert windows, 'Collections dashboard did not open'
        windows[-1].destroy()
        app.mode.set('Copy')
        app.organise()
        deadline = time.monotonic() + 16
        while app.busy and time.monotonic() < deadline:
            app.update(); time.sleep(0.015)
        app.update()
        assert not app.busy, app.status.get()
        assert len(list((dest/'Collections'/'The Mighty Ducks Collection').glob('*.mkv'))) == 2
        assert len(list(source.glob('*.mkv'))) == 2
    app.destroy(); gc.collect()
print('PASS: V3 scan, NFO metadata, missing film detection, collection dashboard, safe Copy to Collections folder')
