# Knowledge Service

Standalone FastAPI wrapper around `knowledge.store` + related helpers so other local processes can use profile knowledge over HTTP.

## Run

From `backend/`:

```bash
uvicorn knowledge.service:app --host 127.0.0.1 --port 5100
```

Or:

```bash
python -m knowledge.service
```

`KNOWLEDGE_SERVICE_PORT` overrides the default `5100` for the module runner.

## Backend Integration Toggle

- `KNOWLEDGE_SERVICE_URL` unset: `knowledge.client` uses in-process functions.
- `KNOWLEDGE_SERVICE_URL=http://127.0.0.1:5100`: `knowledge.client` calls the HTTP service.

`backend/main.py` uses `knowledge.client`, so behavior stays the same in either mode.
