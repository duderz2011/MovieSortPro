import tempfile
import unittest
from pathlib import Path
from MovieSortPro import Movie, filename_title_year, first_letter, safe_name, main_target, target_parts, sidecars, choose_target

class MovieSortTests(unittest.TestCase):
    def test_scene_filename(self):
        self.assertEqual(filename_title_year(Path('The.Matrix.1999.1080p.BluRay.x265.mkv')), ('The Matrix', 1999))

    def test_numeric_title(self):
        self.assertEqual(filename_title_year(Path('1917.2019.1080p.mkv')), ('1917', 2019))
        self.assertEqual(filename_title_year(Path('1984.mkv')), ('1984', None))

    def test_articles_and_alphabet(self):
        self.assertEqual(first_letter('The Matrix'), 'M')
        self.assertEqual(first_letter('A Beautiful Mind'), 'B')
        self.assertEqual(first_letter('2001: A Space Odyssey'), '#')

    def test_windows_sanitize(self):
        self.assertEqual(safe_name('CON'), '_CON')
        self.assertEqual(safe_name('Title: Part / One'), 'Title_ Part _ One')

    def test_presets(self):
        m = Movie(Path('example.mkv'), 'Example', 2024, {'title':'Example', 'year':2024,'genres':['Action'], 'studios':['Warner Bros.'],'actors':['A Famous Actor']}, 'Matched')
        self.assertEqual(target_parts(m, 'Genre / Year'), ['Action', '2024'])
        self.assertEqual(target_parts(m, 'A-P / Q-Z'), ['A-P'])
        self.assertEqual(main_target(m, Path('M:/Movies'), 'Genre / Year', True, True), Path('M:/Movies/Action/2024/Example (2024)/Example (2024).mkv'))

    def test_collision_and_sidecars(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            movie = folder/'Test.2024.mkv'
            movie.write_bytes(b'abc')
            srt = folder/'Test.2024.en.srt'
            srt.write_text('hello')
            self.assertEqual(sidecars(movie), [srt])
            destination = folder/'organized'/'Test.mkv'
            picked, msg = choose_target(movie, destination, set())
            self.assertEqual(picked, destination)
            destination.parent.mkdir()
            destination.write_bytes(b'abc')
            picked, msg = choose_target(movie, destination, set())
            self.assertIsNone(picked)
            self.assertIn('Identical', msg)
            destination.write_bytes(b'different')
            picked, msg = choose_target(movie, destination, set())
            self.assertEqual(picked.name, 'Test [2].mkv')

if __name__ == '__main__': unittest.main()
