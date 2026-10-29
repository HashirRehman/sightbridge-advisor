# Production verification — 28 Oct 2026

Site: https://main.d4rzdcojhvk3v.amplifyapp.com

API health: https://uv91uglc6b.execute-api.ap-southeast-2.amazonaws.com/api/health

## Checks completed

- Terraform provisions the stack in Sydney; the final plan reports no changes needed.
- RDS contains 13 source documents and 284 Titan V2 chunks at 1024 dimensions.
- Health returns 200 with database status ok; unauthenticated chat returns 401.
- Both reviewer accounts sign in through the public website and Cognito.
- Session cookies are Secure, HttpOnly and SameSite=Lax.
- Cited 1031 and 721 answers render, citation links open, and conversation history survives reload.
- The live mobile view has no horizontal overflow at a 390-pixel viewport.
- Advisors receive 403 from the audit API and an access-required screen in the UI. Admins see the recorded exchanges.
- Another authenticated user receives 404 when reading or appending to an advisor's conversation.
- Prompt-injection and personalized-advice requests are refused, include the disclaimer and appear in the audit log.
- The audit trigger is enabled. The app database role has neither UPDATE nor DELETE permission on exchanges.
- The database connection uses TLS; a connection with SSL disabled is rejected.
- Four budget notifications use the approved recipient. The threshold is an alert, not a spending cap.
- Neither the model key nor the repository credential appears in Terraform state. Secret files are ignored by Git.

## Local checks

127 backend tests passed against PostgreSQL/pgvector. Ruff lint/format and strict mypy
passed. Frontend lint, typecheck, production build and 20 desktop/mobile Playwright tests
passed. Terraform formatting, validation and TFLint passed. The arm64 Lambda archive passed
its integrity check and ran successfully in AWS.

## Model evaluation and remaining limits

Nova Lite with Titan, abbreviation expansion and a 0.40 similarity floor passed 23/24
behavioral/source-selection cases. DeepSeek passed 15/24 at 0.30 with a 20-second allowance.
Every response carried the disclaimer. Full reports are linked in the eval README.

These scores do not establish complete factual correctness. The corpus contains support
for the post-2017 personal-property question, but Nova under-answered it. In a live browser
check, one 721-versus-1031 phrasing returned unsupported after citation verification failed;
a basic 721/UPREIT question returned a cited answer. Model output can vary between requests,
and the application discards unverifiable claims instead of showing them. Retrieval
calibration used the same small eval set; a held-out benchmark and human review remain next
steps. Prior conversation turns are not used to answer follow-ups.

Reviewer credentials are stored privately and are not part of this report or the repository.

## Follow-up verification

The capabilities question from review now returns `answered/capabilities` from the live API,
with a fixed scope explanation and disclaimer, without retrieval or model usage. Policy
precedence and persistence/audit behavior are covered by backend tests. Browser tests cover
long-thread scrolling with the composer visible, dropdown placement on desktop/mobile, and
icons on the audit question buttons.

A Sydney-only retrieval comparison used all 284 chunks and the 14 answerable evaluation
questions. Titan V2 and Cohere English v3 both retrieved an expected document at ranks 1, 3
and 6 for all 14 questions. Median query embedding times were 609 ms and 612.5 ms respectively.
This source-selection proxy does not establish equal answer quality or identify the best
embedding model; it supplies no demonstrated reason to migrate the existing Titan corpus.
No production embeddings changed. See `backend/eval/results/sydney-embedding-comparison.json`.

Backend CI/CD is implemented but inactive because the account's organization policy denies
IAM OIDC provider creation. Amplify frontend CD remains active. See the deployment guide.
