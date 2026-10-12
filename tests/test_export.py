"""Regression tests for complete, correctly attributed publication exports."""

from contextlib import contextmanager
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

import bibtexparser
import pandas as pd
import requests

from doi2bib import doi2bib
from author import Author
from figshare_api import FigShare
from figshare_fetch import fetch_publications
from figshare_bibtex import build_bibliography
from generate_stats import generate_statistics


@contextmanager
def working_directory():
    previous = os.getcwd()
    with tempfile.TemporaryDirectory() as directory:
        os.chdir(directory)
        try:
            yield Path(directory)
        finally:
            os.chdir(previous)


def response(status=200, payload=None):
    result = Mock(status_code=status)
    result.json.return_value = payload
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(f'HTTP {status}')
    return result


class ApiTests(unittest.TestCase):
    def test_forbidden_fails_immediately_instead_of_appearing_empty(self):
        with working_directory(), patch('figshare_api.post', return_value=response(403)) as post:
            fs = FigShare(rate_limit_delay=0, max_retries=30)
            with self.assertRaises(requests.HTTPError):
                fs.articles_by_author('James Brown')
            self.assertEqual(post.call_count, 1)

    def test_temporary_failures_retry_but_are_bounded(self):
        with working_directory(), patch('figshare_api.time.sleep') as sleep:
            with patch('figshare_api.post', side_effect=[response(503), response(200, [])]) as post:
                fs = FigShare(rate_limit_delay=0, max_retries=3)
                self.assertEqual(fs.articles_by_author('James Brown'), [])
                self.assertEqual(post.call_count, 2)
                sleep.assert_called_once_with(1)
            with patch('figshare_api.post', return_value=response(503)) as post:
                with self.assertRaises(requests.HTTPError):
                    fs.articles_by_author('James Brown', use_cache=False)
                self.assertEqual(post.call_count, 3)

    def test_network_timeout_is_retried(self):
        with working_directory(), patch('figshare_api.time.sleep'):
            with patch('figshare_api.post', side_effect=[requests.Timeout(), response(200, [])]):
                fs = FigShare(rate_limit_delay=0, max_retries=2)
                self.assertEqual(fs.articles_by_author('James Brown'), [])

    def test_changed_article_revision_refreshes_cached_details(self):
        with working_directory(), patch('figshare_api.get', side_effect=[response(200, {'title': 'old'}), response(200, {'title': 'new'})]) as get:
            fs = FigShare(rate_limit_delay=0)
            self.assertEqual(fs.get_article(1, revision='a')['title'], 'old')
            self.assertEqual(fs.get_article(1, revision='a')['title'], 'old')
            self.assertEqual(fs.get_article(1, revision='b')['title'], 'new')
            self.assertEqual(get.call_count, 2)

    def test_invalid_response_does_not_become_an_empty_page(self):
        with working_directory(), patch('figshare_api.post', return_value=response(200, {})):
            with self.assertRaises(RuntimeError):
                FigShare(rate_limit_delay=0).articles_by_author('James Brown')


