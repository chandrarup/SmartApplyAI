# Nightly Job Scraper (Standalone)

This subsystem is intentionally independent of the existing knowledge service and main API.

## Public ATS endpoints used (no auth)

- Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`
- Lever: `GET https://api.lever.co/v0/postings/{token}?mode=json`
- Ashby: `GET https://api.ashbyhq.com/posting-api/job-board/{token}`

## Files

- `clients.py`: per-ATS fetchers for raw JSON jobs
- `normalize.py`: map ATS records to unified schema
- `store.py`: SQLite storage/upsert/expire logic (`jobs.db`)
- `run.py`: one full scrape cycle
- `schedule.py`: optional APScheduler nightly runner
- `tierb.py`: Tier B interface placeholder only
- `companies.yaml`: board targets
- `filters.yaml`: internship/location/sponsorship flag patterns

## Add companies

Edit `companies.yaml` and append entries:

```yaml
companies:
  - { ats: greenhouse, token: stripe }
  - { ats: lever, token: palantir }
  - { ats: ashby, token: notion }
```

Supported `ats` values: `greenhouse`, `lever`, `ashby`.

Tokens that 404 or return zero jobs are dropped during a run and listed in output.

## Targeting flags

`normalize.py` computes three booleans per job from `filters.yaml`:

- `is_internship` (title pattern match)
- `location_match` (allowed location pattern match)
- `sponsorship_knockout` (description regex match)

## Manual run

From repository root:

```bash
python -m backend.scraper.run
```

Or from `backend/`:

```bash
python -m scraper.run
```

## Scheduler (manual start only)

No scheduler starts automatically on import. To run nightly at default 02:00 local:

```bash
python -m backend.scraper.schedule
```

Or call `start_scheduler(hour=<0-23>, minute=<0-59>)` explicitly from your own entrypoint.
