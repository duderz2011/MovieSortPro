"""Regression checks for the unmatched-film routing feature."""
from pathlib import Path
import unittest
from MovieSortPro import Movie, confidently_identified, unmatched_target, choose_target


class UnmatchedRoutingTests(unittest.TestCase):
    def test_no_confident_match_or_outage_is_unmatched(self):
        for state in ("Filename only", "Needs review: no confident match",
                      "Needs review: ambiguous match", "Lookup failed / needs review"):
            movie = Movie(Path("Mystery.Scene.2021.mkv"), "Mystery Scene", 2021, {}, state)
            self.assertFalse(confidently_identified(movie), state)
            self.assertEqual(unmatched_target(movie, Path("D:/Movies")),
                             Path("D:/Movies/unmatched/Mystery.Scene.2021.mkv"))

    def test_metadata_match_requires_title_and_confirmed_state(self):
        movie = Movie(Path("Film.mkv"), "Film", 2021, {"title": "Film", "genres": ["Action"]}, "Matched: TMDB")
        self.assertTrue(confidently_identified(movie))
        movie.match_state = "Needs review: ambiguous match"
        self.assertFalse(confidently_identified(movie))
        movie.match_state = "Matched: Local NFO"
        movie.metadata = {}
        self.assertFalse(confidently_identified(movie))

    def test_existing_unmatched_file_is_skipped(self):
        p = Path("D:/Movies/unmatched/Mystery.mkv")
        self.assertIsNone(choose_target(p, p, set())[0])


if __name__ == '__main__':
    unittest.main()
