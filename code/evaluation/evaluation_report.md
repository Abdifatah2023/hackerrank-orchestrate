# Evaluation Report

Status: in progress. This report is updated at every roadmap milestone (see `code/ARCHITECTURE.md` §6).

## How to reproduce

```powershell
python code/evaluation/main.py              # run the pipeline on the 32 samples and score it
python code/evaluation/main.py --diff       # plus a per-case diff of every imperfect case
python code/evaluation/main.py --markdown   # the tables below
python code/evaluation/main.py --pred FILE  # score an existing predictions CSV
```

The labelled set is `dataset/sample_deletion_requests.csv` with answers in `dataset/sample_output.csv`: 32 cases (22 INCOMPLETE, 10 COMPLETE, 0 NOT_APPLICABLE) and 193 gold steps.

## Metric definitions

| Metric | Definition |
|---|---|
| status accuracy | Share of cases whose `deletion_status` matches. |
| step precision / recall / F1 | Micro-averaged multiset match on exact `(phase, operation, system_id, resource_id, state, evidence_id)` tuples. `step_no` is excluded here and is covered by sequence accuracy. |
| field accuracy | Per field, over all gold steps. Each gold step is aligned to at most one predicted step (strictest key first: full tuple, then operation + resource, then phase + resource, then resource only). An unaligned gold step counts as wrong for every field. |
| sequence accuracy | Share of cases whose whole `steps_json` array matches exactly, including order and `step_no`. |
| order score | Mean over cases of LCS(gold, pred) / max(len) on step tuples in emitted order. Gives partial credit for ordering. |
| case exact | Share of cases where status and the full step sequence both match. |

Error buckets (from the alignment): **missed resource** (gold resource absent from the prediction), **missing step** (resource predicted, but this step is not), **extra resource** (predicted resource not in gold, usually a decoy), **extra step**, **wrong treatment** (operation/state/phase differs on an aligned step), **wrong evidence** (only `evidence_id` differs), **ordering** (same steps, different order), **wrong status**, **missing case**.

The harness was checked two ways: scoring `sample_output.csv` against itself gives 100% on every metric, and a mutated copy with one injected error per bucket is reported with exactly those buckets.

## Results by milestone

### Milestone 1: baseline (contract skeleton, every case COMPLETE with `[]`)

Cases: 32, gold steps: 193, predicted steps: 0

| Metric | Value |
|---|---|
| status accuracy | 31.25% |
| step precision | 0.00% |
| step recall | 0.00% |
| step F1 | 0.00% |
| field accuracy (all fields) | 0.00% |
| sequence accuracy | 0.00% |
| order score (mean LCS ratio) | 0.00% |
| case exact | 0.00% |

| Error bucket | Count |
|---|---|
| missed resource | 193 |
| wrong status | 22 |

The 31.25% status accuracy is the 10/32 COMPLETE majority-class floor.

### Milestone 2: evidence ingestion

Ingestion does not change predictions yet (the pipeline is still the placeholder), so the scores above are unchanged. Its exit criterion is reachability, checked with `python code/evaluation/main.py --ingest-coverage`:

| Check | Result |
|---|---|
| Evidence store | 3,158 observations, 371 snapshot coverages, 4,011 activities, 15 documents (11 screenshots awaiting extraction); every log line parsed (1,972/1,972) |
| Gold resources found in at least one observation, activity, or document | 108/111 |
| Gold evidence citations reachable (an item naming the resource, or a snapshot covering its system) | 75/75 |

The 3 unreachable resources (`account_004` in case_04, `audit_record_5569` in case_20, `audit_record_6645` in case_18) do not occur in any participant-facing file, in any format; the two screenshots most likely to hold them were checked by eye. They must be produced by policy rules (every case verifies its account and its retained audit record), not by evidence lookup.

Failures found while building ingestion:

- **Absence is evidence.** The first version only stored rows, so 1 gold citation (case_31 → `evidence_51`) was unreachable: that snapshot proves `account_4857` absent by listing an empty `customer_profiles` collection. Fixed by emitting `Coverage` for JSON snapshot collections as well as SQL tables (74/75 → 75/75).
- **Phones outside phone fields.** PII scans put phone numbers in `scan_key` / `matched_value`; identifier extraction only looked at fields named `*phone*`. Fixed by recognizing `+`-prefixed numbers in any field.
- **Missing dependency.** `pypdf` was assumed installed but was absent from both interpreters; it is now pinned in `code/requirements.txt`.

## Custom evaluation cases

To be added (milestone 9). Planned: non-English non-deletion request, historical-email-only request, post-`audit_at` evidence that must be ignored, replica-only residue, look-alike decoy email.

## Failures found and improvements made

To be filled as milestones land.
