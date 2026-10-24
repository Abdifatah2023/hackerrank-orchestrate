# Deletion-Audit Agent: Architecture & Roadmap

Status: design only, no code yet. Every claim below comes from inspecting the participant-facing files in `dataset/` and the 32 public sample answers. Organizer-only data was not used.

---

## 1. Problem in one paragraph

For each of 518 requests we must (1) decide whether the text authorizes deletion, (2) resolve who the requester is across current and historical identifiers, (3) find every resource belonging to that person in a global, decoy-laden evidence pool, (4) reconstruct each resource's state **as of `audit_at`**, (5) compare it with the policy in `deletion_rules.csv`, and (6) emit a deterministic, strictly ordered `steps_json` plan. The scoring is mostly **set recall/precision on (system_id, resource_id, operation, evidence_id) tuples plus their order**, so resource discovery and evidence selection matter more than prose reasoning.

---

## 2. What the data actually looks like (findings that drive the design)

### 2.1 Inputs at a glance

| File | Rows | Role |
|---|---|---|
| `deletion_requests.csv` | 518 (`case_33`–`case_550`) | Cases to answer; `audit_at` ranges 2026-01-29 → 2026-10-01 |
| `sample_deletion_requests.csv` / `sample_output.csv` | 32 | Only labelled data (22 INCOMPLETE, 10 COMPLETE, **0 NOT_APPLICABLE**) |
| `user_identities.csv` | 850 | Current `user_id`, name, email, phone, `email_hash` |
| `user_audit_log.csv` | 306 | History of `email`, `email_hash`, `phone` changes (old → new) |
| `system_activity.csv` | 4,011 | Global event stream; `reported_result` is a claim (SUCCESS/FAILED/PARTIAL) |
| `deletion_rules.csv` | 15 | Category → DELETE / REDACT / ANONYMIZE / RETAIN |
| `evidence_files.csv` | 94 | `file_id` → path + `captured_at` (no case linkage) |

### 2.2 Evidence families (94 files)

| Family | Files | Format | What it gives |
|---|---|---|---|
| Resource readback exports (`service_resource_export_*.json`) | 12, about every 3 weeks Jan–Sep | JSON `service_exports.{system}.{collection}[]` | Per-resource state: 404 vs 200, identifier fields, `enabled`, `restore_allowed`, `customer_reference` |
| Inventory snapshots (`customer_data_inventory_*.sql`) | 11 | Typed SQLite tables (`customer_profiles`, `customer_search_documents`, `connector_subscriptions`, `backup_restore_entries`, `security_audit_records`, …) | Point-in-time records with identifiers + `account_status` |
| Readback traces (`resource_lookup_trace_*.log`) | 11 | key=value log | **`lookup_key=<email or user_id>` paired with `resource_id`** plus result (404 / hit / enabled / restore_allowed) |
| Replica SQL (`read_replica`, `regional_replicas`, `reporting_replica`, `failover_replica`) | 13 | `record_json` blobs in `*_primary` and `*_replica_*` tables | Replication drift: a record gone from primary but alive in a replica |
| Snapshot bundles (`customer_data_snapshot_bundle_*.json`) | 12 | Nested `snapshots[]`, **each with its own `captured_at`** | Multi-store records, historical identifiers |
| Observability logs (service_events, syslog, app traces, …) | 11 | key=value, noisy | `correlation_key` (email or **old email hash**), `message_pii_scan`, `connector_heartbeat enabled=true`, `restore_candidate_evaluated eligible=true deletion_block=false` |
| Ledgers (`security_audit_ledger`, `connector_configurations`, handover `.txt`) | 3 | `records[]` / pipe table | `system | resource | CATEGORY | FOUND/NOT_FOUND/IDENTIFIABLE/ANONYMIZED | subject` |
| Story artifacts (PDF ×4, PNG ×11, export jobs, analytics report, support_replica.log, search primary SQL, accounts failover SQL) | ~20 | Mixed | Hand-built cases (`account_001`…, `user_01`–`user_20`), often tied together by `LINK_RESOURCE_IDENTITY` activity rows |

