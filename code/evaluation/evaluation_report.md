# Evaluation Report

Deletion-audit agent for **Ghost Data Still Exists**. All numbers below were produced by the harness in this folder and can be reproduced with the commands in the first section.

## How to reproduce

```powershell
python code/evaluation/main.py                    # run the pipeline on the 32 samples and score it
python code/evaluation/main.py --diff             # plus a per-case diff of every imperfect case
python code/evaluation/main.py --markdown         # the metric tables below
python code/evaluation/main.py --custom --diff    # the hand-built cases (custom_cases.py)
python code/evaluation/main.py --ingest-coverage  # can the evidence store reach every gold resource / citation?
python code/evaluation/main.py --pred FILE        # score any predictions CSV
```

Labelled set: `dataset/sample_deletion_requests.csv` with answers in `dataset/sample_output.csv`, 32 cases (22 INCOMPLETE, 10 COMPLETE, 0 NOT_APPLICABLE), 193 gold steps. The samples were used for development, so the scores measure fit to the samples, not held-out accuracy (there is no separate held-out labelled set). The custom cases and the sanity checks on the 518 evaluation requests are the out-of-sample checks.

## Metric definitions

| Metric | Definition |
|---|---|
| status accuracy | Share of cases whose `deletion_status` matches. |
| step precision / recall / F1 | Micro-averaged multiset match on exact `(phase, operation, system_id, resource_id, state, evidence_id)` tuples. `step_no` is left out here and covered by sequence accuracy. |
| field accuracy | Per field, over all gold steps. Each gold step is aligned to at most one predicted step (strictest key first: full tuple, then operation + resource, then phase + resource, then resource only). An unaligned gold step counts as wrong for every field. |
| sequence accuracy | Share of cases whose whole `steps_json` array matches exactly, including order and `step_no`. |
| order score | Mean over cases of LCS(gold, pred) / max(len) on step tuples in emitted order. Partial credit for ordering. |
| case exact | Share of cases where status and the full step sequence both match. |

Error buckets come from the alignment: **missed resource** (gold resource absent from the prediction), **missing step** (resource predicted, this step not), **extra resource** (predicted resource not in gold, usually a decoy), **extra step**, **wrong treatment** (operation / state / phase differs), **wrong evidence** (only `evidence_id` differs), **ordering**, **wrong status**, **missing case**.

The harness was validated two ways: scoring `sample_output.csv` against itself gives 100% on every metric, and a copy of it with one injected error per bucket is reported with exactly those buckets.

## Final results (sample set)

Cases: 32, gold steps: 193, predicted steps: 190

| Metric | Value |
|---|---|
| status accuracy | 100.00% |
| step precision | 99.47% |
| step recall | 97.93% |
| step F1 | 98.69% |
| field accuracy: step_no | 95.34% |
| field accuracy: phase | 97.93% |
| field accuracy: system_id | 97.93% |
| field accuracy: resource_id | 97.93% |
| field accuracy: operation | 97.93% |
| field accuracy: state | 97.93% |
| field accuracy: evidence_id | 97.93% |
| sequence accuracy | 87.50% |
| order score (mean LCS ratio) | 97.43% |
| case exact | 87.50% |

| Error bucket | Count |
|---|---|
| missed resource | 4 |
| extra resource | 1 |

Status confusion: COMPLETE -> COMPLETE 10, INCOMPLETE -> INCOMPLETE 22.

The 4 remaining misses in 4 cases:

| Case | Gold step not produced | Why |
|---|---|---|
| case_04 | `VERIFY_RECORD_ABSENT account_service/account_004` | `account_004` appears in no participant file in any form (checked text, SQL, logs, PDFs, and every screenshot). |
| case_18 | `VERIFY_RECORD_PRESENT security_service/audit_record_6645` | Same: the id occurs nowhere in the participant data. |
| case_20 | `VERIFY_RECORD_PRESENT security_service/audit_record_5569` | Same. |
| case_23 | `VERIFY_RECORD_ABSENT account_service/account_410` (we produce `account_7015`) | The 09-05 burst has three FAILED account deletes for three NOT_STARTED deletion cases and no evidence tying any of them to a person; the deterministic tie-break (case-number order) picks the wrong one here. This is the one extra-resource step. |

## Results by milestone

| Milestone | Status acc. | Step F1 | Case exact | Change |
|---|---|---|---|---|
| 0-1 Contract skeleton + harness (all COMPLETE, `[]`) | 31.25% | 0.00% | 0.00% | Baseline: the majority-class floor. |
| 2 Ingestion | 31.25% | 0.00% | 0.00% | No prediction change; 108/111 gold resources and 75/75 gold citations reachable through the evidence store. |
| 3-6 Identity, discovery, state, policy, planner (first full pipeline) | 96.88% | 93.77% | 62.50% | First end-to-end run. |
| + violation defined by policy (retained records are not violations) | 100.00% | 98.69% | 87.50% | Retained audit records were dropped from COMPLETE cases and blocked anonymized-record assignment. |
| 7-8 Intent rules, media extraction, aliases, readings after `reported_at` | 100.00% | 98.69% | 87.50% | Sample scores unchanged; fixes land in the 12 hand-built evaluation story cases (see below). |
| 9 Custom cases | 100.00% | 98.69% | 87.50% | 6/6 custom cases pass. |

## Custom evaluation cases

