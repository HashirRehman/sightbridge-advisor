# RAG evaluation

The 24 questions cover 1031, DST, 721/REIT, regulatory information, missing evidence,
personalized advice, out-of-scope requests and prompt injection.

An automated pass requires the expected status, refusal reason where specified, at least
one expected source for answered items, and the fixed disclaimer. This is a behavioral and
source-selection check, not an independent factual-quality rating. Review the saved answer
text and supporting quotes for completeness and faithful paraphrasing.

## Real-model results (28 Oct 2026)

| Configuration | Score | Notes |
|---|---|---|
| Titan + Nova Lite; floor 0.30; abbreviation expansion | 22/24 | Eight-second generation budget |
| Titan + Nova Lite; floor 0.40; abbreviation expansion | **23/24** | Selected deployment setting; all refusal and evidence-gap categories pass |
| Titan + DeepSeek V3.2; floor 0.30; abbreviation expansion | 15/24 | Twenty-second generation budget; nine answers rejected by citation verification |

The remaining Nova miss asks whether equipment/personal property qualifies after 2017.
The pipeline returned unsupported despite relevant corpus material. This is conservative
under-answering, not evidence that the question is outside the corpus. The 24-question set
also informed calibration; there is no separate held-out benchmark.

Reports are saved in `results/titan-nova-sim-0.30-expanded.json`,
`results/titan-nova-strict-sim-0.40-expanded.json` and
`results/titan-deepseek-sim-0.30-expanded.json`. They retain questions, rendered answers,
structured claims, citation quotes/links, actual model IDs, latency and separate input/output
token counts. Counts cover successful returned generations, not failed attempts.

## Reproduce

First ingest using Titan; lexical-development vectors cannot be compared with Titan queries.
Configure AWS_REGION=ap-southeast-2 and the runtime secret privately, then from backend:

```bash
EMBEDDING_PROVIDER=bedrock uv run python -m scripts.ingest
RETRIEVAL_MIN_SIMILARITY=0.40 EMBEDDING_PROVIDER=bedrock uv run python -m scripts.run_eval --chat-model apac.amazon.nova-lite-v1:0 --label titan-nova-recheck
RETRIEVAL_MIN_SIMILARITY=0.30 GENERATION_TIMEOUT_SECONDS=20 EMBEDDING_PROVIDER=bedrock uv run python -m scripts.run_eval --chat-model deepseek.v3.2 --label titan-deepseek-recheck
```

`--chat-model` forces Bedrock generation and disables fallback for isolated comparisons.
Every report is written even when cases fail; any failed case returns exit code 1. Model
responses can vary between runs even at temperature zero. Historical local stand-in reports
remain unchanged: 22/24 at floor 0.20 and 18/24 at 0.30; those did not measure LLM quality.

### Sydney embedding comparison

Run `uv run python -m scripts.compare_embeddings` against the local Titan corpus with a
Sydney Bedrock key configured. It embeds the same chunks with Cohere English v3 in memory,
uses `search_document`/`search_query` input types and reports expected-document ranks without
writing database vectors. Both models matched an expected source in their first result for
14/14 answerable questions; median query times were about 0.61 seconds. This small existing
set does not distinguish the models and does not measure passage entailment or answer quality.
Models need independent similarity calibration before any rollout. The report is
[here](results/sydney-embedding-comparison.json).