PNGs are clean, rendered admin-console screenshots (tables of resource IDs, emails, flags). They are ideal for a one-time vision-model extraction.

### 2.3 Patterns learned from the 32 samples

1. **The account resource is always verified.** Every authorized case has `VERIFY_RECORD_ABSENT account_service/<account_id>`, even when the account is already gone and not a finding.
2. **Retained security records are always verified, never remediated.** `security_service/audit_record_*` (linked by `subject_reference = user_id`) gets `VERIFY_RECORD_PRESENT`. Exception: the hand-built cases `case_01`, `case_02`, and `case_05`–`case_10` have no audit record.
3. **Linkage often exists only in the activity stream.** For example, `account_6440` (case_21) and `account_410` (case_23) appear in no evidence that names the user. The case's `deletion_worker` burst is the link:
   - the account `DELETE_RECORD` sits within about 1.5 h of `reported_at`;
   - its `reported_result` mirrors `reported_status` (SUCCESS↔COMPLETE, PARTIAL↔PARTIALLY_COMPLETE, FAILED↔NOT_STARTED);
   - follow-on actions (analytics, tombstone, support, flow, backup) come in the next 0.5–3 h.
   Several cases share a burst, so the linkage needs **global assignment**, not a greedy per-case search.
4. **Anonymized records carry no identifiers.** An anonymized analytics row (e.g. `analytics_5335`) has every identifier `null`. Only the burst links it to the case.
5. **Evidence citation = the file that shows the state, latest at/before `audit_at`.** For example, case_12 cites `evidence_01` (09-15 11:59) over `evidence_92` (09-15 11:44). In a COMPLETE case, different steps can cite different files (case_04: `evidence_54` for the account, `evidence_56` for analytics).
6. **INCOMPLETE ⇒ every remediation and verification `evidence_id` is `""`**, including the account and security verifications. COMPLETE ⇒ every verification cites evidence.
7. **Category comes from the record's shape, not just the system.** In case_12, an `analytics_platform` contact lookup keyed by the *old* email hash is DELETE (secondary identifier), not ANONYMIZE.
8. **Decoys are deliberate.** Similar emails (`smartinez6461.jr@gmail.com`, `g.silva76.jr@gmail.com`), upper-cased emails (`SIPHO.MOKOENA@GMAIL.COM`, a real variant to normalize), same-surname users, and resources of other users in the same burst. Matching must be **exact after case-folding**, never fuzzy.
9. **Ordering confirmed:** phase → operation order of the contract table → `system_id` → `resource_id` (string sort). Remediation order DISABLE_DATA_FLOW → BLOCK_RESTORE → DELETE → REDACT → ANONYMIZE automatically satisfies "disable flows and block restores first".
10. **Serialization:** `email_hash = sha256(email)` (lower-case input). Sample CSV keys appear in mixed order, and the grader ignores key order. Emit sorted keys with compact separators for determinism.

---

## 3. Architecture

The core is a deterministic evidence pipeline. LLM calls are limited to three narrow, cacheable jobs: intent, image/PDF extraction, and an optional tie-breaker. This keeps the run reproducible and cheap, and makes the token report honest.

```
                ┌──────────────────────────────────────────────────────────┐
dataset/ ──►    │ 1. INGEST  (parsers → unified Observation store)          │
                │   sql | json | log | txt | pdf/png (LLM-extracted, cached)│
                └───────────────┬──────────────────────────────────────────┘
                                ▼
 request ──► 2. INTENT ──NOT_APPLICABLE──────────────────────────► [] row
                │ deletion
                ▼
            3. IDENTITY  (email → user_id → all current+historical ids)
                ▼
            4. DISCOVERY (case → candidate resources; global burst assignment)
                ▼
            5. STATE     (per resource: latest observation ≤ audit_at, replica drift, flows, restores)
                ▼
            6. POLICY    (category → required treatment → compliant? → finding type)
                ▼
            7. PLANNER   (findings / remediation / verification, sorted, evidence chosen)
                ▼
            8. EMIT + VALIDATE → output.csv      ◄── 9. EVALUATION harness (samples + custom cases)
```

