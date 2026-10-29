# Sightbridge Advisor Intelligence

**Trusted answers for tax-advantaged real estate.** An educational Q&A assistant for financial advisors. It answers questions about **1031 exchanges, Delaware Statutory Trusts (DSTs) and 721 exchanges into REIT operating partnerships**, using only 13 vetted public documents, and it cites the exact passage behind every claim. It declines personalised tax, legal or investment advice, adds a disclaimer to every response, and logs every exchange to an admin audit view.

> Prototype for the Sightbridge AI Engineering Lead take-home. Not an official Sightbridge product. The one-page business memo is [docs/memo.md](docs/memo.md) ([PDF](docs/memo.pdf)).

## Live demo

**Not deployed yet.** The Terraform is written and validated, and the Lambda package was tested in AWS's official runtime image. The AWS account available during the build blocks *all* Bedrock model invocation at account level (`Operation not allowed`, including Amazon's own models). Details and the exact runbook are in [docs/deployment.md](docs/deployment.md). Reviewer logins will be shared privately once the stack is live; no credentials are ever committed.

Everything below runs locally today.

## How it works

```text
Browser → Next.js (login + same-origin proxy; tokens in httpOnly cookies)
        → FastAPI on Lambda (Cognito JWT verified on every request)
        → policy check → Titan embeddings → pgvector top-6 → Nova Lite (structured)
        → citation verification → disclaimer → append-only audit row (PostgreSQL)
```

- **Grounding:**
  - Each answer is built from at most 6 retrieved passages, with a similarity floor.
  - The model must return claims, each tied to a passage ID and a **verbatim quote**.
  - Code then checks every citation:
    1. The passage was actually retrieved for this question.
    2. The quote appears in the stored passage text (allowing for whitespace and typography).
    3. The claim overlaps its evidence.
    4. The claim contains no advice phrasing.
  - Failing claims are dropped. If nothing survives, the user gets an honest *"not found in the approved sources"* rather than a guess.
  - Citations deep-link to the PDF page or the exact passage on a web page.
- **Safety (enforced in code, not prompts):**
  - Prompt-injection attempts, personalised-advice requests and out-of-scope topics are caught by tested rules **before** any model call.
  - Personalised questions get a fixed decline, plus general education only when it passes verification.
  - The disclaimer is added by code to every outcome: answered, refused, unsupported or failed.
  - Retrieved text is treated as untrusted data in the prompt.
- **Audit:**
  - Every exchange is stored once: question, the answer exactly as shown, a citation snapshot, status and reason, model, tokens, latency and request ID.
  - A database trigger makes the log **append-only**.
  - Admins can filter by outcome, user and text.
- **Models:** Amazon Nova Lite (primary), DeepSeek V3.2 (fallback, used only if Nova errors), and Titan Text Embeddings V2. All on Bedrock, temperature 0, output capped at 900 tokens.

More: [architecture](docs/architecture.md) · [decision log](docs/decisions.md) (13 decisions with alternatives and tradeoffs).

## Sources (13)

