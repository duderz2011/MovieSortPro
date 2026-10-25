"""Run manually with xvfb-run on Linux or Python on Windows."""
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from MovieSortPro import App

with tempfile.TemporaryDirectory() as folder:
    source = Path(folder)/'input'; source.mkdir()
    output = Path(folder)/'output'; output.mkdir()
    movie=source/'The.Matrix.1999.mkv'; movie.write_bytes(b'Demo movie bytes')
    (source/'The.Matrix.1999.nfo').write_text('''<movie><title>The Matrix</title><year>1999</year><genre>Action</genre><studio>Warner Bros</studio><actor><name>Keanu Reeves</name></actor></movie>''',encoding='utf-8')
    (source/'The.Matrix.1999.en.srt').write_text('subtitle',encoding='utf-8')
    root=App()
    root.withdraw()
    root.sources=[source]
    root.source_list.insert('end',str(source))
    root.destination.set(str(output))
    for key in ('TMDB','OMDb','Wikidata','Local Catalogue'):
        root.source_enabled[key].set(False)
    root.update_source_summary()
    root.metadata_settings()
    root.update()
    root.winfo_children()[-1].destroy() if False else None
    # Shut metadata dialog by finding the Toplevel, since we are doing non-visual testing.
    for child in root.winfo_children():
        if child.winfo_class() == 'Toplevel': child.destroy()
    root.scan()
    until=time.monotonic()+15
    while root.busy and time.monotonic()<until:
        root.update(); time.sleep(0.02)
    root.update()
    assert not root.busy, 'scan hung'
    assert len(root.movies)==1, str(root.status.get())
    m = root.movies[0]
    assert m.match_state=='Matched: Local NFO', m.match_state
    assert m.target==output/'Action'/'1999'/'The Matrix (1999)'/'The Matrix (1999).mkv', m.target
    root.mode.set('Copy')
    with patch('MovieSortPro.messagebox.askyesno', return_value=True),patch('MovieSortPro.messagebox.showinfo'):
        root.organise()
        until=time.monotonic()+15
        while root.busy and time.monotonic()<until:
            root.update(); time.sleep(0.02)
        root.update()
    assert m.target.exists(), str(root.status.get())
    assert movie.exists(), 'copy deleted original'
    assert (m.target.parent/'The Matrix (1999).en.srt').exists(), 'subtitle not copied'
    assert (m.target.parent/'The Matrix (1999).nfo').exists(), 'nfo not copied'
    print('GUI smoke test passed: modal, scan, identify NFO, preview path, copy movie+NFO+subtitles')
    root.destroy()
