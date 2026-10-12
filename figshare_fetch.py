#!/usr/bin/env python
"""Export the configured group's public Lincoln publications from Figshare."""

import argparse
import json
import logging
import os
from pathlib import Path

import pandas as pd
import yaml

from author import Author
from figshare_api import FigShare

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-c', '--config', required=True, help='Author YAML configuration')
    parser.add_argument('-o', '--output', default='figshare_articles.csv')
    parser.add_argument('-O', '--output-all', default='figshare_articles_all.csv')
    parser.add_argument('--use-author-cache', action='store_true',
                        help='Reuse complete author exports; may omit new publications')
    parser.add_argument('--rate-limit-delay', type=float, default=1.0)
    parser.add_argument('--max-retries', type=int, default=3,
                        help='Total attempts for temporary network/server failures')
    parser.add_argument('--debug', action='store_true')
    return parser.parse_args()


def load_yaml_config(filename):
    with open(filename, encoding='utf-8') as source:
        config = yaml.safe_load(source)
    if not isinstance(config, dict) or not config.get('authors'):
        raise ValueError('Configuration must contain a non-empty authors list')
    filters = config.get('filters') or {}
    if not isinstance(filters, dict):
        raise ValueError('filters must be a mapping')
    # Accept the old spelling so existing configurations still load.
    if 'instituion_id' in filters:
        filters.setdefault('institution_id', filters.pop('instituion_id'))
    unsupported = set(filters) - {'institution_id'}
    if unsupported:
        raise ValueError(f'Unsupported public-search filters: {sorted(unsupported)}')
    config['filters'] = filters
    if not isinstance(config['authors'], list):
        raise ValueError('authors must be a list')
    names = set()
    for author in config['authors']:
        if not isinstance(author, dict) or not isinstance(author.get('name'), str) or not author['name'].strip():
            raise ValueError('Each author must have a non-empty name')
        if author['name'] in names:
            raise ValueError(f"Duplicate configured author: {author['name']}")
        names.add(author['name'])
    return config


def fetch_publications(config, fs, use_author_cache=False):
    """Search publicly, then verify attribution against article author metadata."""
    frames = []
    institution_id = config['filters'].get('institution_id')
    for metadata in config['authors']:
        author = Author(metadata['name'], user_id=metadata.get('user_id'),
                        institution_id=institution_id, orcid=metadata.get('orcid'), fs=fs)
        fingerprint = json.dumps({'author': metadata, 'filters': config['filters'],
                                  'schema': 2}, sort_keys=True)
        manifest = Path(f'{author.name}.cache.json')
        cached = (use_author_cache and manifest.exists()
                  and manifest.read_text(encoding='utf-8') == fingerprint)
        if cached:
            author.load()
        else:
            author._retrieve_figshare(use_cache=False)
            author._remove_non_repository()
            author._retrieve_details(use_cache=True)
            author._remove_other_authors()
            author._custom_fields_to_dicts()
            author._flatten()
            author._create_dataframe()
            author.save()
            manifest.write_text(fingerprint, encoding='utf-8')
        if author.df is None or author.df.empty:
            logger.warning('No matching public publications for %s', author.name)
            continue
        author.df.to_csv(f'{author.name}.csv', index=False, encoding='utf-8')
        frames.append(author.df)
    if not frames:
        raise RuntimeError('No matching publications found for any configured author')
    all_articles = pd.concat(frames, ignore_index=True)
    all_articles = all_articles.drop_duplicates(subset=['id', 'author'])
    grouped_authors = all_articles.groupby('id')['author'].agg(
        lambda names: ', '.join(sorted(set(names))))
    unique_articles = all_articles.drop_duplicates(subset=['id']).copy()
    unique_articles['authors'] = unique_articles['id'].map(grouped_authors)
    return all_articles, unique_articles


def figshare_fetch():
    args = parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    config = load_yaml_config(args.config)
    fs = FigShare(rate_limit_delay=args.rate_limit_delay, max_retries=args.max_retries)
    all_articles, unique_articles = fetch_publications(config, fs, args.use_author_cache)
    # Only replace group outputs after every author search has completed successfully.
    for frame, filename in [(all_articles, args.output_all), (unique_articles, args.output)]:
        temporary = f'{filename}.tmp'
        frame.to_csv(temporary, index=False, encoding='utf-8')
        os.replace(temporary, filename)
    logger.info('Exported %s unique publications for %s authors',
                len(unique_articles), all_articles['author'].nunique())


if __name__ == '__main__':
    try:
        figshare_fetch()
    except Exception as error:
        logger.error('Publication export failed: %s', error)
        raise SystemExit(1)
