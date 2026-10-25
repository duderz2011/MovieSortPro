import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from metadata_sources import (get_local_nfo, LocalCatalogue, MetadataResolver,
                              OMDbProvider, WikidataProvider, compatible,
                              split_values, can_merge, merge_missing)
from MovieSortPro import (Movie, custom_target_parts, main_target_custom,
                         sidecars, companion_target)


class MetadataTests(unittest.TestCase):
    def test_nfo_file_movie(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            film = folder / 'The.Matrix.1999.mkv'
            film.write_bytes(b'not a video')
            (folder / 'The.Matrix.1999.nfo').write_text('''<?xml version="1.0"?>
                <movie><title>The Matrix</title><premiered>1999-03-31</premiered>
                <genre>Action</genre><genre>Sci-Fi</genre><studio>Warner Bros.</studio>
                <actor><name>Keanu Reeves</name></actor>
                <director>Lana Wachowski</director>
                <uniqueid type="imdb">tt0133093</uniqueid></movie>''', encoding='utf-8')
            md = get_local_nfo(film)
            self.assertEqual(md['year'], 1999)
            self.assertEqual(md['actors'], ['Keanu Reeves'])
            self.assertEqual(md['genres'], ['Action', 'Sci-Fi'])
            self.assertEqual(md['imdb_id'], 'tt0133093')

    def test_movie_nfo_only_in_single_video_folder(self):
        with tempfile.TemporaryDirectory() as root:
            film = Path(root)/'film.mkv'
            film.touch()
            (Path(root)/'movie.nfo').write_text('<movie><title>Film</title><year>2024</year></movie>')
            self.assertEqual(get_local_nfo(film)['title'], 'Film')
            (Path(root)/'extra.mkv').touch()
            self.assertEqual(get_local_nfo(film), {})

    def test_catalogue_csv_and_json(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root)
            csvfile = folder/'movies.csv'
            csvfile.write_text('filename,title,year,genre,studio,actors,director\nmovie.mkv,Sample Movie,2021,Action | Adventure,Film Corp,Person One; Person Two,Director X\n', encoding='utf-8')
            meta = LocalCatalogue(csvfile).lookup(folder/'movie.mkv', 'Sample Movie', 2021)
            self.assertEqual(meta['genres'], ['Action', 'Adventure'])
            self.assertEqual(meta['studios'], ['Film Corp'])
            jsonfile = folder/'movies.json'
            jsonfile.write_text(json.dumps({'movies': [{'title': 'Other Movie', 'year': 2019, 'genres': ['Comedy'], 'filename':'other.mkv'}]}))
            self.assertEqual(LocalCatalogue(jsonfile).lookup(folder/'other.mkv', 'Other Movie', 2019)['genres'], ['Comedy'])

    def test_omdb_parsing_and_matching(self):
        response = {'Response': 'True', 'Title': 'Sample Movie', 'Year': '2020',
                    'Genre':'Action, Drama', 'Actors':'Actor A, Actor B',
                    'Director':'Director A', 'Production':'Filmworks', 'imdbID': 'tt0123456'}
        with patch('metadata_sources.json_get', return_value=response) as fetch:
            md = OMDbProvider('not-a-real-key').lookup('Sample Movie', 2020)
            self.assertEqual(md['genres'], ['Action','Drama'])
            self.assertEqual(md['studios'], ['Filmworks'])
            self.assertIn('www.omdbapi.com', fetch.call_args[0][0])
            self.assertIn('apikey=not-a-real-key', fetch.call_args[0][0])
            self.assertEqual(OMDbProvider('not-a-real-key').lookup('Wrong', 2020), {})

    def test_priority_fallback_and_merge(self):
        class FakeTMDb:
            def __init__(self, token):
                self.token = token
            def lookup(self, title, year):
                return {'title':title, 'year':year, 'genres':['Action'], 'tmdb_id':'12'}, 'Matched'
            def close(self): pass
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'Movie.2024.mkv'
            path.touch()
            (Path(root) / 'Movie.2024.nfo').write_text('<movie><title>Movie</title><year>2024</year><studio>Film Studio</studio></movie>')
            with patch.object(OMDbProvider, 'lookup', return_value={'title':'Movie','year':2024,'actors':['Lead Actor']}):
                enabled={'Local NFO':True,'TMDB':True, 'OMDb': True}
                resolver=MetadataResolver(['Local NFO','TMDB','OMDb'],enabled,tmdb_token='test',omdb_key='test',merge=True,tmdb_factory=FakeTMDb)
                md,state = resolver.lookup(path,'Movie',2024)
                self.assertIn('Local NFO', state)
                self.assertIn('OMDb',state)
                self.assertEqual(md['studios'], ['Film Studio'])
                self.assertEqual(md['genres'], ['Action'])
                self.assertEqual(md['actors'], ['Lead Actor'])
                resolver.close()

    def test_wikidata_ambiguity_never_assumes(self):
        provider = WikidataProvider()
        response = [
            {'search':[{'id':'Q1','label':'Film'},{'id':'Q2','label':'Film'}]},
            {'entities':{'Q1':{'claims':{'P31':[{'mainsnak': {'datavalue': {'value': {'id':'Q11424'}}}}]}},
                         'Q2':{'claims':{'P31':[{'mainsnak': {'datavalue': {'value': {'id':'Q11424'}}}}]}}}}
        ]
        with patch.object(provider,'request',side_effect=response):
            self.assertEqual(provider.lookup('Film'),{})

    def test_custom_sort_and_actor(self):
        md={'title':'Sample Movie','year':2023,'genres':['Action'],'actors':['Person A', 'Person B'],
            'directors':['Director X'],'studios':['Studio Y']}
        movie = Movie(Path('sample.2023.mkv'), 'Sample Movie', 2023, md, 'Matched: Local NFO')
        self.assertEqual(custom_target_parts(movie,['Genre','Year','Movie Title']),['Action','2023','Sample Movie (2023)'])
        self.assertEqual(custom_target_parts(movie,['Selected Actor','Studio'], 'Person B'), ['Person B','Studio Y'])
        destination = main_target_custom(movie,Path('D:/Movies'),['Genre','Year','Movie Title'],True,True)
        self.assertEqual(destination,Path('D:/Movies/Action/2023/Sample Movie (2023)/Sample Movie (2023).mkv'))

    def test_move_single_folder_artwork_only_when_safe(self):
        with tempfile.TemporaryDirectory() as root:
            folder=Path(root)
            video=folder/'Film.mkv'; video.touch()
            (folder/'movie.nfo').touch()
            (folder/'poster.jpg').touch()
            self.assertEqual({p.name for p in sidecars(video)}, {'movie.nfo','poster.jpg'})
            self.assertEqual(companion_target(folder/'movie.nfo', video, folder/'Film (2024)'/'Film (2024).mkv', True), folder/'Film (2024)'/'movie.nfo')
            (folder/'Another.mkv').touch()
            self.assertEqual(sidecars(video), [])

    def test_compatible_guardrails(self):
        self.assertFalse(compatible({'title':'Wrong Film','year':2020},'Correct Film',2020))
        self.assertFalse(compatible({'title':'Correct Film','year':1995},'Correct Film',2020))
        self.assertTrue(can_merge({'title':'Correct Film', 'imdb_id':'tt0123456'},{'title':'Correct Film','imdb_id':'tt0123456'}))
        self.assertEqual(merge_missing({'genres':['Action']},{'genres':['Drama'],'studios':['Studio']})['genres'],['Action'])
        self.assertEqual(split_values(['Action / Adventure','Drama']),['Action','Adventure','Drama'])

if __name__=='__main__':
    unittest.main()