class ExportTests(unittest.TestCase):
    def test_shared_publication_is_counted_once_with_both_group_authors(self):
        summary = {'id': 42, 'title': 'Shared paper', 'modified_date': '2026-01-01',
                   'url_public_html': 'https://repository.lincoln.ac.uk/articles/42',
                   'timeline': {'firstOnline': '2025-12-01'}}
        detail = {'authors': [{'full_name': 'James Brown', 'orcid_id': '123'},
                              {'full_name': 'Wenting Duan'}],
                  'custom_fields': [{'name': 'External DOI', 'value': 'https://doi.org/10.1234/example'}]}
        other = {**summary, 'id': 43}
        fs = Mock()
        fs.articles_by_author.side_effect = lambda *args, **kwargs: copy.deepcopy([summary, other])
        fs.get_article.side_effect = lambda article_id, **kwargs: copy.deepcopy(detail if article_id == 42 else {'authors': [{'full_name': 'James Brown', 'orcid_id': 'wrong'}]})
        config = {'filters': {'institution_id': 1068}, 'authors': [
            {'name': 'James Brown', 'orcid': '123'}, {'name': 'Wenting Duan'}]}
        with working_directory():
            all_articles, unique = fetch_publications(config, fs)
        self.assertEqual(len(all_articles), 2)
        self.assertEqual(len(unique), 1)
        self.assertEqual(unique.iloc[0]['authors'], 'James Brown, Wenting Duan')
        self.assertEqual(unique.iloc[0]['External DOI'], '10.1234/example')
        self.assertEqual(unique.iloc[0]['online_year'], 2025)

    def test_identifier_overrides_name_and_missing_orcid_allows_name_match(self):
        with working_directory():
            author = Author('James Brown', orcid='123')
            author.articles = [{'details': {'authors': [candidate]}} for candidate in [
                {'full_name': 'James Brown', 'orcid_id': 'wrong'},
                {'full_name': 'J Brown', 'orcid_id': '123'},
                {'full_name': 'James Brown', 'orcid_id': ''}]]
            author._remove_other_authors()
            self.assertEqual(len(author.articles), 2)

    def test_missing_dates_remain_undated(self):
        with working_directory():
            author = Author('James Brown')
            author.articles = [{'id': 1, 'details/resource_doi': '10.1234/paper'}]
            author._create_dataframe()
            self.assertTrue(pd.isna(author.df.iloc[0]['online_year']))
            self.assertEqual(author.df.iloc[0]['External DOI'], '10.1234/paper')

    def test_missing_input_exits_with_failure(self):
        script = Path(__file__).resolve().parents[1] / 'figshare_bibtex.py'
        with working_directory():
            result = subprocess.run([sys.executable, str(script), '-i', 'missing.csv'], capture_output=True)
            self.assertNotEqual(result.returncode, 0)

    def test_bibtex_retains_missing_and_failed_dois_and_full_author_list(self):
        df = pd.DataFrame([
            {'id': 1, 'title': 'Undated manuscript', 'details/authors': "[{'full_name': 'James Brown'}, {'full_name': 'Other Author'}]"},
            {'id': 2, 'title': 'Published paper', 'External DOI': '10.1234/example', 'online_year': 2025},
            {'id': 3, 'title': 'Same deposited paper', 'External DOI': '10.1234/example', 'online_year': 2025}])
        resolver = Mock()
        resolver.get_bibtex_entry.side_effect = requests.Timeout()
        database = build_bibliography(df, resolver)
        parsed = bibtexparser.loads(bibtexparser.dumps(database))
        self.assertEqual(len(parsed.entries), 2)
        undated = next(entry for entry in parsed.entries if entry['ID'] == 'figshare1')
        self.assertEqual(undated['author'], 'James Brown and Other Author')
        self.assertNotIn('year', undated)

    def test_publisher_month_macros_parse_without_shortdoi_service(self):
        resolver = doi2bib()
        with patch.object(resolver, 'get_bibtext', return_value='@article{paper, title={Example}, month=july, year={2025}}'):
            with patch.object(resolver, 'shorten') as shorten:
                entry = resolver.get_bibtex_entry('10.1234/example')
                self.assertEqual(entry['month'], 'July')
                shorten.assert_not_called()
                self.assertTrue(entry['ID'].startswith('doi'))

    def test_statistics_include_undated_publications_in_author_totals(self):
        with working_directory():
            df = pd.DataFrame([{'id': 1, 'author': 'James Brown', 'online_year': 2025},
                               {'id': 2, 'author': 'James Brown', 'online_year': None}])
            df.to_csv('all.csv', index=False)
            df.to_csv('dedup.csv', index=False)
            stats = generate_statistics('all.csv', 'dedup.csv')
            self.assertIn('Undated', stats)
            self.assertIn('**James Brown** | **2**', stats)


if __name__ == '__main__':
    unittest.main()
