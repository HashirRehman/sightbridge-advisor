# Sightbridge Advisor Intelligence

**Trusted answers for tax-advantaged real estate.** An educational Q&A assistant for financial advisors that answers questions about 1031 exchanges, Delaware Statutory Trusts (DSTs) and 721 exchanges, using only a curated set of public documents. Every answer cites the passages it relied on.

> Work in progress. Sections are completed milestone by milestone.

## Local setup

Prerequisites: Docker, Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 22+.

```bash
docker compose up -d                      # PostgreSQL 16 + pgvector on localhost:5433
cp .env.example backend/.env

cd backend
uv sync
uv run alembic upgrade head               # apply migrations
uv run uvicorn app.main:app --port 8080   # http://localhost:8080/api/health
uv run pytest                             # unit + integration tests

cd ../frontend
npm install
npm run dev                               # http://localhost:3000
```

See [docs/architecture.md](docs/architecture.md) and [docs/decisions.md](docs/decisions.md).
