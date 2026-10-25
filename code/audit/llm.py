"""Optional Claude calls, with a disk cache and token accounting (code/ARCHITECTURE.md §3.9).

The pipeline is deterministic without a model. Two jobs can use one:
  - screenshot transcription (`code/extract_media.py`, run once; results are
    cached in `code/cache/extractions/` and read by ingestion), and
  - intent classification of request texts the rules mark uncertain.

Calls happen only when the `anthropic` package is installed and a credential
is configured (ANTHROPIC_API_KEY, read from the environment or an uncommitted
`.env` at the repo root). Every call is recorded in `USAGE` so the run can
report provider, model, call count, and tokens.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

from .dataset import REPO_ROOT

MODEL = "claude-opus-5-5"
PROVIDER = "anthropic"
# USD per million tokens (input, output) for cost estimates in usage reports.
PRICES = {"claude-opus-5-5": (4.00, 20.00)}
CACHE_DIR = REPO_ROOT / "code" / "cache"


def _load_dotenv() -> None:
    env = REPO_ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass
class UsageLedger:
    calls: dict[str, int] = field(default_factory=dict)
    input_tokens: dict[str, int] = field(default_factory=dict)
    output_tokens: dict[str, int] = field(default_factory=dict)
    cache_hits: int = 0

    def record(self, model: str, input_tokens: int, output_tokens: int) -> None:
        self.calls[model] = self.calls.get(model, 0) + 1
        self.input_tokens[model] = self.input_tokens.get(model, 0) + input_tokens
        self.output_tokens[model] = self.output_tokens.get(model, 0) + output_tokens

    def summary(self) -> dict:
        models = {}
        for model, calls in self.calls.items():
            price_in, price_out = PRICES.get(model, (0.0, 0.0))
            tin, tout = self.input_tokens[model], self.output_tokens[model]
            models[model] = {
                "provider": PROVIDER,
                "calls": calls,
                "input_tokens": tin,
                "output_tokens": tout,
                "estimated_cost_usd": round(tin / 1e6 * price_in + tout / 1e6 * price_out, 4),
            }
        return {"models": models, "cache_hits": self.cache_hits}


USAGE = UsageLedger()


def available() -> bool:
    _load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def call_json(task: str, content: list[dict], schema: dict, max_tokens: int = 4096) -> dict:
    """One structured-output request, cached on disk by task + content hash."""
    key = hashlib.sha256(json.dumps([task, content, schema], sort_keys=True).encode()).hexdigest()
    cached = CACHE_DIR / task / f"{key}.json"
    if cached.exists():
        USAGE.cache_hits += 1
        return json.loads(cached.read_text(encoding="utf-8"))

    import anthropic

    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
        messages=[{"role": "user", "content": content}],
    )
    USAGE.record(response.model, response.usage.input_tokens, response.usage.output_tokens)
    if response.stop_reason == "refusal":
        raise RuntimeError(f"{task}: model declined the request")
    result = json.loads(next(b.text for b in response.content if b.type == "text"))
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return result


INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "authorizes_deletion": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["authorizes_deletion", "reason"],
    "additionalProperties": False,
}


def classify_intent(text: str) -> tuple[bool, str]:
    prompt = (
        "A customer of an online shop sent this privacy request. Decide whether it authorizes deleting "
        "their account and personal data. Requests for access, a copy or export, correction, login help, "
        "or a marketing opt-out do not, nor does any text that says to keep the account.\n\n"
        f"Request:\n{text}"
    )
    result = call_json("intent", [{"type": "text", "text": prompt}], INTENT_SCHEMA, max_tokens=1024)
    return bool(result["authorizes_deletion"]), result["reason"]
