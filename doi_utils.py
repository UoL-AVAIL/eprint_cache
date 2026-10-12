#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Utility functions for DOI guessing and retrieval
"""

import requests
import shelve
from logging import getLogger
from difflib import SequenceMatcher

logger = getLogger("doi_utils")


def guess_doi_from_crossref(title, author):
    """
    Use crossref API to guess the DOI for an article based on the title and authors
    
    Args:
        title: Article title
        author: Author name
        
    Returns:
        DOI string if found, None otherwise
    """
    with shelve.open("crossref_cache.db") as cache:
        if not title:
            logger.warning("No title found for article, can't guess DOI")
            return None
        
        if title in cache:
            logger.info(f"Found DOI {cache[title]} in cache for title: {title}")
            return cache[title]

        # Construct query URL for Crossref API
        base_url = "https://api.crossref.org/works"
        params = {
            "query.bibliographic": f"{title}",
            "query.author": f"{author}",
            "sort": "relevance",
            "rows": 10,  # Get top 10 matches
            "select": "DOI,title,author",
        }
        
        try:
            
            logger.debug(f"Querying Crossref for title: {title}")
            response = requests.get(base_url, params=params, timeout=30)
            response.raise_for_status()
            
            # Check if response is valid and contains JSON
            if response.ok and response.headers.get('Content-Type', '').lower().startswith('application/json') and response.text.strip():
                data = response.json()
            else:
                logger.warning(f"Received empty or invalid JSON response from Crossref API (status: {response.status_code})")
                return None
            
            if data["message"]["total-results"] == 0:
                logger.debug(f"No DOI found for: {title}")
                return None
            
            # Get all matches and find the best one using fuzzy matching
            items = data["message"]["items"]
            if items:
                logger.debug(f"Found {len(items)} potential matches for title: {title}")
                
                best_match = None
                best_score = 0
                threshold = 0.8  # Minimum similarity score to accept a match
                
                for item in items:
                    if "title" in item and item["title"]:
                        item_title = item["title"][0]
                        # Calculate similarity score
                        score = SequenceMatcher(None, title.lower(), item_title.lower()).ratio()
                        logger.debug(f"==== '{title}' == '{item['title'][0]}'??? ==> {score:.2f}")
                        
                        if score > best_score:
                            best_score = score
                            best_match = item
                
                if best_match and best_score >= threshold:
                    doi = best_match.get("DOI")
                    authors_string = str(best_match.get("author", ""))
                    authors_last_name = author.split()[-1]
                    
                    if doi and authors_last_name in authors_string:
                        logger.info(f"Found DOI {doi} for title: {title} (match score: {best_score:.2f})")
                        cache[title] = doi
                        return doi
                    else:
                        logger.warning(f"DOI found but author {authors_last_name} not in authors list or DOI missing")
                else:
                    logger.warning(f"No good title match found. Best score was {best_score:.2f}, below threshold {threshold}")
                    if best_match and 'title' in best_match:
                        logger.warning(f"  '{title}' != '{best_match['title'][0]}' (score: {best_score:.2f})")
                
                return None
        
        except Exception as e:
            logger.warning(f"Error guessing DOI: {e}")
        
        return None
