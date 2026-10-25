"""Hand-built evaluation cases (run with `python code/evaluation/main.py --custom`).

Each case is a controlled variant of a labelled sample: one input is changed
and the expected outcome follows from the contract, not from running the
pipeline. The variant replaces its base request in the run, so deletion
bursts shared with other cases are assigned exactly as in the real data.

expect:
  "gold"                     the base case's answer in sample_output.csv still holds
  {"status": ..., "steps": [...]}  an explicit expected answer
checks (optional), properties every answer must satisfy:
  "no_late_evidence"         no step cites evidence captured after audit_at
  ("never_cites", ids)       none of these evidence ids is cited
  ("never_mentions", ids)    none of these resources appears in any step
"""

CUSTOM_CASES = [
    {
        "id": "custom_01_spanish_access_request",
        "base": "case_05",
        "changes": {
            "request_text": "Quisiera recibir una copia de los datos de mi cuenta. No eliminen ni cierren la cuenta, "
            "la sigo usando."
        },
        "expect": {"status": "NOT_APPLICABLE", "steps": []},
        "why": "Non-English request for a copy of the data that explicitly keeps the account: no deletion is "
        "authorized, even though the same person has an incomplete deletion on record.",
    },
    {
        "id": "custom_02_negated_delete_word",
        "base": "case_12",
        "changes": {
            "request_text": "Please do not delete anything. I only want the phone number on my profile updated "
            "to my new one."
        },
        "expect": {"status": "NOT_APPLICABLE", "steps": []},
        "why": "Contains the word 'delete' but negates it and asks for a correction.",
    },
    {
        "id": "custom_03_old_email_only",
        "base": "case_12",
        "changes": {"email": "minjun.kim90@gmail.com"},
        "expect": "gold",
        "checks": ["no_late_evidence"],
        "why": "The request arrives from the email user_12 replaced in May (audit_282). Identity resolution "
        "must reach the same user, so the answer (including the record keyed by the old email hash) is unchanged.",
    },
    {
        "id": "custom_04_intermediate_historical_email",
        "base": "case_10",
        "changes": {"email": "nadia.petrescu@yahoo.com"},
        "expect": "gold",
        "why": "user_10 changed email twice (audit_121, audit_238); the middle address must resolve too.",
    },
    {
        "id": "custom_05_email_case_and_spacing",
        "base": "case_28",
        "changes": {"email": "  SMARTINEZ6461@GMAIL.COM "},
        "expect": "gold",
        "checks": [("never_mentions", ["search_8168"])],
        "why": "Upper-case, padded email must normalize to the same user; the look-alike "
        "smartinez6461.jr@gmail.com search document (search_8168) belongs to someone else and must stay out.",
    },
    {
        "id": "custom_06_evidence_after_audit_ignored",
        "base": "case_31",
        "changes": {"audit_at": "2026-09-15T11:59:59Z"},
        "expect": None,
        "checks": ["no_late_evidence", ("never_cites", ["evidence_51"])],
        "why": "Moves audit_at one second before evidence_51, the only file proving case_31's state. That file "
        "must no longer be cited or change the answer.",
    },
]
