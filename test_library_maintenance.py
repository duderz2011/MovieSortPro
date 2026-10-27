import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

from library_maintenance import (no_video_folders, prune_empty_tree, safe_to_recycle,
    VideoQuality, lower_quality_duplicates, clearly_lower)
from MovieSortPro import VIDEO_EXTENSIONS


class TestScanCleanup(unittest.TestCase):
    def test_scan_identifies_empty_and_nonmovie_folders_but_protects_video_ancestors(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source'; root.mkdir()
            (root/'with-video'/'deep').mkdir(parents=True)
            (root/'with-video'/'deep'/'a.mkv').write_bytes(b'video')
            (root/'unused'/'empty').mkdir(parents=True)
            (root/'misc').mkdir()
            (root/'misc'/'important.txt').write_text('KEEP ME')
            dest=root/'organised';dest.mkdir()
            candidates=no_video_folders([root],dest,VIDEO_EXTENSIONS)
            by_name={x.path.name:x for x in candidates}
            self.assertEqual(set(by_name),{'unused','misc'})
            self.assertFalse(by_name['unused'].contains_other_files)
            self.assertTrue(by_name['misc'].contains_other_files)
            removed=sum(prune_empty_tree(x.path,[root],dest) for x in candidates)
            self.assertEqual(removed,2)
            self.assertFalse((root/'unused').exists())
            self.assertTrue((root/'misc'/'important.txt').exists())
            self.assertTrue((root/'with-video'/'deep'/'a.mkv').exists())
            self.assertTrue(root.exists())
            self.assertTrue(dest.exists())

    def test_candidate_has_video_in_descendant_is_never_recycled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';(root/'a'/'b').mkdir(parents=True)
            (root/'a'/'b'/'film.avi').touch()
            self.assertFalse(safe_to_recycle(root/'a',[root],None,VIDEO_EXTENSIONS))
            self.assertFalse(safe_to_recycle(root,[root],None,VIDEO_EXTENSIONS))

    def test_symlink_and_nested_source_roots_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'root';secondary=root/'nested'/'selected';secondary.mkdir(parents=True)
            (root/'nested'/'empty').mkdir()
            self.assertFalse(safe_to_recycle(root/'nested',[root,secondary],None,VIDEO_EXTENSIONS))
            self.assertEqual(prune_empty_tree(root/'nested',[root,secondary]),0)
            self.assertTrue(secondary.exists())
            try:
                (root/'link').symlink_to(secondary, target_is_directory=True)
            except OSError:
                return
            self.assertFalse(safe_to_recycle(root/'link',[root],None,VIDEO_EXTENSIONS))

    def test_destination_nested_under_candidate_never_recursively_pruned_or_recycled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'root';dest=root/'unused'/'output';dest.mkdir(parents=True)
            self.assertEqual(prune_empty_tree(root/'unused',[root],dest),0)
            self.assertFalse(safe_to_recycle(root/'unused',[root],dest,VIDEO_EXTENSIONS))
            self.assertTrue(dest.is_dir())

    def test_nonmovie_folder_is_never_deleted_during_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'src';root.mkdir();folder=root/'text';folder.mkdir();(folder/'important.txt').write_text('a')
            folders=no_video_folders([root],None,VIDEO_EXTENSIONS)
            for x in folders:
                prune_empty_tree(x.path,[root])
            self.assertEqual((folder/'important.txt').read_text(),'a')


class TestDuplicates(unittest.TestCase):
    def movie(self, path, title, year, identifier, edition=''):
        path.write_bytes(b'placeholder')
        return SimpleNamespace(path=path, match_state='Matched: TMDB', metadata={
            'title':title, 'year':year,'tmdb_id':identifier})

    def test_actual_quality_detected_same_film(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            a=self.movie(root/'Film (2020) 480p.mkv','Film',2020,12)
            b=self.movie(root/'Film (2020) 1080p.mkv','Film',2020,12)
            qa=VideoQuality(640,480,'h264',700000,'bt709')
            qb=VideoQuality(1920,1080,'h264',3200000,'bt709')
            found=lower_quality_duplicates([a,b],probe=lambda x:qa if x==a.path else qb)
            self.assertEqual([(x[0],x[1]) for x in found],[(a.path,b.path)])

    def test_different_film_id_and_cuts_are_never_suggested(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            a=self.movie(root/'Film theatrical 480p.mkv','Film',2020,12)
            b=self.movie(root/'Film extended 1080p.mkv','Film',2020,12)
            c=self.movie(root/'Film same name 1080p.mkv','Film',2020,99)
            qa=VideoQuality(640,480,'h264',700000,'bt709')
            qb=VideoQuality(1920,1080,'h264',3200000,'bt709')
            self.assertFalse(lower_quality_duplicates([a,b,c],probe=lambda x:qa if x==a.path else qb))

    def test_hdr_sdr_and_unknown_codec_quality_not_automatically_ranked(self):
        self.assertFalse(clearly_lower(VideoQuality(640,480,'h264',300000,'bt709'),
                                      VideoQuality(1920,1080,'h264',3000000,'smpte2084')))
        self.assertFalse(clearly_lower(None,VideoQuality(1920,1080,'h264',4000000,'bt709')))
        self.assertFalse(clearly_lower(VideoQuality(1920,1080,'h265',1000000,'bt709'),
                                      VideoQuality(1920,1080,'h264',5000000,'bt709')))


if __name__=='__main__': unittest.main()
