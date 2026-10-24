# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

@AGENTS.md

`AGENTS.md` (imported above) is the authoritative contract: mandatory `log.txt` logging (§2, §5), the dataset/output contract (§6), and the submission-link rule (§4.1). The notes below add only what is not stated there.

## Logging values for this harness

Every `SESSION START` and per-turn entry in `log.txt` must use `tool=Claude Code` and the exact runtime model ID given in the system prompt (for example `model=claude-opus-5-5`). Append only, never rewrite earlier entries.

## Current state

`code/ARCHITECTURE.md` is the design source of truth: an 8-stage pipeline (ingest → intent → identity → discovery → state at `audit_at` → policy → planner → emit), data findings from the 32 samples, the proposed code layout, the evaluation plan, and the milestone roadmap. Read it before writing solution code. Milestone 0 (contract skeleton) is done: `code/audit/contract.py` (operation tables, ordering, serialization), `dataset.py` (paths, loaders), `emit.py` (writer + contract validator). `audit_case` in `code/main.py` is still a placeholder that returns COMPLETE with no steps. `code/evaluation/main.py` is empty. `dataset/output.csv` is a blank template; the generated `output.csv` goes at the repo root.

## Commands (Windows environment)

- Use `python`, not `python3` (`python3` resolves to the Microsoft Store stub here). Python 3.13 is installed, with a virtualenv at the repo root: `.venv\Scripts\python.exe` (or activate it with `.venv\Scripts\Activate.ps1`). Keep `.venv` at the root, outside `code/`, so it stays out of `code.zip`.
- Run the solution: `python code/main.py` (writes `./output.csv` and validates it; exit code 1 on any contract violation). `--sample` runs the 32 sample requests into `./sample_predictions.csv`. `--validate FILE` only validates, e.g. `python code/main.py --sample --validate dataset/sample_output.csv` (the gold answers must stay valid).
- There is no `sqlite3` CLI. Load `dataset/evidence/databases/*.sql` with Python's `sqlite3` module (`conn.executescript(open(path).read())` into an in-memory DB).
- Package: `Compress-Archive -Path README.md, code -DestinationPath code.zip -Force` (PowerShell). `Compress-Archive` cannot exclude anything, so first remove every `__pycache__` under `code/` and make sure `code/` holds no `.zip/.tar/.gz/.tgz/.7z/.rar` files: a nested archive gets the submission rejected. Never include `dataset/`.

## Agent-trace submission

`agent_trace_instructions.md` describes a separate `agent-trace.zip` deliverable. For Claude Code: copy this project's session JSONL files from `%USERPROFILE%\.claude\projects\` plus a `/export` transcript of each session into `agent-trace/claude-code/`, redact secrets, then zip it.

## Things that span multiple files

- **Evaluation report location conflict:** `AGENTS.md`/`problem_statement.md` say `evaluation/…`, but `README.md` says `code/evaluation/evaluation_report.md` and `code/evaluation/usage_report.md` so they get bundled in `code.zip`. Follow the README layout.
- **Step ordering within a phase** (beyond `AGENTS.md` §6.3): follow the row order of the Allowed Operations table, then `system_id`, then `resource_id` ascending.
- **Serialization:** match `dataset/sample_output.csv` with `json.dumps(steps, sort_keys=True, separators=(",", ":"))`. Use the samples as the dev/eval set: 32 answered cases for `dataset/sample_deletion_requests.csv`.
- **Policy mapping:** `dataset/deletion_rules.csv` maps a `data_category` (+ condition) to DELETE / REDACT / ANONYMIZE / RETAIN. RETAIN categories (security audit log within retention, legal hold, backup archive) produce `VERIFY_RECORD_PRESENT` rather than deletion; backups additionally need restore blocking. `ACCOUNT_TOMBSTONE` is REDACT (keep `user_id`, strip name/email/phone/email_hash).
- **Identity resolution:** the request `email` may be historical. Map it to `user_id` through `user_identities.csv` (current) plus `user_audit_log.csv` (old→new values for email/phone/name), then search evidence for all past and current identifiers, including `email_hash` (SHA-256).
- **Evidence linkage:** `evidence_files.csv` maps `file_id` → path (relative to `dataset/`) + `captured_at`. Files are global and include decoys. Link a file to a case only by its contents, and drop any evidence/activity/audit row timestamped after the case's `audit_at`. Evidence types: SQL snapshots (primary/regional/read/reporting/failover replicas, so compare them for drift), logs, JSON/text records, PDFs and PNG screenshots (need text extraction / OCR or a vision model).
- **`system_activity.csv`** is a global event stream (sync, export, backup, restore, index, delete). `reported_result=SUCCESS` is a claim, not proof. Active sync/import/restore events after deletion are what drive `REPORT_ACTIVE_DATA_FLOW` / `REPORT_RESTORE_RISK`.
- Model-based steps must record provider, model, call counts, and tokens so `usage_report.md` can be written from the final full run. Read API keys from env vars or an uncommitted `.env`.
