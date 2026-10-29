# Architecture

## Request flow

```text
Browser ──HTTPS──▶ Next.js (Amplify Hosting)
                    │  /api/auth/*   Cognito USER_PASSWORD_AUTH → httpOnly session cookies
                    │  /api/proxy/*  attaches Bearer ID token, forwards server-to-server
                    ▼
                 API Gateway (HTTP API) ──▶ Lambda: FastAPI via Mangum
                                              │ verify Cognito JWT (sig, iss, aud, exp, token_use)
                                              │ authorize (advisor owns conversation / admin group)
                                              ▼
                                     ChatService (application layer)
                                       1. validate + per-user rate limit
                                       2. AdvicePolicy (deterministic: injection, personalised advice, scope)
                                       3. embed question (Bedrock Titan v2)
                                       4. pgvector top-k + similarity floor
                                       5. generate structured answer (Bedrock Nova Lite, DeepSeek V3.2 fallback, via LangChain)
                                       6. CitationVerifier (passage id ∈ retrieved, quote ∈ passage text)
                                       7. append disclaimer (code, not model)
                                       8. persist exchange (audit) in one transaction
                                              ▼
                                     PostgreSQL + pgvector (RDS)      S3 (original source files)
```

## Backend layers (`backend/app`)

| Layer | Responsibility | Depends on |
|---|---|---|
| `api/` | HTTP routes, auth dependencies, request/response schemas. Thin. | application, domain |
| `application/` | Use cases: chat orchestration, RAG pipeline, ingestion. | domain interfaces |
| `domain/` | Policies (advice/scope/injection), citation verification, value objects, ports (`Embedder`, `AnswerGenerator`, repositories). Pure Python, no I/O. | nothing |
| `infrastructure/` | SQLAlchemy ORM models + repositories, Bedrock adapters, pgvector retrieval. | domain interfaces |

Swapping the model provider means writing a new `AnswerGenerator` adapter; routes and the RAG service do not change.

## Data model

Managed exclusively through SQLAlchemy ORM models (`app/infrastructure/database/models.py`) and Alembic migrations (`backend/alembic/versions`).

- `documents`: one row per curated source (stable `source_id`, URL, publisher, content hash, S3 key).
- `document_chunks`: chunk text, page/section reference, `vector(1024)` embedding.
- `ingestion_runs`: outcome of every ingestion attempt per source, failures included.
- `users`: Cognito `sub`, email, last-seen role.
- `conversations`: owned by one user.
- `exchanges`: one row per question → answer turn. This is both the conversation history and the immutable audit log (status, policy reason, cited sources snapshot, model id, token usage, latency).

## Local development

`docker compose up -d` starts PostgreSQL 16 + pgvector on `localhost:5433`. The API runs with `uv run uvicorn app.main:app --port 8080`, and the web app with `npm run dev`.
