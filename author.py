#!/usr/bin/env python
# -*- coding: utf-8 -*-

import pandas as pd
import shelve
import re
from logging import getLogger, INFO, DEBUG
from flatten_dict import flatten

from figshare_api import FigShare
from doi2bib import doi2bib
from doi_utils import guess_doi_from_crossref


class Author:
    """Represents an author and manages their Figshare article collection.
    
    This class handles retrieving, processing, and caching article metadata for
    a specific author from the Figshare repository.
    """
    
    def __init__(self, name, user_id=None, institution_id=None, orcid=None, debug=False, rate_limit_delay=1.0, max_retries=5, fs=None):
        """Initialize an Author instance.
        
        Args:
            name: Author's full name (required)
            user_id: Figshare user ID (optional, improves search accuracy)
            institution_id: Institution ID for filtering articles (optional, recommended)
            orcid: Author's ORCID identifier (optional, for reference)
            debug: Enable debug logging (default: False)
            rate_limit_delay: Delay in seconds between API requests (default: 1.0)
            max_retries: Maximum retry attempts for failed API calls (default: 5)
        """
        self.logger = getLogger("Author")
        if debug:
            self.logger.setLevel(DEBUG)
        self.name = name
        self.user_id = user_id
        self.institution_id = institution_id
        self.orcid = orcid
        self.fs = fs if fs is not None else FigShare(rate_limit_delay=rate_limit_delay, max_retries=max_retries)
        self.articles = {}
        self.public_html_prefix = "https://repository.lincoln.ac.uk"
        self.df = None

    def save(self, filename=None):
        """Save author's articles and dataframe to a persistent cache file.
        
        Args:
            filename: Path to cache file (default: '{author_name}.db')
        """
        if filename is None:
            filename = f"{self.name}.db"
        with shelve.open(filename) as db:
            db['articles'] = self.articles
            db['df'] = self.df

    def load(self, filename=None):
        """Load author's articles and dataframe from a persistent cache file.
        
        Args:
            filename: Path to cache file (default: '{author_name}.db')
        """
        if filename is None:
            filename = f"{self.name}.db"
        with shelve.open(filename) as db:
            self.articles = db['articles']
            self.df = db['df']
    

    def _retrieve_figshare(self, use_cache=True):
        """Retrieve articles for this author from Figshare.
        
        Uses the most precise search method available based on the author metadata:
        - If user_id and/or institution_id are available, uses articles_by_author()
          with filtering for more accurate results
        - Otherwise, falls back to simple name-based search
        
        Args:
            use_cache: Whether to use cached API results (default: True)
        """
        self.logger.info(f"retrieving articles for {self.name}")
        
        # Use enhanced search with user_id tracking and institution filtering when available
        if self.user_id or self.institution_id:
            if self.user_id and self.institution_id:
                self.logger.info(f"Using enhanced search for user_id {self.user_id} with institution_id {self.institution_id}")
            elif self.user_id:
                self.logger.info(f"Using enhanced search for user_id {self.user_id}")
            else:
                self.logger.info(f"Using enhanced search with institution_id {self.institution_id}")
            
            self.articles = self.fs.articles_by_author(
                self.name,
                user_id=self.user_id,
                institution_id=self.institution_id,
                use_cache=use_cache
            )
        else:
            self.logger.info(f"Using basic name search (no user_id or institution_id available)") 
            self.articles = self.fs.articles_by_user_name(self.name, use_cache=use_cache)

        self.logger.info(f"found {len(self.articles)} articles for {self.name}")

    def _retrieve_details(self, use_cache=True):
        """Retrieve detailed metadata for each article.
        
        Fetches full article details including custom fields, tags, categories, etc.
        
        Args:
            use_cache: Whether to use cached API results (default: True)
        """
        for article in self.articles:
            self.logger.info(f"retrieving details for article {article['id']}")
            article['details'] = self.fs.get_article(
                article['id'], use_cache=use_cache, revision=article.get('modified_date'))
            if not isinstance(article['details'].get('authors'), list):
                raise RuntimeError(f"Article {article['id']} is missing its author metadata")

    def _remove_non_repository(self):
        self.logger.info(f"removing non-repository articles out of {len(self.articles)}")
        self.articles = [a for a in self.articles if a.get('url_public_html', '').startswith(self.public_html_prefix + '/')]
        self.logger.info(f"retained {len(self.articles)} articles")

    def _remove_other_authors(self):
        """Reject name-search matches that belong to a different author."""
        def normalise_name(name):
            return ' '.join(name.casefold().split())

        def matches(candidate):
            if self.user_id is not None:
                return str(candidate.get('id')) == str(self.user_id)
            candidate_orcid = (candidate.get('orcid_id') or '').removeprefix('https://orcid.org/')
            expected_orcid = (self.orcid or '').removeprefix('https://orcid.org/')
            if expected_orcid and candidate_orcid:
                return expected_orcid == candidate_orcid
            return normalise_name(candidate.get('full_name', '')) == normalise_name(self.name)

        self.articles = [article for article in self.articles
                         if any(matches(candidate) for candidate in article['details'].get('authors', []))]

    def _custom_fields_to_dicts(self):
        for article in self.articles:
            if 'details' not in article:
                continue
            if 'custom_fields' not in article['details']:
                continue
            self.logger.debug(f"convert")

            cf = article['details']['custom_fields']
            if type(cf) == list:
                new_cf = {}
                for p in cf:
                    new_cf[p['name']] = p['value']
                article['details']['custom_fields'] = new_cf


    def _guess_doi(self, article):
        """
        Use crossref API to guess the DOI for an article based on the title and authors
        """
        if 'title' not in article or not article['title']:
            self.logger.warning("No title found for article, can't guess DOI")
            return None
        
        title = article['title']
        author = article['author']
        
        return guess_doi_from_crossref(title, author)


    def _retrieve_bibtex_from_dois(self):
        if self.df is None:
            self.logger.warning(f"no dataframe found for {self.name}, can't continue")
            return
        doi2bibber = doi2bib()
        # iterate over all rows in the dataframe self.df
        for index, row in self.df.iterrows():
            doi = row['External DOI']
            # Check if DOI is in valid format
            if doi and isinstance(doi, str):
                # Basic DOI validation - should start with 10. followed by numbers/dots/hyphens
                if not doi.startswith('10.') or not len(doi.split('/', 1)) == 2:
                    self.logger.warning(f"Invalid DOI format: {doi}, will try to guess")
                    doi = None
            else:
                self.logger.info(f"No DOI defined in record for article, will try to guess")
                doi = None
            if doi is None:
                doi = self._guess_doi(row)
                if doi is None:
                    self.logger.debug(f"Unable to guess DOI for article, no option left but to skip it")
                    continue
                self.logger.info(f"Guessed DOI for article: {doi}, updating dataframe")
                self.df.at[index, 'External DOI'] = doi
            try:
                bibtex = doi2bibber.get_bibtex_entry(doi)
                # Update the dataframe with the bibtex information
                if bibtex is not None:
                    self.df.at[index, 'bibtex'] = bibtex
                    self.df.at[index, 'bibtex_str'] = doi2bibber.entries_to_str([bibtex])
                    self.logger.info(f"got bibtex for {doi}")
                else:
                    self.logger.warning(f"Couldn't get bibtex for {doi}")

            except Exception as e:
                self.logger.warning(f"Failed to get bibtex for {doi}: {e}")
    
    def _flatten(self):
        self.logger.info(f"flattening article dicts for {self.name}")
        new_articles = []
        for a in self.articles:
            try:
                new_articles.append(flatten(a, reducer='path'))
            except Exception as e:
                self.logger.warning(f"Failed to flatten article {a}: {e}")
        self.articles = new_articles

    def retrieve(self, use_cache=True):
        self._retrieve_figshare(use_cache=use_cache)
        self._remove_non_repository()
        self._retrieve_details(use_cache=True)
        self._custom_fields_to_dicts()
        self._flatten()
        self._create_dataframe()
        self._retrieve_bibtex_from_dois()

    def _create_dataframe(self):
        if len(self.articles) == 0:
            self.logger.warning(f"no articles found for {self.name}, can't create dataframe")
            self.df = None
            return
        self.df = pd.DataFrame.from_dict(self.articles)
        # add column with author name
        self.df['author'] = self.name
        
        # add column with online date (as datetime object)
        if 'timeline/firstOnline' in self.df.columns:
            self.df['online_date'] = pd.to_datetime(self.df['timeline/firstOnline'], utc=True, errors='coerce', format='mixed')
        else:
            self.logger.warning(f"'timeline/firstOnline' field not found, setting online_date to NaT")
            self.df['online_date'] = pd.Series(pd.NaT, index=self.df.index, dtype='datetime64[ns, UTC]')
        
        # Keep repository posting dates separate from publication dates.
        # Undated accepted manuscripts remain undated in the publication statistics.
        if 'details/timeline/publisherPublication' in self.df.columns:
            publication_date = pd.to_datetime(
                self.df['details/timeline/publisherPublication'], utc=True,
                errors='coerce', format='mixed')
            self.df['online_date'] = self.df['online_date'].fillna(publication_date)
        self.df['online_year'] = self.df['online_date'].dt.year.astype('Int64')

        def normalise_doi(value):
            if isinstance(value, list):
                value = value[0] if value else None
            if not isinstance(value, str):
                return None
            value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '',
                           value.strip(), flags=re.IGNORECASE)
            return value if re.match(r'^10\.\d{4,9}/\S+$', value) else None

        external = self.df.get('details/custom_fields/External DOI',
                               pd.Series(None, index=self.df.index, dtype=object))
        self.df['External DOI'] = external.map(normalise_doi)
        resource = self.df.get('details/resource_doi',
                               pd.Series(None, index=self.df.index, dtype=object))
        self.df['External DOI'] = self.df['External DOI'].fillna(resource.map(normalise_doi))

        return self.df
