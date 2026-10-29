# RAG evaluation

`questions.json` has 24 questions across seven categories: answerable 1031, DST and 721/REIT questions; questions with insufficient evidence; out-of-scope questions; personalised advice; and prompt injection. Each has an expected outcome. Answerable items also list the sources a correct answer should cite.

```bash
uv run python -m scripts.run_eval --label <name>   # writes results/<name>.json
```

An item passes when:
- the status matches;
- the refusal reason matches, where one is specified;
- answered items cite at least one expected source.

The disclaimer is checked on every response.

## Results so far

| Run | Embeddings / generator | Passed | Notes |
|---|---|---|---|
| `local-sim-0.30` | local lexical / local extractive | 18/24 | Floor too high for lexical scores; answerable questions fall to "unsupported" |
| `local-sim-0.20` | local lexical / local extractive | 22/24 | All policy categories pass. Two insufficient-evidence questions are "answered" because the extractive stand-in can't judge relevance |
| `local-sim-0.15`, `local-sim-0.25` | local / local | 21/24, 19/24 | Threshold sweep |

The local runs test **policy, retrieval plumbing, verification and disclaimers**. They say nothing about LLM answer quality. Bedrock (Titan V2 + Claude Haiku) results will be added once AWS access is approved. A similarity floor alone can't separate related-but-unanswerable questions from answerable ones; that is the job of the model's `answerable` flag plus citation verification (see docs/decisions.md D9).
