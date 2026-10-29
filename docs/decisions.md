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