### 3.1 Ingest: `Observation` store

Every parser normalizes into one record type, so downstream logic is format-agnostic:

```
Observation(
  evidence_id, file_captured_at, observed_at,     # observed_at = row/snapshot time when present
  system_id, resource_id, collection, category_hint,
  identifiers: {emails[], email_hashes[], phones[], names[], user_refs[]},
  state: {exists, status_code, account_status, enabled, restore_allowed,
          deletion_block, eligible, anonymized, retention_until, pii_detected},
  copy: "primary" | "replica:<name>" | "failover" | None,
  raw: <original fragment, for debugging>)
```

Parsers: `sql` (load each file into in-memory `sqlite3`, handling both the typed inventory schema and the generic `record_json` replica schema; record which table each row came from), `json` (export / bundle / ledger / jobs / report variants, keyed by shape), `log` (key=value regex, joining `*_started` + `*_completed` by `trace_id` so `lookup_key` attaches to results), `txt` (pipe table), `pdf` (all 4 have embedded text: `pypdf`/PyMuPDF, both installed, no LLM needed), `png` (11 screenshots: vision LLM once → schema-constrained JSON cache in `code/cache/`, reused on every run. No Tesseract is installed, so OCR is not the fallback).

`system_activity.csv` is ingested separately as `Event` rows. Events are **claims**. They are used for linkage (§3.4) and for recreation risk (`SYNC_RECORD` after a delete, `RESTORE_BACKUP`, `CHECK_DATA_FLOW` / `CHECK_RESTORE_ACCESS`). They never serve as proof of a final state.

### 3.2 Intent classifier → NOT_APPLICABLE

- **Stage A (rules, multilingual):** explicit deletion verbs (delete/erase/remove/close/supprimer/eliminar/exclusão/削除/…) vs non-deletion intents (access/copy, correction, password reset, invoice, export, marketing opt-out) and negations ("do not close", "don't delete", "without removing").
- **Stage B (LLM, small model, batched, cached):** only for texts where Stage A is not confident. The output is a strict JSON label plus rationale.
- **Corroboration:** a `CLASSIFY_REQUEST … non-deletion` activity for the case's account is a strong but non-binding signal. Disagreements are logged for review.
- The sample set has no NOT_APPLICABLE example, so the custom eval cases (§5) must cover this path.

### 3.3 Identity resolver

`email` → case-fold/trim → match `user_identities.email` **or** any `user_audit_log` old/new email value. That gives the `user_id`. Collect the identifier closure: all emails, all hashes (recomputed with sha256 and taken from the audit log), all phones (digit-normalized), name, `user_id`. Store each identifier with its validity interval, because an old email only matters for evidence where it was live or copied. A request email matching nobody → no resources. The case is then still authorized, with whatever the activity stream links.

### 3.4 Resource discovery (the hardest part)

Candidates for a case come from four sources, scored and merged:

1. **Direct identifier hit:** an observation (file `captured_at ≤ audit_at`) carrying one of the subject's emails, hashes, or phones, or `customer_reference` / `subject_reference` / `user_id` equal to the subject. Exact match only.
2. **Readback lookup key:** trace logs where `lookup_key` is a subject identifier → that `resource_id`. This works even when the result is 404, which is how already-deleted accounts get linked.
3. **Catalog link:** a `LINK_RESOURCE_IDENTITY` event naming the subject's email or `user_id`.
4. **Burst assignment:** `deletion_worker` events in `[requested_at, reported_at + ~3 h]`. Solve a **global assignment** over all cases (sample + eval share bursts):
   - Each case gets one account `DELETE_RECORD`. Its cost is the time distance to `reported_at`, plus a penalty when `reported_result` disagrees with `reported_status`, plus a hard exclusion when evidence ties that account to a different person.
   - Follow-on actions attach to the case whose account was assigned in the same burst. Ties go to the nearest-in-time and policy-plausible resource types.
   - Use Hungarian or min-cost matching. It is deterministic.