| # | Source | Publisher | Topics |
|---|---|---|---|
| 1 | [Like-kind exchanges – Real estate tax tips](https://www.irs.gov/businesses/small-businesses-self-employed/like-kind-exchanges-real-estate-tax-tips) | IRS | 1031 |
| 2 | [Like-kind exchanges now limited to real property](https://www.irs.gov/newsroom/like-kind-exchanges-now-limited-to-real-property) | IRS | 1031 |
| 3 | [Instructions for Form 8824, Like-Kind Exchanges](https://www.irs.gov/instructions/i8824) | IRS | 1031 |
| 4 | [Revenue Ruling 2004-86 (DSTs and § 1031)](https://home.treasury.gov/system/files/136/archive-documents/rr0486.pdf) | IRS (IRB 2004-33, Treasury-hosted copy) | DST, 1031 |
| 5 | [26 U.S.C. § 1031](https://www.law.cornell.edu/uscode/text/26/1031) | Cornell LII | 1031 |
| 6 | [26 CFR § 1.1031(k)-1, Deferred exchanges](https://www.law.cornell.edu/cfr/text/26/1.1031%28k%29-1) | Cornell LII | 1031 |
| 7 | [26 U.S.C. § 721](https://www.law.cornell.edu/uscode/text/26/721) | Cornell LII | 721 |
| 8 | [721 Exchange/UPREIT](https://www.franklintempleton.com/forms-literature/download/721-FL) | Franklin Templeton | 721, REIT |
| 9 | [Real Estate Investment Trusts (REITs)](https://www.investor.gov/introduction-investing/investing-basics/investment-products/real-estate-investment-trusts-reits) | SEC (Investor.gov) | REIT, 721 |
| 10 | [Private Placements under Regulation D](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/private-placements-under-regulation-d-updated-investor-bulletin) | SEC (Investor.gov) | DST, regulatory |
| 11 | [Accredited Investors](https://www.investor.gov/introduction-investing/investing-basics/glossary/accredited-investors) | SEC (Investor.gov) | DST, regulatory |
| 12 | [Notice to Members 05-18 (§ 1031 TIC interests)](https://www.finra.org/rules-guidance/notices/05-18) | FINRA (then NASD) | 1031, regulatory |
| 13 | [Six Risks of DSTs in 1031 Exchanges](https://www.kiplinger.com/retirement/risks-of-delaware-statutory-trusts-in-1031-exchanges) | Kiplinger | DST, 1031 |

The manifest is in [backend/data/sources.json](backend/data/sources.json), and content hashes and retrieval dates are in [backend/data/sources.lock.json](backend/data/sources.lock.json). IRS FS-2008-18 was deliberately **excluded**: it predates the 2017 TCJA, which limited § 1031 to real property, so it would ground outdated answers.

## Run locally

Prerequisites: Docker, Python 3.12 + [uv](https://docs.astral.sh/uv/), Node 22+. No AWS is needed: local mode uses a deterministic lexical embedder, an extractive stand-in for the LLM, and local sign-in. Production config refuses all three.

```bash
docker compose up -d                         # PostgreSQL 16 + pgvector on localhost:5433
cp .env.example backend/.env                 # set LOCAL_AUTH_SECRET (openssl rand -hex 32)

cd backend
uv sync
uv run alembic upgrade head                  # schema is managed only by ORM models + migrations
uv run python -m scripts.ingest              # fetch, chunk, embed, store (idempotent)
uv run uvicorn app.main:app --port 8080

cd ../frontend                               # new terminal
cp ../.env.example .env.local                # use the frontend block: same LOCAL_AUTH_SECRET,
npm install && npm run dev                   # plus LOCAL_DEMO_USERS for an advisor and an admin
```

Open http://localhost:3000 and sign in with a `LOCAL_DEMO_USERS` account. To use real models, set `EMBEDDING_PROVIDER=bedrock` and `LLM_PROVIDER=bedrock` with an AWS profile, run `uv run python -m scripts.bedrock_smoke` first, then re-ingest.

## Tests and evaluation (results as actually run)

| Suite | Command | Result |
|---|---|---|
| Backend unit + integration (real Postgres/pgvector, migrations applied) | `cd backend && uv run pytest` | **94 passed** |
| Static checks | `ruff check`, `ruff format --check`, `mypy --strict` | clean (83 files) |
| Frontend e2e (Playwright, desktop + mobile; backend stubbed) | `cd frontend && npm run test:e2e` | **16 passed** |
| Frontend static | `npm run lint && npm run typecheck && npm run build` | clean |
| Infrastructure | `terraform validate`, `tflint` | clean |

What the tests pin down:
- Each refusal path, with no model call made.
- Fabricated passage IDs and fabricated quotes are never shown.
- The disclaimer appears on every outcome.
- Bedrock errors and malformed output fail safely, and the fallback model is used only on error.
- JWT attacks are rejected: expired, wrong audience or issuer, access token instead of ID token, foreign key, `alg=none`, and HS256 key confusion.
- Every endpoint returns 401 without a token; advisors get 404 on each other's conversations and 403 on the audit log.
- Rate limiting, append-only enforcement, idempotent re-ingestion, and migration up/down/drift.

**RAG eval:** 24 questions across 1031, DST, 721/REIT, insufficient evidence, out-of-scope, personalised advice and prompt injection ([backend/eval](backend/eval)).

| Run | Result |
|---|---|
| Local stand-ins (lexical embedder + extractive generator), similarity floor 0.20 | **22/24** |
| Same, floor 0.30 | 18/24 |
| Titan + Nova Lite / DeepSeek V3.2 | **pending**: blocked by the AWS account restriction |

- **Passing at 0.20:** every policy category (advice 3/3, injection 2/2, out-of-scope 2/2), and every response carried the disclaimer.
- **The two misses** are "insufficient evidence" questions. Retrieval found related passages, and the extractive stand-in can't judge relevance. This is evidence that a similarity floor alone is not enough, and why the real model's `answerable` flag plus citation verification are the second layer.
- **These local runs** test the pipeline and policies, not LLM answer quality.

## Security decisions

- **Server-side identity:** Cognito ID tokens are verified server-side on every request: RS256 pinned, plus issuer, audience, expiry and token type. The role comes only from the `admin` Cognito group, and the request schema rejects client-supplied identity fields.
- **Browser tokens:** tokens never reach browser JavaScript. Next.js keeps them in httpOnly, SameSite=Lax cookies and calls the API server-to-server through an allow-listed proxy with Origin and JSON checks (CSRF).
- **Ownership:** it is part of every conversation query, so another advisor's conversation returns 404.
- **Least privilege:**
  - IAM can invoke only the three approved models.
  - The database role can read the corpus and only *append* exchanges.
  - Secrets live in SSM SecureString, read at cold start. The API has no plaintext credentials in env vars or git.
- **Rate limits and caps:** a per-user rate limit (6/min, 60/hour) plus API Gateway throttling. Questions are capped at 1,000 characters, retrieval at 6 passages, and output at 900 tokens.
- **Error responses:** never echo input or internals, and every response carries a request ID that matches the structured logs.

## Known limitations

- **No deployment yet**, and therefore no Bedrock-quality eval numbers (see above).
- **Each question is answered independently.** Follow-ups such as "and for a DST?" don't use earlier turns for retrieval.
- **Citation verification proves traceability, not truth.** A real quote can still be paraphrased wrongly. Admin review of the audit log covers that.
- **The regex policy rules** can miss novel phrasing or occasionally over-refuse. They're centralised and table-tested.
- **The corpus is a 13-document snapshot.** There is no scheduled re-fetch or change alerting, and statute text is dense.
- **Public RDS endpoint** (TLS plus a generated password) to stay on the free tier. Private networking is the production path.
- **The rate limiter isn't atomic** under concurrent bursts, which is acceptable for a pilot.

## What I'd build next

1. Deploy, run the Bedrock eval on Nova Lite and DeepSeek V3.2, and calibrate the Titan similarity floor from the results.
2. Conversation-aware query rewriting for follow-ups, still answered statelessly against the corpus.
3. An approved-document admin workflow: upload, compliance sign-off, versioning and re-ingest. Real use needs Sightbridge's own PPMs and FAQs, not only public sources.
4. Hybrid retrieval (BM25 plus vectors) if the eval shows recall misses on exact terms such as "45-day".
5. Private networking, SSO for advisors (broker-dealer IdPs), and alarms on refusal and failure rates.

## Project layout

```text
backend/   FastAPI · SQLAlchemy 2 + Alembic · LangChain/langchain-aws · pytest · eval/ · scripts/
frontend/  Next.js 15 (App Router) · Tailwind v4 · shadcn/ui · Playwright
infra/     Terraform (Cognito, RDS, Lambda, HTTP API, S3, SSM, Amplify, Budgets)
docs/      architecture · decisions · deployment · memo
```