Defined in `code/evaluation/custom_cases.py`. Each is a controlled variant of a labelled sample: one input changes and the expected answer follows from the contract, not from running the pipeline. The variant replaces its base request in the run, so deletion bursts it shares with other cases are assigned exactly as in the real data. Result: **6/6 pass**.

| Case | Base | Change | Expected | Result |
|---|---|---|---|---|
| custom_01_spanish_access_request | case_05 | Spanish request for a copy of the data that keeps the account | `NOT_APPLICABLE`, `[]` | PASS |
| custom_02_negated_delete_word | case_12 | "Please do not delete anything. I only want the phone number ... updated" | `NOT_APPLICABLE`, `[]` | PASS |
| custom_03_old_email_only | case_12 | request email = the address user_12 replaced (audit_282) | case_12's gold answer, including the record keyed by the old email's hash; no evidence after `audit_at` | PASS |
| custom_04_intermediate_historical_email | case_10 | request email = the middle of user_10's three addresses | case_10's gold answer | PASS |
| custom_05_email_case_and_spacing | case_28 | `"  SMARTINEZ6461@GMAIL.COM "` | case_28's gold answer; the look-alike `smartinez6461.jr` search document (`search_8168`) never appears | PASS |
| custom_06_evidence_after_audit_ignored | case_31 | `audit_at` one second before `evidence_51`, the file proving the case | `evidence_51` never cited; no citation after `audit_at` | PASS (the account's absence is then proven by the earlier `evidence_07` snapshot) |

## Checks on the 518 evaluation requests

There are no labels, so these are consistency checks:

- The output validates against the full contract (`python code/main.py` exits 0): 518 rows sorted by case id, valid JSON, consecutive step numbers, allowed operation/state pairs, phase and within-phase ordering, empty evidence on INCOMPLETE remediation/verification, every cited id exists and was captured at or before that case's `audit_at`.
- Status mix: 354 INCOMPLETE, 154 COMPLETE, 10 NOT_APPLICABLE (68% / 30% / 2%), close to the samples' 69% / 31%.
- Intent: exactly the 10 non-deletion texts are NOT_APPLICABLE (each pairs a deletion word with a negation or keeps the account); the 10 `CLASSIFY_REQUEST ... non-deletion` activities corroborate the count. No text needed the model fallback.
- Identity: all 518 request emails resolve to a user.
- Accounts: 498 of 508 authorized cases get an account from their deletion burst or their own evidence; 2 choices are ambiguous (an unclaimed alternative delete exists) and 10 have no account at all (hand-built story cases whose evidence is screenshots and PDFs, with no account delete or account record for the person).
- No COMPLETE case is empty; every COMPLETE step cites evidence.

## Failures found and improvements made

1. **Absence is evidence.** The first ingestion only stored rows, so `case_31`'s citation was unreachable: `evidence_51` proves the account absent by listing an empty `customer_profiles` collection. Snapshots now also record coverage (which table / copy they captured), and an absent record is proven by a snapshot that left it out (74/75 -> 75/75 citations reachable).
2. **Nested identifiers.** SQL `record_json` rows keep identifiers inside a `document` object; they were not flattened, so two search documents lost their email and their case link. Fixed by flattening nested objects.
3. **Retained records counted as violations.** "Present" was treated as a violation for every category, so audit records were dropped from COMPLETE answers and blocked anonymized-record assignment. Violations now come from the policy (`plan_resource(...).finding`), which for RETAIN categories is never a finding (F1 93.8% -> 98.7%).
4. **Anonymized records have no identifiers.** They can only be linked through the deletion burst. Ordering by account-delete time paired them wrongly (`case_27` and `case_28` swapped); ordering eligible cases by case number matched all three sample bursts that have more than one.
5. **Account choice.** The account deleted in the request's own burst is the one verified, not any account the person ever had: `case_17`'s user also has an account deleted months earlier that the gold answer ignores. Non-deletion requests are classified before assignment so they do not take a delete (`case_536` in the 09-05 burst).
6. **Stale pre-deletion readings.** Evidence from before `reported_at` only shows the expected pre-deletion state. Every gold citation is captured after `reported_at`, and using older readings flagged `object_19` in `case_07`. Readings and absence proofs now must be observed after `reported_at`; SQL rows are timed by their snapshot, not their `updated_at`.
7. **Screenshots over-linked.** A screen lists many people's rows, so linking every resource on it to every identifier on it tied staff exports, a look-alike search document, and another customer's connector to the story cases. Screenshots now link only through their per-record observations.
8. **A multi-person register mis-linked a tombstone.** The legal retention register PDF lists six users; the alias `user_17` (the registry key of `tombstone_17`) linked that tombstone to `user_21` and made `case_21` ineligible for its anonymized record. Aliases that are user ids are no longer matched in free text, and a document naming more than one person links nothing.
9. **Missing dependency.** `pypdf` was assumed installed but was absent; it is pinned in `code/requirements.txt`.

## Known limitations

- Three gold resources occur in no participant file and cannot be produced from evidence.
- When a burst has several equally plausible account deletes or anonymized records and no evidence separates them, the tie-break (case-number order) is a guess; ambiguous choices are listed in `python code/main.py --explain <case>` notes (2 account and 7 anonymized-record pairs in the evaluation set).
- Retention conditions (`SECURITY_AUDIT_LOG` within retention, `LEGAL_HOLD_RECORD` while active) are not evaluated: every retention date in the data is in 2031-2034, after every `audit_at`.
- The backup-console screenshot shows no restore block for its archives; the transcription records them as restorable, which is an inference from the screen rather than an explicit flag.
