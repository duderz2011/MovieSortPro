"""V3 regression suite. Never calls the network or touches a real movie library."""
import tempfile
import unittest
from datetime import date
from pathlib import Path
from movie_collections import collect, movie_order
from MovieSortPro import Movie, main_target_custom


PARTS = [
    {'id': 100, 'title': 'The Mighty Ducks', 'release_date': '1992-10-02'},
    {'id': 101, 'title': 'D2: The Mighty Ducks', 'release_date': '1994-03-25'},
    {'id': 102, 'title': 'D3: The Mighty Ducks', 'release_date': '1996-10-04'},
    {'id': 103, 'title': 'The Mighty Ducks: Future Film', 'release_date': '2099-01-01'},
]


def duck(title, year, tmdb_id, collection_id=1234, name='The Mighty Ducks Collection'):
    return Movie(Path(f'{title.replace(" ", ".")}.{year}.mkv'), title, year,
                 {'title': title, 'year': year, 'tmdb_id': tmdb_id,
                  'collection_id': collection_id, 'collection': name}, 'Matched: TMDB')


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.first = duck('The Mighty Ducks', 1992, 100)
        self.third = duck('D3: The Mighty Ducks', 1996, 102)
        self.today = date(2026, 10, 25)

    def test_missing_second_film_and_unreleased_sequel(self):
        collection = collect([self.first, self.third], 2, lambda cid: PARTS, self.today)[0]
        self.assertEqual(collection.count, 2)
        self.assertEqual([x['id'] for x in collection.missing], [101])
        self.assertEqual([x['id'] for x in collection.upcoming], [103])
        self.assertEqual(collection.catalogue_status, 'Checked against TMDB')
        self.assertEqual([m.title for m in sorted(collection.owned, key=lambda m: movie_order(m, collection))],
                         ['The Mighty Ducks', 'D3: The Mighty Ducks'])

    def test_scans_all_discovered_movies_even_outside_collection(self):
        second = Movie(Path('D2.The.Mighty.Ducks.1994.avi'), 'D2: The Mighty Ducks', 1994)
        collection = collect([self.first, self.third, second], 2, lambda cid: PARTS, self.today)[0]
        self.assertEqual(collection.missing, [])

    def test_deduplicate_formats_and_minimum_group_size(self):
        alternate = duck('The Mighty Ducks', 1992, 100)
        alternate.path = Path('The.Mighty.Ducks.1992.mp4')
        self.assertEqual(collect([self.first, alternate], 2), [])
        self.assertEqual(len(collect([self.first, self.third], 3)), 0)

    def test_noncollection_stays_normal_and_no_catalogue_never_means_complete(self):
        collection = collect([self.first, self.third], 2)[0]
        self.assertIn('Not checked', collection.catalogue_status)
        self.assertEqual(collection.missing, [])
        self.assertEqual(collection.ordered_parts, [])
        single = Movie(Path('Solo.2020.mkv'), 'Solo', 2020, {'genres': ['Adventure']}, 'Matched: NFO')
        self.assertEqual(len(collect([single], 2)), 0)
        self.assertEqual(main_target_custom(single, Path('Movies'), ['Genre', 'Year'], False, True),
                         Path('Movies/Adventure/2020/Solo (2020).mkv'))

    def test_mixed_catalogues_cannot_mix_conflicting_ids(self):
        other = duck('Different Movie', 1994, 200, collection_id=5555)
        self.assertEqual(collect([self.first, self.third, other], 2)[0].count, 2)

    def test_provider_failure_does_not_abandon_local_collection(self):
        def unavailable(_):
            raise RuntimeError('offline')
        collection = collect([self.first, self.third], 2, unavailable)[0]
        self.assertIn('unavailable', collection.catalogue_status)
        self.assertEqual(collection.missing, [])

    def test_series_order_overrides_when_supplied(self):
        self.third.metadata['collection_order'] = 1
        self.first.metadata['collection_order'] = 2
        collection = collect([self.first, self.third], 2, lambda _: PARTS, self.today)[0]
        self.assertEqual([m.title for m in sorted(collection.owned, key=lambda m: movie_order(m, collection))],
                         ['D3: The Mighty Ducks', 'The Mighty Ducks'])

    def test_invalid_or_unknown_release_is_not_declared_missing(self):
        things = PARTS + [{'id': 500, 'title': 'Date unconfirmed', 'release_date': ''}]
        collection = collect([self.first, self.third], 2, lambda _: things, self.today)[0]
        self.assertEqual(len(collection.missing), 1)
        self.assertEqual(len(collection.upcoming), 2)


if __name__ == '__main__':
    unittest.main()