Resources linked only through someone else's identifiers are rejected (decoy guard). The target is **100% resource recall on the 32 samples** before tuning anything else.

### 3.5 State reconstruction at `audit_at`

For each (case, resource):

- Drop every observation whose file `captured_at > audit_at` (hard rule). Inside bundles, also respect per-snapshot `captured_at`.
- **Latest wins:** the effective state is the latest observation ≤ `audit_at`.
- **Copies are separate obligations:** if a replica, failover, or reporting table still holds identifiers while primary is clean, the resource still exposes data. This becomes a finding against that `resource_id`, with the evidence file showing the drift.
- **Recreation risks:** a connector with `enabled=true` / `scheduler_registered=true` / `last_delivery_status=delivered` → active data flow. A backup with `restore_allowed=1` / `eligible=true, deletion_block=false` → restore risk. A `SYNC_RECORD` or `RESTORE_BACKUP` event after the delete, with no later clean observation → recreation evidence.
- Unknown state (no observation) falls back to "no evidence of violation". The resource still gets its mandatory verification step if policy demands one (the account, security records).

### 3.6 Policy engine

Map each resource to a `data_category` from system, collection, and record shape:

| Shape | Category | Required | Compliant when |
|---|---|---|---|
| account profile | USER_PROFILE | DELETE | 404 / NOT_FOUND / absent |
| search doc | SEARCH_INDEX_ENTRY | DELETE | no hit |
| contact lookup / hash-keyed secondary row | SECONDARY_IDENTIFIER | DELETE | absent |
| cache entry | CACHE_ENTRY | DELETE | absent |
| support ticket with shared context | SUPPORT_MESSAGE | REDACT | requester name/email/phone null |
| attachment owned only by subject | SUPPORT_ATTACHMENT | DELETE | absent |
| analytics profile / telemetry | ANALYTICS_PROFILE / PRODUCT_TELEMETRY | ANONYMIZE | no email/phone/hash/customer_reference |
| export file | USER_EXPORT | DELETE | absent |
| marketing profile | MARKETING_PROFILE | DELETE | absent |
| email delivery log | EMAIL_DELIVERY_LOG | REDACT | no email |
| security audit record | SECURITY_AUDIT_LOG | RETAIN if `retention_until > audit_at` | present |
| legal hold | LEGAL_HOLD_RECORD | RETAIN while hold active | present |
| backup archive | BACKUP_ARCHIVE | RETAIN + block restore | `restore_allowed=false` |
| deleted-account reference | ACCOUNT_TOMBSTONE | REDACT (keep `user_id` only) | name/email/phone/hash null |
| connector / data flow | (risk) | disable | `enabled=false` |

Finding type from the violation: record must be gone but exists → `REPORT_REMAINING_RECORD`; record may stay but identifiers are exposed → `REPORT_EXPOSED_PII`; flow enabled → `REPORT_ACTIVE_DATA_FLOW`; restore possible → `REPORT_RESTORE_RISK`.

The rules table is loaded from CSV, not hard-coded. Only the shape → category classifier is code.

### 3.7 Planner (pure function, fully deterministic)

```
status = NOT_APPLICABLE if not authorized
       = INCOMPLETE     if any finding
       = COMPLETE       otherwise
FINDING:      one per violation, evidence = file proving the violation (latest ≤ audit_at)
REMEDIATION:  one per finding: DISABLE_DATA_FLOW | BLOCK_RESTORE | DELETE | REDACT | ANONYMIZE
VERIFICATION: account ABSENT  + every retained record PRESENT
              + every remediated or treated resource in its final state
              (COMPLETE: cite proving evidence; INCOMPLETE: evidence "")
sort each phase by (operation rank, system_id, resource_id); renumber step_no from 1
```

In COMPLETE cases, previously treated resources (anonymized analytics, redacted support, disabled flow, blocked backup, redacted tombstone) still get their verification step with proving evidence (samples 26–28, 31).

### 3.8 Emit and validate

