# Ghost Data Still Exists: Deletion-Audit Agent

HackerRank Orchestrate (October 2026). For every request in `dataset/deletion_requests.csv`, the agent decides whether the person actually asked for deletion. For each deletion request it reconstructs their current and historical identifiers, finds every copy of their data in the supplied evidence, judges each record's state at the case's `audit_at`, applies `dataset/deletion_rules.csv`, and writes an ordered plan of findings, remediation steps and verification checks.

The full challenge contract is in [`problem_statement.md`](./problem_statement.md). The design is in [`code/ARCHITECTURE.md`](./code/ARCHITECTURE.md).

## Setup

Python 3.11 or newer (developed on 3.13).

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1            # macOS / Linux: source .venv/bin/activate
pip install -r code/requirements.txt  # pypdf
```

No API key is needed. Optional: `pip install anthropic` and set `ANTHROPIC_API_KEY` (environment variable or an uncommitted `.env` file at the repo root) to re-run the screenshot transcription or to let a model decide request texts the intent rules find ambiguous.

## Run

```powershell
python code/main.py
```

This reads `dataset/`, writes `output.csv` (518 rows, `case_id,deletion_status,steps_json`) at the repository root, validates it against the output contract, and exits non-zero on any violation. A full run takes about 2 seconds and makes no model calls unless a model is configured and a request text is ambiguous (none is in this dataset).

Other entry points:

| Command | Purpose |
|---|---|
| `python code/main.py --sample` | Run the 32 sample requests into `sample_predictions.csv` |
| `python code/main.py --validate FILE` | Validate any output file against the contract |
| `python code/main.py --explain case_12` | Show how a case was decided: intent, identity, linked resources and why, each resource's state, steps |
| `python code/main.py --debug-resource account_5684 --before 2026-09-10T00:00:00Z` | Everything the evidence store holds for a resource |
| `python code/main.py --debug-identifier someone@example.com` | Everything carrying an identifier |
| `python code/evaluation/main.py [--diff]` | Score the pipeline on the labelled samples |
| `python code/evaluation/main.py --custom` | Run the hand-built evaluation cases |
| `python code/evaluation/main.py --ingest-coverage` | Check that the evidence store reaches every gold resource and citation |
| `python code/extract_media.py` | Re-transcribe screenshots into `code/cache/extractions/` (needs `anthropic` and an API key) |

## How it works

```text
dataset/ -> ingest -> intent -> identity -> discovery -> state at audit_at -> policy -> planner -> output.csv
```

1. **Ingest** (`code/audit/ingest/`): every evidence file is parsed into one store. This covers SQL snapshots (typed inventories and primary/replica copy tables), JSON exports and bundles (one generic walker), the handover text file, six log layouts (with trace context carried from each lookup's start line to its result), PDFs, and screenshot transcriptions. Snapshots also record which tables they captured, so a record can be proven ABSENT by a snapshot that left it out.
2. **Intent** (`intent.py`): multilingual rules. A request is NOT_APPLICABLE when it asks for something else (a copy, a correction, login help, a marketing opt-out) or negates deletion. An optional model call settles texts the rules are unsure about.
3. **Identity** (`identity.py`): request email -> `user_id` through current identities plus the audit log of email/phone/hash changes, plus the SHA-256 hash of every email the user ever had.
4. **Discovery** (`discovery.py`): resources linked by any of the subject's identifiers in any evidence. Accounts and anonymized records are linked through the deletion worker's batch ("burst") that ran just before `reported_at`; bursts are shared by several cases, so these are assigned across all cases at once. A resource tied to someone else is never assigned.
5. **State** (`state.py`): each record's readings (absent, present, exposed, clean, active, disabled, restorable, blocked) from evidence captured by `audit_at` and observed after the workflow reported. The latest reading per copy wins, and the worst copy decides, so replica drift is caught.
6. **Policy** (`policy.py`): category -> DELETE / REDACT / ANONYMIZE / RETAIN from `deletion_rules.csv`, plus data flows and restore paths.
7. **Planner** (`planner.py`): status, findings citing the file that shows each violation, remediation, and verification steps. Steps are ordered by phase, then operation, then system and resource. COMPLETE answers cite the evidence proving each state.

Reported statuses and activity results are treated as claims and never as proof of state.

## Evaluation

On the 32 labelled samples: 100% status accuracy, 98.7% step F1, and 87.5% of cases exactly right (28/32). 6/6 hand-built custom cases pass. Details, metric definitions, failures found and limitations: [`code/evaluation/evaluation_report.md`](./code/evaluation/evaluation_report.md). Model usage for the final run: [`code/evaluation/usage_report.md`](./code/evaluation/usage_report.md).

## Audit report viewer (optional)

`app/streamlit_app.py` is a read-only Streamlit page over the generated `output.csv` and `dataset/`: per-case status, timestamps, an activity timeline, steps by phase with their cited evidence files, and an overview of all cases. PII is masked by default. It does not import the pipeline and is not part of `code.zip`.

```powershell
pip install -r app/requirements.txt   # streamlit, pandas, plotly
streamlit run app/streamlit_app.py
```

## Repository layout

```text
code/
  main.py                  CLI: run, validate, explain, debug
  extract_media.py         one-time screenshot transcription (optional, needs an API key)
  audit/                   the pipeline (contract, dataset, ingest/, identity, intent, discovery, state, policy, planner, emit, llm)
  cache/extractions/       cached screenshot transcriptions (input to ingestion)
  evaluation/              scorer, custom cases, evaluation_report.md, usage_report.md
  ARCHITECTURE.md          design, data findings, roadmap
app/                       optional Streamlit audit report viewer (not shipped in code.zip)
dataset/                   participant data (not shipped in code.zip)
output.csv                 generated predictions
```

## Packaging

```powershell
Get-ChildItem code -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
Compress-Archive -Path README.md, code -DestinationPath code.zip -Force
```

`code.zip` must not contain `dataset/`, virtual environments, nested archives, or secrets.

Submit at <https://www.hackerrank.com/contests/hackerrank-orchestrate-october26/challenges/ghost-data-still-exists>
