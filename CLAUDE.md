# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

`AGENTS.md` (imported above) is the authoritative contract: mandatory `log.txt` logging (§2, §5), the dataset/output contract (§6), and the submission-link rule (§4.1). The notes below add only what is not stated there.

## Logging values for this harness

Every `SESSION START` and per-turn entry in `log.txt` must use `tool=Claude Code` and the exact runtime model ID given in the system prompt (for example `model=claude-opus-5-5`). Append only, never rewrite earlier entries.

## Current state

The repo is a starter: `code/main.py` and `code/evaluation/main.py` are empty. The solution (the deletion-audit agent) and the evaluation harness have not been built yet. `dataset/output.csv` is a blank template; the generated `output.csv` goes at the repo root.

## Commands (Windows environment)

- Use `python`, not `python3` (`python3` resolves to the Microsoft Store stub here). Python 3.13 is installed.
- Run the solution: `python code/main.py` (writes `./output.csv`).
- There is no `sqlite3` CLI. Load `dataset/evidence/databases/*.sql` with Python's `sqlite3` module (`conn.executescript(open(path).read())` into an in-memory DB).
- Package: `Compress-Archive -Path README.md, code -DestinationPath code.zip -Force` (PowerShell). Remove `code/__pycache__` first; never include `dataset/` or nested archives.

## Things that span multiple files

- **Evaluation report location conflict:** `AGENTS.md`/`problem_statement.md` say `evaluation/…`, but `README.md` says `code/evaluation/evaluation_report.md` and `code/evaluation/usage_report.md` so they get bundled in `code.zip`. Follow the README layout.
- **Step ordering** (from `problem_statement.md`): phase order FINDING → REMEDIATION → VERIFICATION; within a phase, follow the operation order of the Allowed Operations table, then `system_id`, then `resource_id` ascending. Remediation still must disable flows and block restores before changing records.
- **Serialization:** `dataset/sample_output.csv` serializes each step with keys sorted alphabetically and no whitespace (`json.dumps(steps, sort_keys=True, separators=(",", ":"))`). Use it as the dev/eval set: 32 cases with answers for `dataset/sample_deletion_requests.csv`.
- **Policy mapping:** `dataset/deletion_rules.csv` maps a `data_category` (+ condition) to DELETE / REDACT / ANONYMIZE / RETAIN. RETAIN categories (security audit log within retention, legal hold, backup archive) produce `VERIFY_RECORD_PRESENT` rather than deletion; backups additionally need restore blocking. `ACCOUNT_TOMBSTONE` is REDACT (keep `user_id`, strip name/email/phone/email_hash).
- **Identity resolution:** the request `email` may be historical. Map it to `user_id` through `user_identities.csv` (current) plus `user_audit_log.csv` (old→new values for email/phone/name), then search evidence for all past and current identifiers, including `email_hash` (SHA-256).
- **Evidence linkage:** `evidence_files.csv` maps `file_id` → path (relative to `dataset/`) + `captured_at`. Files are global and include decoys. Link a file to a case only by its contents, and drop any evidence/activity/audit row timestamped after the case's `audit_at`. Evidence types: SQL snapshots (primary/regional/read/reporting/failover replicas, so compare them for drift), logs, JSON/text records, PDFs and PNG screenshots (need text extraction / OCR or a vision model).
- **`system_activity.csv`** is a global event stream (sync, export, backup, restore, index, delete). `reported_result=SUCCESS` is a claim, not proof. Active sync/import/restore events after deletion are what drive `REPORT_ACTIVE_DATA_FLOW` / `REPORT_RESTORE_RISK`.
- Model-based steps must record provider, model, call counts, and tokens so `usage_report.md` can be written from the final full run. Read API keys from env vars or an uncommitted `.env`.
