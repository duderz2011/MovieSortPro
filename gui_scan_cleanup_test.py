"""GUI smoke test for scan-time empty-folder deletion and no-film review."""
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import patch
from MovieSortPro import App

with tempfile.TemporaryDirectory() as temp:
    base=Path(temp)
    os.environ['LOCALAPPDATA']=str(base/'appdata')
    src=base/'source';src.mkdir()
    dest=src/'output';dest.mkdir()
    (src/'empty'/'nested').mkdir(parents=True)
    nonvideo=src/'receipts';nonvideo.mkdir()
    (nonvideo/'must-keep.pdf').write_bytes(b'Not a movie')
    (src/'Video').mkdir()
    (src/'Video'/'Film.2021.mp4').write_bytes(b'movie')
    app=App();app.withdraw()
    app.sources=[src]
    app.destination.set(str(dest))
    app.scan_empty_folders.set(True)
    for key in app.source_enabled:
        app.source_enabled[key].set(key=='Local NFO')
    with patch('MovieSortPro.messagebox.showinfo'), patch('MovieSortPro.messagebox.showwarning'):
        app.scan()
        deadline=time.monotonic()+20
        while app.busy and time.monotonic()<deadline:
            app.update();time.sleep(.015)
        app.update()
    assert not app.busy
    assert not (src/'empty').exists(), 'Scan should prune entirely empty trees'
    assert (nonvideo/'must-keep.pdf').read_bytes()==b'Not a movie', 'Must never remove user documents during scan'
    assert len(app.no_video_candidates)==1, app.no_video_candidates
    assert app.no_video_candidates[0].path==nonvideo
    assert (src/'Video'/'Film.2021.mp4').exists()
    assert dest.is_dir()
    app.destroy()
print('PASS: auto-prune empty folders; non-movie files review only; video and destination safe')