Write `output.csv` with exactly 518 rows sorted by `case_id`, valid JSON arrays, consecutive `step_no`, and allowed operation/state pairs only. Assert that no `evidence_id` was captured after `audit_at` and that every cited id exists in the manifest, `system_activity`, or `user_audit_log`. Fail loudly on any violation.

### 3.9 LLM usage and cost control

| Job | Model | Calls | Caching |
|---|---|---|---|
| PNG → structured JSON | `claude-sonnet-5-5` (vision) | 11, once | `code/cache/extractions/*.json` keyed by file sha256 |
| Ambiguous intent | `claude-haiku-5-5` | only Stage-A misses, batched | keyed by text hash |
| Optional tie-break (category or linkage) | `claude-sonnet-5-5` | only flagged cases | keyed by case + inputs hash |

Temperature 0, JSON-schema outputs, and a token/cost counter feed `evaluation/usage_report.md`. The API key comes from the `ANTHROPIC_API_KEY` env var. With a warm cache, the pipeline runs fully offline and deterministically.

### 3.10 Proposed code layout

```
code/
  main.py                 # CLI: python code/main.py [--cases ...] [--no-llm] [--debug case_id]
  audit/
    ingest/{sql,json,logs,text,media}.py
    observations.py       # Observation / Event dataclasses + store + time filtering
    intent.py
    identity.py
    discovery.py          # direct links + global burst assignment
    state.py
    policy.py
    planner.py
    emit.py               # serialization + contract validator
    llm.py                # client, cache, token accounting
  cache/                  # LLM extraction cache (derived, deterministic)
  evaluation/
    main.py               # scorer vs sample_output + custom cases
    custom_cases/         # hand-built cases (inputs + expected)
    evaluation_report.md
    usage_report.md
```

---

## 4. Highest-signal evidence by decision

### NOT_APPLICABLE
1. **`request_text` intent.** This is the decision itself. Look for negations ("do not close", "don't delete", "without removing") and non-deletion asks (copy of data, invoices, correction, login reset, opt-out).
2. **`CLASSIFY_REQUEST` activity** ("classified … as non-deletion; no destructive action was authorized"). Strong corroboration, 10 events.
3. Absence of a `deletion_worker` burst near `reported_at` (weak).
   The `reported_status` value is irrelevant here.

### INCOMPLETE (any one suffices)
1. **Latest point-in-time readback ≤ `audit_at`.** `service_resource_export` (status 200 / identifiers non-null), `resource_lookup_trace` (`rows=1`, `result=hit`, `enabled=true`, `restore_allowed=true`), inventory SQL rows with identifiers, ledgers/handover (`FOUND`, `IDENTIFIABLE`).
2. **Replica drift.** A replica, failover, or reporting table row with identifiers when primary is clean (`customer_accounts_failover_replica`, `customer_data_*_replica*`).
3. **Recreation risk in logs.** `connector_heartbeat enabled=true scheduler_registered=true`, `restore_candidate_evaluated eligible=true deletion_block=false`, `message_pii_scan matched_value=…`, `correlation_key=<old email or old hash>`.
4. **Activity after deletion.** `SYNC_RECORD` to a replication cluster or failover, `RESTORE_BACKUP … repopulated`, `INDEX_RECORD` re-adding the subject. These are claims that point to where to look. Do not cite them blindly.
5. **Story artifacts.** Screenshots/PDFs showing an attachment, export link, marketing profile, or tombstone still carrying PII.

### COMPLETE (all must hold)
1. Latest observation ≤ `audit_at` for the account shows 404 / `NOT_FOUND` / absent.
2. Every other linked resource is in its required final state: identifiers null (anonymized/redacted), connector `enabled=false`, backup `restore_allowed=false`, tombstone with only `user_id`.
3. Retained security records are present (`retention_until > audit_at`).
4. No later-but-pre-audit evidence contradicts this, with no sync or restore after the clean observation.
   `reported_result=SUCCESS` and `reported_status=COMPLETE` are **not** evidence. Samples 25, 26, and 28 are COMPLETE despite FAILED or NOT_STARTED claims, and many INCOMPLETE cases claim COMPLETE.

