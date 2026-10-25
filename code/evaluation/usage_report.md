# Usage Report

Model usage for the final full-dataset run that produced `output.csv` (518 requests in `dataset/deletion_requests.csv`).

## Summary

The audit pipeline is deterministic: intent rules, identity resolution, discovery, state reconstruction, policy, and planning run without a model. The only model work is the one-time transcription of the 11 screenshot evidence files, whose results are cached in `code/cache/extractions/` and read by ingestion on every run.

| Stage | Provider | Model | Calls | Input tokens | Output tokens | Cost (USD) |
|---|---|---|---|---|---|---|
| Final full run (`python code/main.py`) | - | - | 0 | 0 | 0 | 0.00 |
| Screenshot transcription (one-time, cached) | Anthropic | `claude-opus-5-5` | 11 | ~33,000 (estimate) | ~6,600 (estimate) | ~0.26 (estimate) |
| Intent fallback for uncertain texts | Anthropic | `claude-opus-5-5` | 0 | 0 | 0 | 0.00 |
| **Overall** | | | **11** | **~33,000** | **~6,600** | **~0.26** |

The final run printed `model calls this run: 0 (cache hits 0)`: no request text was uncertain under the intent rules, and every screenshot was already in the extraction cache.

## Per request

| Measure | Final run only | Including the one-time transcription, spread over 518 requests |
|---|---|---|
| Total tokens | 0 | ~39,600 |
| Average tokens per request | 0 | ~76 |
| Estimated cost | $0.00 | ~$0.26 total, ~$0.0005 per request |

## How the numbers were obtained

- **Final run**: measured. `code/audit/llm.py` keeps a ledger (`USAGE`) of every model call, recording provider, model, call count, and `response.usage` input/output tokens. `code/main.py` prints it at the end of each run.
- **Screenshot transcription**: estimated. No `ANTHROPIC_API_KEY` was available in the development environment, so the 11 screenshots were transcribed once by the coding agent (Claude Code running `claude-opus-5-5`), viewing each image. The resulting JSON (screen text plus one record per resource row) is in `code/cache/extractions/`, keyed by each image's SHA-256, with the extractor recorded in each file. Token counts are estimated from image size (about 1800x1150 px, roughly 2,700 input tokens per image by the width x height / 750 rule) plus about 300 prompt tokens per image, and about 600 output tokens per transcription. Cost uses `claude-opus-5-5` list prices of $4 / $20 per million input / output tokens.
- **Reproducing the transcription through the API**: `python code/extract_media.py --force` (needs `pip install anthropic` and `ANTHROPIC_API_KEY` in the environment or an uncommitted `.env`). It sends each image once with a JSON-schema output format, caches the result in the same place, and prints the measured token usage.

## Cost controls

- Deterministic rules first; a model is called only for request texts the intent rules mark uncertain (none in this dataset) and for screenshots missing from the cache.
- Every model result is cached on disk by a hash of its input, so re-runs make no calls.
- Structured JSON output at `low` effort for both jobs, with the server-side refusal fallback enabled.
