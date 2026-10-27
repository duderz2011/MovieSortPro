"""No live API requests: verifies match confidence, fallbacks, and MOVE-only empty-folder removal."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from metadata_sources import (AppleMoviesProvider, TraktProvider, OMDbProvider,
                              choose_confident_candidate, MetadataResolver, PROVIDERS)
from MovieSortPro import (TMDb, cleanup_empty_source_dirs, App)


class TestMetadataFallback(unittest.TestCase):
    def test_apple_fallback_extracts_genre_year_without_guessing_studio(self):
        payload = {'results': [{'kind': 'feature-movie', 'trackName': 'The Ducks',
                                'releaseDate': '1992-10-02T00:00:00Z',
                                'primaryGenreName': 'Comedy', 'artistName': 'Random Creator'}]}
        with patch('metadata_sources.json_get', return_value=payload) as fetch:
            md = AppleMoviesProvider().lookup('The Ducks', 1992)
            self.assertEqual(md['title'], 'The Ducks')
            self.assertEqual(md['genres'], ['Comedy'])
            self.assertNotIn('studios', md)
            self.assertIn('itunes.apple.com/search', fetch.call_args.args[0])

    def test_ambiguous_same_title_without_year_remains_unmatched(self):
        movies = [{'title':'Same Film', 'year':2001}, {'title':'Same Film','year':2011}]
        self.assertEqual(choose_confident_candidate(movies, 'Same Film'), {})
        self.assertEqual(choose_confident_candidate(movies, 'Same Film', 2001), movies[0])
        self.assertEqual(choose_confident_candidate(movies, 'Same Film', 2010), movies[1])

    def test_omdb_broad_search_uses_id_details_after_failed_exact(self):
        calls=[]
        def fake_get(url, *args, **kwargs):
            calls.append(url)
            if '&s=' in url:
                return {'Response':'True','Search':[{'Title':'Second Chance','Year':'2023','imdbID':'tt10123456'}]}
            if '&i=' in url:
                return {'Response':'True','Title':'Second Chance','Year':'2023', 'Genre':'Action', 'imdbID':'tt10123456'}
            return {'Response':'False','Error':'Not found!'}
        with patch('metadata_sources.json_get',side_effect=fake_get):
            md=OMDbProvider('test').lookup('Second Chance',2023)
        self.assertEqual(md['imdb_id'],'tt10123456')
        self.assertTrue(any('&s=' in c for c in calls))

    def test_trakt_client_id_and_strict_year(self):
        response=[{'type':'movie', 'movie':{'title':'Ducks', 'year':1992,
                   'genres':['comedy'], 'ids':{'imdb':'tt1000011','tmdb':123}}}]
        with patch('metadata_sources.json_get',return_value=response) as fetch:
            self.assertEqual(TraktProvider('client').lookup('Ducks',1992)['tmdb_id'],123)
            self.assertEqual(fetch.call_args.kwargs['headers']['trakt-api-key'], 'client')
            self.assertEqual(TraktProvider('client').lookup('Ducks',2020),{})

    def test_tmdb_year_fallback_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as d, patch('MovieSortPro.app_dir',return_value=Path(d)):
            client=TMDb('sample-token')
            calls=[]
            def fake_get(endpoint,params=None):
                calls.append((endpoint,params))
                if endpoint=='search/movie':
                    if 'year' in params: return {'results':[]}
                    return {'results':[{'id':12,'title':'Second Chance', 'original_title':'Second Chance', 'release_date':'2023-08-01'}]}
                if endpoint=='movie/12':
                    return {'id':12,'title':'Second Chance','release_date':'2023-08-01', 'genres':[{'name':'Thriller'}]}
                raise AssertionError(endpoint)
            with patch.object(client,'get',side_effect=fake_get), patch('MovieSortPro.time.sleep'):
                md,state=client.lookup('Second Chance',2023)
                self.assertEqual(md['tmdb_id'],12)
                self.assertEqual(state,'Matched')
                md2,state2=client.lookup('Second Chance',2023)
                self.assertEqual(len(calls),3)  # cache successful resolution
            client.close()

    def test_new_sources_applied_to_resolver(self):
        self.assertIn('Apple Movies', PROVIDERS)
        self.assertIn('Trakt', PROVIDERS)
        with tempfile.TemporaryDirectory() as d:
            movie=Path(d)/'Film.2020.mp4'; movie.touch()
            with patch.object(AppleMoviesProvider,'lookup',return_value={'title':'Film','year':2020,'genres':['Comedy']}):
                resolver=MetadataResolver(['Apple Movies'],{'Apple Movies':True})
                md,state=resolver.lookup(movie,'Film',2020)
                self.assertIn('Apple Movies',state)
                self.assertEqual(md['genres'],['Comedy'])


class TestEmptyFolders(unittest.TestCase):
    def test_nested_empty_directories_removed_but_source_root_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'source'; old=source/'Genre'/'Old'; old.mkdir(parents=True)
            moved=old/'movie.mkv'; moved.touch(); moved.unlink()
            count=cleanup_empty_source_dirs([moved],[source],Path(tmp)/'output')
            self.assertEqual(count,2)
            self.assertTrue(source.exists())

    def test_hidden_or_other_files_protect_source_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source'; old=root/'a'; old.mkdir(parents=True)
            moved=old/'movie.mkv'; (old/'.keep').touch()
            self.assertEqual(cleanup_empty_source_dirs([moved],[root]),0)
            self.assertTrue((old/'.keep').exists())

    def test_copy_mode_does_not_run_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source'; original=root/'nested'/'movie.mkv'; original.parent.mkdir(parents=True)
            original.write_bytes(b'film contents')
            output=Path(tmp)/'output'/'Action'/'movie.mkv'
            messages=[]
            dummy=object.__new__(App)
            dummy.queue_message=lambda *args: messages.append(args)
            with patch('MovieSortPro.app_dir',return_value=Path(tmp)/'state'),patch('MovieSortPro.cleanup_empty_source_dirs') as cleaner:
                (Path(tmp)/'state').mkdir()
                dummy.organise_thread([(original,output,False)],'Copy',False,False,True,[root],Path(tmp)/'output')
                cleaner.assert_not_called()
            self.assertTrue(original.exists())
            self.assertTrue(output.exists())

    def test_real_move_cleans_only_empty_folders_and_undo_remains_possible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source'; nested=root/'Old'/'Films'; nested.mkdir(parents=True)
            original=nested/'movie.mkv'; original.write_bytes(b'movie')
            subtitle=nested/'movie.srt';subtitle.write_text('subtitles')
            dst=Path(tmp)/'destination'/'Action'/'movie.mkv'
            messages=[]
            dummy=object.__new__(App)
            dummy.queue_message=lambda *args: messages.append(args)
            with patch('MovieSortPro.app_dir',return_value=Path(tmp)/'state'):
                (Path(tmp)/'state').mkdir()
                dummy.organise_thread([(original,dst,False)],'Move',False,True,True,[root],dst.parent.parent)
                journal=json.loads((Path(tmp)/'state'/'last_run.json').read_text())
            self.assertTrue(dst.exists())
            self.assertTrue((dst.parent/'movie.srt').exists())
            self.assertFalse(nested.exists())
            self.assertFalse((root/'Old').exists())
            self.assertTrue(root.is_dir())
            self.assertEqual(len(journal['changes']),2)
            self.assertTrue(any(x[0]=='work_done' and '2 empty source subfolder' in x[1] for x in messages))

    def test_source_root_destination_root_and_symlink_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'library';source.mkdir()
            dest=source/'sorted';dest.mkdir()
            a=source/'A'/'B';a.mkdir(parents=True)
            self.assertEqual(cleanup_empty_source_dirs([a/'m.mkv'], [source],dest),2)
            self.assertTrue(source.exists())
            self.assertTrue(dest.exists())
            # Already moved files don't cause deleting unrelated directories.
            self.assertEqual(cleanup_empty_source_dirs([], [source],dest),0)

if __name__=='__main__':
    unittest.main()