### Identity (feeds all three)
`user_identities.csv` plus `user_audit_log.csv`. The audit-log rows carry old emails and **old email hashes**, which is the only way to catch hash-keyed records (case_12).

---

## 5. Evaluation plan

- **Scorer (`code/evaluation/main.py`)** against `sample_output.csv`:
  - case status accuracy;
  - step-level P/R/F1 on exact `(phase, operation, system_id, resource_id, state, evidence_id)` tuples;
  - field-level accuracy per key on aligned steps;
  - sequence accuracy (exact array match) and order-aware LCS ratio.
- **Error buckets:** missed resource, extra resource (decoy), wrong category/treatment, wrong evidence, ordering. Each fix is logged into `evaluation_report.md`.
- **Custom cases (≥3, more is better).** Each is built by deriving a variant of a real sample with a controlled change:
  1. non-deletion request in a non-English language (→ NOT_APPLICABLE, `[]`);
  2. request using only a historical email + hash-keyed record;
  3. evidence captured after `audit_at` that would flip the verdict (must be ignored);
  4. replica-only residue with a clean primary;
  5. decoy look-alike email in the same snapshot.
- **Sanity on the 518:** status distribution vs `reported_status`, cases with zero linked account (should be ~0), cases where burst assignment was ambiguous.

---

## 6. Implementation roadmap

| # | Milestone | Deliverable | Exit criterion |
|---|---|---|---|
| 0 | Contract skeleton | `main.py` reads requests and writes 518 valid rows (all COMPLETE, `[]` steps); validator | Validator passes; format accepted |
| 1 | Evaluation harness first | Scorer + per-case diff printer | Runs on samples; baseline numbers recorded |
| 2 | Ingest | All text parsers → Observation store; dump tool `--debug-resource` | Every resource id in sample answers is found in ≥1 observation or event |
| 3 | Identity | Resolver with history + hashes | All 32 sample emails resolve; case_10/12/29 pick up old ids |
| 4 | Discovery | Direct links + lookup-key + catalog + global burst assignment | **Sample resource recall = 100%**, no decoys |
| 5 | State + policy | Time filter, latest-wins, drift, flows/restores, category map | Sample status accuracy = 32/32 |
| 6 | Planner + evidence choice | Ordering, verification rules, citation picker | Sample sequence accuracy ≥ 95% |
| 7 | Intent | Rules + LLM fallback + CLASSIFY corroboration | All non-deletion texts in the 518 labelled correctly on manual review |
| 8 | Media extraction | PDF text via pypdf; one-time vision pass on 11 PNGs, cached | Story samples (case_01–case_10) fully correct |
| 9 | Custom cases + hardening | ≥5 custom cases, error-bucket fixes | All custom cases pass |
| 10 | Full run + reports | `output.csv`, `evaluation_report.md`, `usage_report.md`, README run instructions | Clean run from scratch; `code.zip` packaged without dataset |

Order rationale: the scorer exists before any logic, so every later milestone is measured. Discovery is isolated as its own milestone because it dominates the score.

---

## 7. Risks and open questions

- **Burst ambiguity.** Several cases can share a burst with identical claimed statuses. Mitigations: global assignment, evidence-based exclusions, and lookup-key traces. Report residual ambiguity counts.
- **Which file to cite when several show the same state.** The working hypothesis from samples is "latest file ≤ `audit_at` that observes that resource". Validate per resource type on samples.
- **Support message vs attachment, analytics anonymize vs secondary-identifier delete.** These depend on record shape and message content. Mine the samples for the distinguishing fields. Use an LLM tie-break only if rules are ambiguous.
- **Stale vs fresh.** A violation seen in an older file but contradicted by a newer pre-audit file is not a finding. Latest wins, unless the newer file is a different copy (replica).
- **No NOT_APPLICABLE samples.** Intent accuracy cannot be measured on labelled data, so rely on custom cases and manual review of flagged texts.
