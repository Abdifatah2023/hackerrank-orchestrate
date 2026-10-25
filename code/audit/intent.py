"""Does the request text authorize deletion? (code/ARCHITECTURE.md §3.2)

Deterministic, multilingual rules. A request is NOT_APPLICABLE when it asks
for something other than deletion (access, copy, correction, login help,
marketing opt-out) or explicitly keeps the account / negates deletion. Note
that genuine deletion requests may say what can be *kept* ("You can keep
anonymous statistics", "Security records can stay"), so keep-signals are
phrased around the account itself.

`classify` returns an `Intent(authorized, reason, certain)`. Texts that match neither side are
treated as deletion requests when they mention the account at all, and are
flagged as uncertain so they can be reviewed (or sent to a model, see
`code/audit/llm.py`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DELETION_TERMS = [
    r"\bdelet(e|ed|ing|ion)\b",
    r"\beras(e|ed|ure)\b",
    r"\bremov\w*\b.{0,40}\b(account|profile|data|records?|details|information|result|cop(y|ies)|file)",
    r"\b(account|profile)\b.{0,40}\b(been removed|is gone)\b",
    r"\b(close|closed|closing)\b.{0,20}\baccount",
    r"\baccount-deletion\b",
    r"\bsuppr(imer|ession)\b",  # fr
    r"\belimina(r|ción|cion)\b",  # es
    r"\bexclus[ãa]o\b|\bexcluir\b|\bapagar\b",  # pt
    r"\bl[öo]sch(en|ung)\b",  # de
    r"削除",  # ja
    r"삭제",  # ko
    r"删除|注销",  # zh
]

# A deletion word under negation ("not asking for deletion", "don't delete",
# "without removing", "do not close").
NEGATED_DELETION = re.compile(
    r"\b(not|n't|never|without|no)\b[^.!?]{0,40}?\b(delet\w*|eras\w*|remov\w*|clos\w*)",
    re.IGNORECASE,
)

KEEP_ACCOUNT = [
    r"\bleave (the|my) (account|profile)\b",
    r"\bkeep (the|my) account\b",
    r"\b(still|continue to) (need|use|want)\b",
    r"\bcontinue using\b",
    r"\bleave .{0,30}\b(active|in place)\b",
    r"\bmarketing lists only\b",
]

NON_DELETION_REQUESTS = [
    r"\bcopy of\b",
    r"\bdownload my\b",
    r"\bportable format\b",
    r"\bwhat personal information\b",
    r"\binformation request\b",
    r"\breset(ting)? the login\b",
    r"\bmisspelled\b|\bcorrect it\b",
    r"\b(change|update|replace) (the|my) (email|phone|mobile|address|name)\b",
    r"\bout of date\b",
    r"\bstop the promotional\b|\bunsubscribe\b|\bopt[- ]out\b",
    r"\binvoices?\b",
]


@dataclass(frozen=True)
class Intent:
    authorized: bool
    reason: str
    certain: bool = True


def _any(patterns: list[str], text: str) -> str | None:
    for p in patterns:
        if m := re.search(p, text, re.IGNORECASE):
            return m.group(0)
    return None


def classify(text: str) -> Intent:
    if m := NEGATED_DELETION.search(text):
        return Intent(False, f"negated deletion: {m.group(0)!r}")
    if hit := _any(KEEP_ACCOUNT, text):
        return Intent(False, f"keeps the account: {hit!r}")
    deletion = _any(DELETION_TERMS, text)
    other = _any(NON_DELETION_REQUESTS, text)
    if deletion:
        return Intent(True, f"deletion request: {deletion!r}")
    if other:
        return Intent(False, f"non-deletion request: {other!r}")
    if re.search(r"\baccount|\bprofile|\bdata\b", text, re.IGNORECASE):
        return Intent(True, "no explicit intent; account mentioned", certain=False)
    return Intent(False, "no deletion intent found", certain=False)
