"""Identity resolution: request email -> user_id -> every current and historical identifier.

`user_identities.csv` holds each user's current identifiers; `user_audit_log.csv`
holds old -> new changes of email, email_hash, and phone. A request email may
be either current or historical. Email hashes are sha256 of the lower-cased
email, so the hash of every known email is added as well (hash-keyed records
written before an email change carry only the old hash).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache

from .dataset import DATASET_DIR, read_csv
from .observations import EMAIL, EMAIL_HASH, NAME, PHONE, USER, normalize_identifier

USER_IDENTITIES_CSV = DATASET_DIR / "user_identities.csv"
USER_AUDIT_LOG_CSV = DATASET_DIR / "user_audit_log.csv"

_FIELD_KINDS = {"email": EMAIL, "email_hash": EMAIL_HASH, "phone": PHONE}


def email_hash(email: str) -> str:
    return hashlib.sha256(normalize_identifier(EMAIL, email).encode()).hexdigest()


@dataclass(frozen=True)
class Identity:
    request_email: str
    user_id: str | None
    name: str | None
    identifiers: frozenset[tuple[str, str]]  # normalized (kind, value), names excluded
    historical: frozenset[tuple[str, str]]  # the subset that is no longer current
    audit_ids: tuple[str, ...]  # user_audit_log rows that contributed

    @property
    def resolved(self) -> bool:
        return self.user_id is not None


@dataclass(frozen=True)
class _Directory:
    current: dict[str, dict[str, str]]  # user_id -> identity row
    history: dict[str, list[dict[str, str]]]  # user_id -> audit rows
    owner: dict[tuple[str, str], set[str]]  # identifier -> user ids that ever held it


@lru_cache(maxsize=1)
def _directory() -> _Directory:
    current = {r["user_id"]: r for r in read_csv(USER_IDENTITIES_CSV)}
    history: dict[str, list[dict[str, str]]] = {}
    owner: dict[tuple[str, str], set[str]] = {}

    def own(kind: str, value: str, user_id: str) -> None:
        if value:
            owner.setdefault((kind, normalize_identifier(kind, value)), set()).add(user_id)
            if kind == EMAIL:
                owner.setdefault((EMAIL_HASH, email_hash(value)), set()).add(user_id)

    for user_id, row in current.items():
        for field, kind in _FIELD_KINDS.items():
            own(kind, row[field], user_id)
    for row in read_csv(USER_AUDIT_LOG_CSV):
        history.setdefault(row["user_id"], []).append(row)
        kind = _FIELD_KINDS[row["field_name"]]
        own(kind, row["old_value"], row["user_id"])
        own(kind, row["new_value"], row["user_id"])
    return _Directory(current, history, owner)


def owners_of(identifier: tuple[str, str]) -> set[str]:
    """User ids that ever held a normalized (kind, value) identifier."""
    if identifier[0] == USER:
        return {identifier[1]} if identifier[1] in _directory().current else set()
    return _directory().owner.get(identifier, set())


def resolve(request_email: str) -> Identity:
    directory = _directory()
    key = (EMAIL, normalize_identifier(EMAIL, request_email))
    owners = directory.owner.get(key, set())
    # An email held by one user is unambiguous. If several users ever held it,
    # prefer the user who holds it now.
    if len(owners) > 1:
        owners = {u for u in owners if normalize_identifier(EMAIL, directory.current[u]["email"]) == key[1]} or owners
    user_id = min(owners) if len(owners) == 1 else None
    if user_id is None:
        return Identity(request_email, None, None, frozenset({key, (EMAIL_HASH, email_hash(request_email))}), frozenset(), ())

    row = directory.current[user_id]
    now: set[tuple[str, str]] = {(USER, user_id)}
    for field, kind in _FIELD_KINDS.items():
        if row[field]:
            now.add((kind, normalize_identifier(kind, row[field])))
    past: set[tuple[str, str]] = set()
    audit_ids = []
    for change in directory.history.get(user_id, []):
        kind = _FIELD_KINDS[change["field_name"]]
        audit_ids.append(change["audit_id"])
        for value in (change["old_value"], change["new_value"]):
            if value:
                past.add((kind, normalize_identifier(kind, value)))
    emails = {v for k, v in now | past if k == EMAIL}
    past |= {(EMAIL_HASH, email_hash(e)) for e in emails}
    identifiers = frozenset(now | past)
    return Identity(
        request_email=request_email,
        user_id=user_id,
        name=normalize_identifier(NAME, row["name"]) if row["name"] else None,
        identifiers=identifiers,
        historical=frozenset(identifiers - now),
        audit_ids=tuple(audit_ids),
    )
