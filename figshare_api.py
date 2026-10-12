#!/usr/bin/env python
# -*- coding: utf-8 -*-

from requests import get, post
from requests.exceptions import ConnectionError, Timeout
import shelve
import time
import os
from logging import getLogger, INFO


class FigShare:
    def __init__(self, page_size=100, rate_limit_delay=1.0, max_retries=5):
        if page_size < 1 or rate_limit_delay < 0 or max_retries < 1:
            raise ValueError("page_size and max_retries must be positive; delay cannot be negative")
        self.logger = getLogger("FigShare")
        self.token = os.getenv('FIGSHARE_TOKEN')
        if self.token:
            self.logger.info("Figshare API: Using authenticated requests")
        else:
            self.logger.info("Figshare API: Using public requests without authentication")
        self.page_size = page_size
        self.rate_limit_delay = rate_limit_delay
        self.max_retries = max_retries
        self.base_url = "https://api.figshare.com/v2"
        
        if self.rate_limit_delay > 0:
            self.logger.info(f"Rate limiting enabled: {self.rate_limit_delay} second delay between API requests")

        # Use shelve for persistent caching
        self.cache_file = "figshare_cache.db"

        with shelve.open(self.cache_file) as cache:
            self.logger.info(f"Figshare API: Using cache file {self.cache_file} with {len(cache.keys())} entries")
            for key in list(cache.keys()):
                self.logger.debug(f"  existing cache key: {key}")


    def __init_params(self):
        return {
            "page_size": self.page_size
        }

    def _request(self, method, url, params=None, use_cache=True, revision=None):
        """Cache successful responses; retry only temporary failures."""
        cache_key = f"v2:{method}:{url}:{params}:{revision}"
        with shelve.open(self.cache_file) as cache:
            if use_cache and cache_key in cache:
                return cache[cache_key]

            headers = {"Authorization": "token " + self.token} if self.token else {}
            for attempt in range(self.max_retries):
                try:
                    if method == "GET":
                        response = get(self.base_url + url, headers=headers,
                                       params=params, timeout=30)
                    else:
                        response = post(self.base_url + url, headers=headers,
                                        json=params, timeout=30)
                except (ConnectionError, Timeout) as error:
                    if attempt == self.max_retries - 1:
                        raise RuntimeError(f"Figshare {method} {url} failed: {error}") from error
                else:
                    if response.status_code not in {429, 500, 502, 503, 504}:
                        response.raise_for_status()
                        try:
                            result = response.json()
                        except ValueError as error:
                            raise RuntimeError(f"Figshare {method} {url} returned invalid JSON") from error
                        expected_type = dict if method == "GET" else list
                        if not isinstance(result, expected_type) or (method == "GET" and not result):
                            raise RuntimeError(f"Unexpected response from Figshare {method} {url}")
                        cache[cache_key] = result
                        if self.rate_limit_delay:
                            time.sleep(self.rate_limit_delay)
                        return result
                    if attempt == self.max_retries - 1:
                        response.raise_for_status()
                delay = min(2 ** attempt, 30)
                self.logger.warning("Temporary Figshare failure; retrying in %s seconds", delay)
                time.sleep(max(delay, self.rate_limit_delay))

    def __get(self, url, params=None, use_cache=True, revision=None):
        return self._request("GET", url, params, use_cache, revision)

    def __post(self, url, params=None, use_cache=True):
        return self._request("POST", url, params, use_cache)

    def articles_by_author(self, author_name, user_id=None, institution_id=None, use_cache=True):
        """Search for articles by author name with optional institution filtering.
        
        Uses the Figshare search API with the :author: search operator and optional
        institution parameter. Note: Figshare's search API does not support searching
        by author_id directly, so we use the author name for search and apply
        institution filtering to narrow results.
        
        Args:
            author_name: The author's full name to search for (required)
            user_id: Figshare user ID (optional, used only for logging/reference)
            institution_id: Institution ID to filter articles (optional, recommended
                          for more precise results when available)
            use_cache: Whether to use cached results (default: True)
        
        Returns:
            List of article dictionaries matching the search criteria. Each article
            contains metadata like id, title, authors, DOI, etc.
        
        Example:
            articles = fs.articles_by_author(
                "Marc Hanheide", 
                user_id=17159320,
                institution_id=1068
            )
        """
        params = self.__init_params()
        
        # Use :author: search operator with author name
        # This is the only reliable way to search by author in Figshare API
        params["search_for"] = f':author: "{author_name}"'
        
        # Add institution filter as direct parameter if provided
        # This significantly narrows results when multiple authors share the same name
        if institution_id:
            params["institution"] = institution_id
            self.logger.info(f"Filtering by institution_id: {institution_id}")
        
        # Paginate through all results
        page = 1
        articles = []
        while True:
            params["page"] = page
            if user_id:
                self.logger.info(f"retrieving page {page} for {author_name} (user_id: {user_id})")
            else:
                self.logger.info(f"retrieving page {page} for {author_name}")
            current_page_articles = self.__post("/articles/search", params=params, use_cache=use_cache)
            page += 1
            if len(current_page_articles) == 0:
                break
            articles += current_page_articles
        
        if user_id:
            self.logger.info(f"found {len(articles)} articles for {author_name} (user_id: {user_id})")
        else:
            self.logger.info(f"found {len(articles)} articles for {author_name}")

        return articles
    
    def articles_by_user_name(self, user_name, use_cache=True):
        """Search for articles by author name without additional filtering.
        
        This is a simpler version of articles_by_author() without institution
        filtering or user_id tracking. Use articles_by_author() for more precise
        searches when institution_id is available.
        
        Args:
            user_name: The author's full name to search for
            use_cache: Whether to use cached results (default: True)
        
        Returns:
            List of article dictionaries matching the author name
        """
        params = self.__init_params()
        params["search_for"] = f':author: \"{user_name}\"'
        page = 1
        articles = []
        while True:
            params["page"] = page
            self.logger.info(f"retrieving page {page} for user {user_name}")
            current_page_articles = self.__post("/articles/search", params=params, use_cache=use_cache)
            page += 1
            if len(current_page_articles) == 0:
                break
            articles += current_page_articles
        self.logger.info(f"found {len(articles)} articles for user {user_name}")

        return articles
    
    def get_article(self, article_id, use_cache=True, revision=None):
        return self.__get(f"/articles/{article_id}", use_cache=use_cache, revision=revision)
    
    def search_authors(self, params, use_cache=True):
        """Search for authors using the Figshare account API.
        
        Args:
            params: Dictionary with search parameters (search, orcid, is_active, 
                   is_public, group_id, institution_id)
            use_cache: Whether to use cached results
        
        Returns:
            List of author dictionaries matching the search criteria
        """
        self.logger.info(f"Searching for authors with params: {params}")
        return self.__post("/account/authors/search", params=params, use_cache=use_cache)
