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

## Custom evaluation cases

To be added (milestone 9). Planned: non-English non-deletion request, historical-email-only request, post-`audit_at` evidence that must be ignored, replica-only residue, look-alike decoy email.

## Failures found and improvements made

To be filled as milestones land.
