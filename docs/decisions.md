# Decision log

Short records of decisions that shaped the build. Format: decision, reason, alternatives, tradeoff.

### D1. Next.js 15.5 rather than 16
- **Reason:** AWS Amplify Hosting documents SSR support for Next.js 12–15. Next 16 support was not confirmed at build time.
- **Alternatives:** Next 16 on Amplify (unverified), static export to S3/CloudFront (loses server-side session handling).
- **Tradeoff:** Misses Next 16 features we don't need. A bundled PostCSS advisory is resolved with an npm `overrides` pin.

### D2. Backend-for-frontend session handling
- **Reason:** The Next.js server signs in to Cognito and keeps tokens in `httpOnly`, `Secure`, `SameSite=Lax` cookies, then proxies API calls server-to-server. Tokens never reach browser JavaScript, and the API needs no browser CORS.
- **Alternatives:** Amplify JS client SDK storing tokens in localStorage (exposed to XSS), Cognito Hosted UI (adds redirect flow, less on-brand).
- **Tradeoff:** One extra network hop per request. The FastAPI backend still independently verifies every JWT.

### D3. Synchronous SQLAlchemy + psycopg
- **Reason:** Bedrock (boto3) is synchronous, and Lambda serves one request per instance, so async gains nothing and adds complexity.
- **Alternatives:** async SQLAlchemy with psycopg async.
- **Tradeoff:** On a long-running multi-worker server we would revisit this. FastAPI runs sync routes in its threadpool.

### D4. Schema managed only by ORM models + Alembic
- **Reason:** One source of truth for the schema, with reviewed and reversible migrations. Tests apply the real migration chain (`alembic upgrade head`) rather than `create_all()`, so migration drift fails CI.
- **Alternatives:** raw SQL DDL, `metadata.create_all()`.
- **Tradeoff:** Each schema change needs a migration file.

### D5. Local ports 5433 (Postgres) and 8080 (API)
- **Reason:** Avoids clashing with a host Postgres on 5432.

### D6. Curated 13-source corpus, mostly primary sources
- **Reason:** IRS guidance, the statute and regulation text (26 USC §1031, §721, 26 CFR 1.1031(k)-1), Rev. Rul. 2004-86, SEC Investor.gov and FINRA cover 1031, DST and regulatory context authoritatively. Two reputable explainers fill gaps the primary sources leave: Franklin Templeton on UPREIT mechanics, and Kiplinger on DST risks.
- **Excluded:** IRS FS-2008-18. It predates the 2017 TCJA, which limited §1031 to real property, so it would ground wrong answers. Sponsor and broker marketing blogs are excluded as not neutral.
- **Tradeoff:** Statute and regulation text is dense, so answers lean on IRS/SEC plain-language pages where they exist. There is no primary-source DST risk guidance, so Kiplinger fills that gap.

### D7. Deterministic local embedder for development and tests
- **Reason:** No AWS access was approved for development. A hashed bag-of-words embedder at the same 1024 dimensions exercises the real schema, ingestion and pgvector code paths. Every chunk records its `embedding_model`, and retrieval filters on it, so vectors from different models can never be compared.
- **Alternatives:** Mock the retriever entirely (leaves pgvector untested); a local neural model (heavy dependency, still not Titan).
- **Tradeoff:** It measures keyword overlap, not meaning. Retrieval thresholds must be recalibrated on Titan V2, and production config rejects `EMBEDDING_PROVIDER=local`.

### D8. Chunking: 1200 chars, 200 overlap, never across a page or heading
- **Reason:** Each chunk has exactly one citable location (PDF page or HTML section). Overlap preserves sentence context at boundaries. Sections under 300 chars are merged forward to avoid fragment chunks. A short "title — section" header is embedded with each chunk but not stored in its text, so quote verification runs against the original text.
- **Idempotency:** `ingest_signature = sha256(content_hash | chunker version | embedding model)`. Unchanged sources are skipped, and any change atomically replaces that document's chunks in one transaction.
