#!/usr/bin/env python
"""Generate an AVAIL bibliography, retaining records without resolvable DOIs."""

import argparse
import ast
import logging
import re

import bibtexparser
from bibtexparser.bibdatabase import BibDatabase
import pandas as pd

from doi2bib import doi2bib

logger = logging.getLogger(__name__)


def text_value(row, column):
    value = row.get(column)
    return value.strip() if isinstance(value, str) else ''


def metadata_entry(row):
    """Use repository metadata when DOI content negotiation is unavailable."""
    article_id = int(row['id'])
    entry = {'ID': f'figshare{article_id}', 'ENTRYTYPE': 'misc',
             'title': text_value(row, 'title'), 'url': text_value(row, 'url_public_html')}
    authors = row.get('details/authors', [])
    if isinstance(authors, str):
        authors = ast.literal_eval(authors)
    if isinstance(authors, list) and authors:
        entry['author'] = ' and '.join(author['full_name'] for author in authors)
    publication_type = text_value(row, 'defined_type_name')
    publication = text_value(row, 'details/custom_fields/Publication Title')
    if publication_type == 'journal contribution':
        entry['ENTRYTYPE'] = 'article'
        if publication:
            entry['journal'] = publication
    elif publication_type == 'conference contribution':
        entry['ENTRYTYPE'] = 'inproceedings'
        if publication:
            entry['booktitle'] = publication
    year = row.get('online_year')
    if pd.notna(year):
        entry['year'] = str(int(year))
    for field, column in [('publisher', 'Publisher'), ('volume', 'Volume'),
                          ('number', 'Issue'), ('pages', 'Pages/Article Number')]:
        value = text_value(row, f'details/custom_fields/{column}')
        if value:
            entry[field] = value
    doi = text_value(row, 'External DOI')
    if doi:
        entry['doi'] = doi
    return entry


def build_bibliography(df, resolver):
    if df.empty or not {'id', 'title'}.issubset(df.columns):
        raise ValueError('Input must contain publications with id and title columns')
    entries = {}
    fallbacks = 0
    for _, row in df.iterrows():
        doi = text_value(row, 'External DOI')
        key = doi.casefold() if doi else f"figshare:{int(row['id'])}"
        if key in entries:
            continue
        entry = None
        if re.match(r'^10\.\d{4,9}/\S+$', doi):
            try:
                entry = resolver.get_bibtex_entry(doi)
            except Exception as error:
                logger.warning('DOI lookup failed for %s: %s', doi, error)
        if not entry:
            entry = metadata_entry(row)
            fallbacks += 1
        # The same publication may have multiple Figshare deposits.
        entries[key] = entry
    database = BibDatabase()
    database.entries = list(entries.values())
    logger.info('Created %s entries; %s records used repository metadata',
                len(database.entries), fallbacks)
    return database


def figshare_bibtex():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('-i', '--input', default='figshare_articles.csv')
    parser.add_argument('-o', '--output', default='avail.bib')
    parser.add_argument('--debug', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO)
    df = pd.read_csv(args.input, encoding='utf-8')
    database = build_bibliography(df, doi2bib())
    with open(args.output, 'w', encoding='utf-8') as output:
        output.write(bibtexparser.dumps(database))


if __name__ == '__main__':
    try:
        figshare_bibtex()
    except Exception as error:
        logger.error('Bibliography export failed: %s', error)
        raise SystemExit(1)
