# Atlanta Car Finder: scraper source

[![Fixture tests](https://github.com/theonetruedaniel/atlanta-car-finder/actions/workflows/ci.yml/badge.svg)](https://github.com/theonetruedaniel/atlanta-car-finder/actions/workflows/ci.yml)

Marketplace parsing and collection code by **Daniel Abrams**, developed with AI assistance. This edition contains TrueCar and CarGurus adapters, evidence normalization, bounded collection, an upload client and fixture tests.

**Status: public source snapshot.** The original private repository retains its dashboard connection and scheduled job. This edition runs fixture-only GitHub Actions checks and contains no live-crawl workflow or credentials. Publication does not start a live crawl.

## What to inspect

- `src/car_finder_scrapers/truecar.py` and `cargurus.py`: source-specific extraction.
- `models.py`, `normalize.py` and `evidence.py`: explicit facts, unknown values and validation.
- `crawl.py` and `cli.py`: bounded runs and complete/partial/blocked/failed outcomes.
- `upload.py`: per-source delivery, bounded retries and refusal of unsafe redirects.
- `tests/`: local parser, model, pagination, upload and CLI cases.

Missing accident, title or equipment text stays unknown. A blocked crawl does not count as zero inventory. Partial and failed attempts preserve the meaning of the last complete snapshot.

## Run fixture tests

Requires Python 3.12 or newer.

```sh
python -m pip install -e ".[test]"
python -m pytest -q
```

GitHub Actions runs these fixture tests on Python 3.12 and 3.13 for pushes and pull requests. Tests use local fixtures, fake fetchers and a loopback HTTP server; the workflow does not install a browser runtime, crawl marketplaces or upload inventory.

Fixture tests do not launch a browser. A live crawl requires Scrapling's browser runtime and your own ingestion endpoint and credentials. Review the source search filters; they reflect an illustrative Atlanta-area search.

## Deployment example

`docs/daily-inventory.example.yml` records the original workflow structure with a placeholder endpoint. It is outside `.github/workflows`, so GitHub does not execute it. Tests inspect this example as documentation of the original budget/concurrency design. A new deployment requires its own authentication, destination and schedule decisions.

Data-center requests may be blocked. Passing fixture tests does not establish current live collection coverage.

## Provenance

Prepared from revision `6e5eee53f63333aa94d810a21aaedcaa71490891` of the original private repository. This edition starts a clean public history and excludes live deployment wiring. The project remains a prototype, with no guarantee of continuous inventory or provider access.

[Portfolio](https://theonetruedaniel.github.io/) · [GitHub](https://github.com/theonetruedaniel)

## License

Original software and accompanying documentation in this repository are licensed under the [MIT License](LICENSE), copyright (c) 2026 Daniel Abrams. Third-party dependencies and any third-party content retain their respective licenses and rights.
