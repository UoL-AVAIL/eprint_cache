# AVAIL publication cache

This repository collates public University of Lincoln Figshare publications for the researchers in [avail-authors.yaml](avail-authors.yaml). It produces CSV exports, an AVAIL bibliography and publication statistics. Only the AVAIL workflow is enabled; inherited scripts and configurations are retained as historical files.

## Local setup

Use Python 3.10 or later and a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-frozen.txt
mkdir -p output
cd output
python ../figshare_fetch.py -c ../avail-authors.yaml
python ../figshare_bibtex.py -o avail.bib
python ../generate_stats.py > publication-statistics.md
```

Run from `output` to keep exports and caches together. Public retrieval does not require a Figshare account or API token. An optional `FIGSHARE_TOKEN` environment variable authenticates requests; invalid credentials cause an explicit failure.

The exports are:

| File | Contents |
| --- | --- |
| `figshare_articles.csv` | One row per Figshare article, with all matching group members in `authors` |
| `figshare_articles_all.csv` | One row per article per matching group member |
| `avail.bib` | BibTeX entries from DOI lookups, with repository metadata as a fallback |
| `publication-statistics.md` | Counts per researcher and publication year, including undated records |
| `{author}.csv` | Publications attributed to one configured researcher |

CSV deduplication uses the Figshare article ID. Separate deposits of the same paper can therefore remain separate rows. BibTeX deduplication also merges entries that share an external DOI.

## Author configuration and attribution

The YAML configuration contains an institution filter and author names:

```yaml
filters:
  institution_id: 1068

authors:
  - name: "James Brown"
    orcid: "0000-0001-7636-4554"
```

Searches use names within Lincoln's repository. The exporter checks the full author metadata for each result. A configured `user_id` requires the matching Figshare author ID. Otherwise, an ORCID is matched when both the configuration and the record contain one; records without an ORCID fall back to an exact name match, ignoring case and repeated spaces. An explicit conflicting ORCID is rejected. Names with variants may need separate investigation or a corrected search name. The exporter does not use the private account author-search endpoint or guess which author to select from ambiguous search results.

The roster defines whose outputs to include; publications co-authored with people outside the group retain their full author list in BibTeX. Retrieval covers public Lincoln records of all dates and repository item types. Private records and unpublished deposits are excluded by the public API.

## Dates and bibliographic completeness

Publication years use Figshare's `firstOnline` date, with `publisherPublication` as a fallback. Repository posting dates and manuscript acceptance dates are not treated as publication dates. Records without either publication date appear in an `Undated` statistics column and still contribute to totals.

DOIs are read from the `External DOI` custom field or `resource_doi`. The exporter accepts both string and list representations. It does not guess DOIs from titles. If a DOI is absent or its bibliography lookup fails, a BibTeX entry is built from the repository title, full author list and available publication metadata. Such entries may have fewer fields than a publisher's entry; inspect them before using the bibliography in a manuscript.

## Refreshing and failures

By default, each run searches again for publications and reuses article details only when their `modified_date` has not changed. Complete author exports can be reused explicitly:

```bash
python ../figshare_fetch.py -c ../avail-authors.yaml --use-author-cache
```

This option may omit new publications. Author caches are checked against the configured identity and filters. Remove the ignored `output` directory to start without caches.

The default delay is one second between Figshare requests. Temporary connection failures and HTTP 429/5xx responses have three attempts by default, with bounded backoff. HTTP 401/403 and invalid responses fail immediately. Network requests have a 30-second timeout. A failed search exits unsuccessfully before replacing either group CSV; a genuine empty result for an individual researcher is logged as a warning. If every researcher has no matching publications, the run fails.

Use `--debug` for additional logging. `--output`, `--output-all`, `--max-retries` and `--rate-limit-delay` customise fetching; BibTeX supports `--input` and `--output`.

## GitHub Actions

[avail-figshare-processing.yaml](.github/workflows/avail-figshare-processing.yaml) runs on pushes and pull requests targeting `main`, on manual dispatch, and at 00:30 and 12:30 UTC each day. It runs the regression tests, retrieves the AVAIL publications, generates `avail.bib`, and adds statistics to the run summary. The job has a 60-minute limit. Successful output files are available as the `outputs` artifact for 30 days.

The workflow uses an AVAIL-specific cache namespace. No LCAS or UoA11 workflows run.

Nexus publishing is optional. For non-pull-request runs it is enabled when both repository secrets are set:

- `AVAIL_REGISTRY_PUSHER`: Nexus publishing username.
- `AVAIL_REGISTRY_TOKEN`: Nexus publishing password/token.

The configured destination is `https://avail.lincoln.ac.uk/repository/`, repository `misc`, directory `bibtex`. Without these secrets, publishing is skipped and the workflow still provides downloadable artifacts. `FIGSHARE_TOKEN` is optional. A local export does not verify Nexus credentials or publishing.

## Verification

From the repository root, with the virtual environment activated:

```bash
python -m unittest discover -s tests -v
```

The tests cover API failures and retries, cache refreshes, author attribution, shared publications, DOI handling, BibTeX fallback, undated statistics and unsuccessful command exits. They use mocked network responses. Live retrieval is a separate check.
