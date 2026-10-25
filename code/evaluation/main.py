"""Evaluation harness: score predictions against labelled answers.

    python code/evaluation/main.py                    # run the pipeline on the samples and score it
    python code/evaluation/main.py --pred FILE        # score an existing predictions CSV
    python code/evaluation/main.py --diff             # also print a diff for every imperfect case
    python code/evaluation/main.py --case case_12     # diff a single case
    python code/evaluation/main.py --markdown         # metrics as Markdown (for evaluation_report.md)
    python code/evaluation/main.py --ingest-coverage  # can the evidence store reach every gold resource and citation?
    python code/evaluation/main.py --custom           # hand-built variant cases (custom_cases.py)

Metrics (step metrics micro-averaged over gold steps; the rest are shares of cases):
  status accuracy   cases whose deletion_status matches
  step P/R/F1       exact (phase, operation, system_id, resource_id, state, evidence_id) multiset match
  field accuracy    per field, on gold steps aligned to a predicted step; unaligned gold steps count as wrong
  sequence accuracy cases whose full steps_json array matches exactly (order and step_no included)
  order score       mean LCS(gold, pred) / max(len) over cases, on step tuples in emitted order
  case exact        status and steps both match
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from audit.contract import STEP_FIELDS, case_sort_key  # noqa: E402
from audit.dataset import SAMPLE_OUTPUT_CSV, SAMPLE_REQUESTS_CSV, load_requests, parse_time  # noqa: E402
from audit.ingest import build_store  # noqa: E402
from audit.pipeline import audit_requests, run_audits  # noqa: E402
from custom_cases import CUSTOM_CASES  # noqa: E402

TUPLE_FIELDS = ("phase", "operation", "system_id", "resource_id", "state", "evidence_id")

# Alignment passes, strictest first: gold steps are paired with predicted
# steps that agree on these fields, so near-misses still get field credit.
ALIGN_KEYS = (
    TUPLE_FIELDS,
    ("phase", "operation", "system_id", "resource_id"),
    ("phase", "system_id", "resource_id"),
    ("system_id", "resource_id"),
)

Answer = tuple[str, list[dict]]


def load_answers(path: Path) -> dict[str, Answer]:
    answers = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                steps = json.loads(row["steps_json"])
            except json.JSONDecodeError:
                steps = []
            answers[row["case_id"]] = (row["deletion_status"], steps if isinstance(steps, list) else [])
    return answers


def predict(requests_path: Path) -> dict[str, Answer]:
    results = audit_requests(load_requests(requests_path))
    return {r.case_id: (r.deletion_status, r.steps) for r in results}


def step_tuple(step: dict, keys: tuple[str, ...] = TUPLE_FIELDS) -> tuple:
    return tuple(step.get(k) for k in keys)


def align(gold: list[dict], pred: list[dict]) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Greedily pair gold and predicted steps; return (pairs, unaligned gold, unaligned pred)."""
    pairs: list[tuple[int, int]] = []
    free_gold = list(range(len(gold)))
    free_pred = list(range(len(pred)))
    for keys in ALIGN_KEYS:
        for g in list(free_gold):
            target = step_tuple(gold[g], keys)
            p = next((p for p in free_pred if step_tuple(pred[p], keys) == target), None)
            if p is not None:
                pairs.append((g, p))
                free_gold.remove(g)
                free_pred.remove(p)
    return sorted(pairs), free_gold, free_pred


def lcs_len(a: list, b: list) -> int:
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, start=1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


@dataclass
class CaseScore:
    case_id: str
    gold_status: str
    pred_status: str | None
    gold_steps: list[dict]
    pred_steps: list[dict]
    exact_steps: int = 0
    field_hits: Counter = field(default_factory=Counter)
    sequence_match: bool = False
    order_score: float = 0.0
    buckets: Counter = field(default_factory=Counter)
    diff: list[str] = field(default_factory=list)

    @property
    def status_match(self) -> bool:
        return self.gold_status == self.pred_status

    @property
    def perfect(self) -> bool:
        return self.status_match and self.sequence_match


def score_case(case_id: str, gold: Answer, pred: Answer | None) -> CaseScore:
    gold_status, gold_steps = gold
    pred_status, pred_steps = pred if pred is not None else (None, [])
    cs = CaseScore(case_id, gold_status, pred_status, gold_steps, pred_steps)

    if pred is None:
        cs.buckets["missing case"] += 1
    elif not cs.status_match:
        cs.buckets["wrong status"] += 1

    gold_tuples = [step_tuple(s) for s in gold_steps]
    pred_tuples = [step_tuple(s) for s in pred_steps]
    cs.exact_steps = sum((Counter(gold_tuples) & Counter(pred_tuples)).values())
    cs.sequence_match = [step_tuple(s, STEP_FIELDS) for s in gold_steps] == [
        step_tuple(s, STEP_FIELDS) for s in pred_steps
    ]
    longest = max(len(gold_tuples), len(pred_tuples))
    cs.order_score = lcs_len(gold_tuples, pred_tuples) / longest if longest else 1.0

    pairs, lost_gold, extra_pred = align(gold_steps, pred_steps)
    pred_resources = {step_tuple(s, ("system_id", "resource_id")) for s in pred_steps}
    gold_resources = {step_tuple(s, ("system_id", "resource_id")) for s in gold_steps}

    for g, p in pairs:
        gs, ps = gold_steps[g], pred_steps[p]
        for k in STEP_FIELDS:
            cs.field_hits[k] += gs.get(k) == ps.get(k)
        if step_tuple(gs) == step_tuple(ps):
            continue
        if (gs.get("operation"), gs.get("state"), gs.get("phase")) != (ps.get("operation"), ps.get("state"), ps.get("phase")):
            bucket = "wrong treatment"
        else:
            bucket = "wrong evidence"
        cs.buckets[bucket] += 1
        changed = [f"{k}: {gs.get(k)!r} -> {ps.get(k)!r}" for k in TUPLE_FIELDS if gs.get(k) != ps.get(k)]
        cs.diff.append(f"  ~ {bucket:15} {_describe(gs)}  [{'; '.join(changed)}]")

    for g in lost_gold:
        gs = gold_steps[g]
        resource = step_tuple(gs, ("system_id", "resource_id"))
        bucket = "missing step" if resource in pred_resources else "missed resource"
        cs.buckets[bucket] += 1
        cs.diff.append(f"  - {bucket:15} {_describe(gs)}")

    for p in extra_pred:
        ps = pred_steps[p]
        resource = step_tuple(ps, ("system_id", "resource_id"))
        bucket = "extra step" if resource in gold_resources else "extra resource"
        cs.buckets[bucket] += 1
        cs.diff.append(f"  + {bucket:15} {_describe(ps)}")

    if Counter(gold_tuples) == Counter(pred_tuples) and not cs.sequence_match:
        cs.buckets["ordering"] += 1
        cs.diff.append("  ! ordering        same steps, different order or step_no")
    return cs


def _describe(step: dict) -> str:
    evidence = step.get("evidence_id") or '""'
    return (
        f"{step.get('phase', '?'):12} {step.get('operation', '?'):25} "
        f"{step.get('system_id', '?')}/{step.get('resource_id', '?')}  evidence={evidence}"
    )


@dataclass
class Report:
    cases: list[CaseScore]

    @property
    def gold_steps(self) -> int:
        return sum(len(c.gold_steps) for c in self.cases)

    @property
    def pred_steps(self) -> int:
        return sum(len(c.pred_steps) for c in self.cases)

    def metrics(self) -> dict[str, float]:
        n = len(self.cases) or 1
        exact = sum(c.exact_steps for c in self.cases)
        precision = exact / self.pred_steps if self.pred_steps else 0.0
        recall = exact / self.gold_steps if self.gold_steps else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        metrics = {
            "status accuracy": sum(c.status_match for c in self.cases) / n,
            "step precision": precision,
            "step recall": recall,
            "step F1": f1,
        }
        hits = sum((c.field_hits for c in self.cases), Counter())
        for k in STEP_FIELDS:
            metrics[f"field accuracy: {k}"] = hits[k] / self.gold_steps if self.gold_steps else 0.0
        metrics["sequence accuracy"] = sum(c.sequence_match for c in self.cases) / n
        metrics["order score (mean LCS ratio)"] = sum(c.order_score for c in self.cases) / n
        metrics["case exact"] = sum(c.perfect for c in self.cases) / n
        return metrics

    def status_confusion(self) -> Counter:
        return Counter((c.gold_status, c.pred_status or "MISSING") for c in self.cases)

    def buckets(self) -> Counter:
        return sum((c.buckets for c in self.cases), Counter())


def score(gold: dict[str, Answer], pred: dict[str, Answer]) -> Report:
    cases = [score_case(cid, gold[cid], pred.get(cid)) for cid in sorted(gold, key=case_sort_key)]
    return Report(cases)


def print_text(report: Report, extra_cases: list[str]) -> None:
    n = len(report.cases)
    print(f"cases={n}  gold steps={report.gold_steps}  predicted steps={report.pred_steps}\n")
    for name, value in report.metrics().items():
        print(f"  {name:32} {value:7.2%}")
    print("\nstatus confusion (gold -> pred):")
    for (g, p), count in sorted(report.status_confusion().items()):
        print(f"  {g:15} -> {p:15} {count}")
    if buckets := report.buckets():
        print("\nerror buckets (steps; 'wrong status'/'ordering'/'missing case' count cases):")
        for name, count in buckets.most_common():
            print(f"  {name:16} {count}")
    for case_id in extra_cases:
        print_case_diff(next(c for c in report.cases if c.case_id == case_id))


def print_case_diff(cs: CaseScore) -> None:
    print(
        f"\n{cs.case_id}  status gold={cs.gold_status} pred={cs.pred_status}  "
        f"exact steps {cs.exact_steps}/{len(cs.gold_steps)} (pred {len(cs.pred_steps)})"
    )
    print("\n".join(cs.diff) if cs.diff else "  (steps match)")


def print_markdown(report: Report) -> None:
    print(f"Cases: {len(report.cases)}, gold steps: {report.gold_steps}, predicted steps: {report.pred_steps}\n")
    print("| Metric | Value |\n|---|---|")
    for name, value in report.metrics().items():
        print(f"| {name} | {value:.2%} |")
    if buckets := report.buckets():
        print("\n| Error bucket | Count |\n|---|---|")
        for name, count in buckets.most_common():
            print(f"| {name} | {count} |")


def ingest_coverage(gold: dict[str, Answer]) -> None:
    """Report gold resources and cited evidence ids the evidence store cannot reach.

    A resource is reachable when some observation, activity, or document names
    it. A citation is reachable when the cited id is one of those items or a
    snapshot covering the resource's system (absence proofs).
    """
    store = build_store()
    resources: dict[tuple[str, str], set[str]] = {}
    citations: dict[tuple[str, str, str], set[str]] = {}
    for case_id, (_, steps) in gold.items():
        for s in steps:
            resources.setdefault((s["system_id"], s["resource_id"]), set()).add(case_id)
            if s["evidence_id"]:
                citations.setdefault((s["system_id"], s["resource_id"], s["evidence_id"]), set()).add(case_id)

    def item_ids(system_id: str, resource_id: str) -> set[str]:
        ids = {getattr(i, "evidence_id", None) or i.activity_id for i in store.for_resource(resource_id)}
        return ids | {c.evidence_id for c in store.coverage_for(system_id)}

    lost_resources = sorted((r for r in resources if not store.for_resource(r[1])), key=lambda r: r[1])
    lost_citations = sorted((c for c in citations if c[2] not in item_ids(c[0], c[1])), key=lambda c: c[1])
    print(
        f"evidence store: {len(store.observations)} observations, {len(store.coverage)} snapshot coverages, "
        f"{len(store.activities)} activities, {len(store.documents)} documents "
        f"({sum(not d.extracted for d in store.documents)} screenshots not yet extracted)"
    )
    print(f"gold resources reachable: {len(resources) - len(lost_resources)}/{len(resources)}")
    for system_id, resource_id in lost_resources:
        cases = ", ".join(sorted(resources[(system_id, resource_id)], key=case_sort_key))
        print(f"  - {system_id}/{resource_id}  ({cases})")
    print(f"gold citations reachable: {len(citations) - len(lost_citations)}/{len(citations)}")
    for system_id, resource_id, evidence_id in lost_citations:
        cases = ", ".join(sorted(citations[(system_id, resource_id, evidence_id)], key=case_sort_key))
        print(f"  - {system_id}/{resource_id} cites {evidence_id}  ({cases})")


def run_custom(gold: dict[str, Answer], verbose: bool) -> int:
    """Run each custom variant in place of its base case and check its expectation."""
    store = build_store()
    requests = {r.case_id: r for r in load_requests(SAMPLE_REQUESTS_CSV)}
    passed = 0
    for case in CUSTOM_CASES:
        base = requests[case["base"]]
        changes = {k: parse_time(v) if k.endswith("_at") else v for k, v in case["changes"].items()}
        variant = replace(base, **changes)
        result = run_audits([variant])[0].plan.result
        answer = (result.deletion_status, result.steps)

        failures = []
        expected = gold[base.case_id] if case["expect"] == "gold" else case["expect"]
        if isinstance(expected, dict):
            expected = (expected["status"], expected["steps"])
        if expected is not None:
            cs = score_case(case["id"], expected, answer)
            if not cs.perfect:
                failures.append(f"differs from expected ({cs.exact_steps}/{len(expected[1])} steps)")
                failures += cs.diff
        cited = {s["evidence_id"] for s in result.steps if s["evidence_id"]}
        for check in case.get("checks", []):
            name, ids = (check, []) if isinstance(check, str) else check
            if name == "no_late_evidence":
                late = sorted(e for e in cited if e in store.files and store.files[e].captured_at > variant.audit_at)
                if late:
                    failures.append(f"cites evidence captured after audit_at: {late}")
            elif name == "never_cites" and (hit := sorted(cited & set(ids))):
                failures.append(f"cites forbidden evidence {hit}")
            elif name == "never_mentions" and (hit := sorted({s["resource_id"] for s in result.steps} & set(ids))):
                failures.append(f"mentions {hit}")

        passed += not failures
        print(f"{'PASS' if not failures else 'FAIL'}  {case['id']}  ({case['base']}: {', '.join(case['changes'])})")
        print(f"      -> {result.deletion_status}, {len(result.steps)} step(s)")
        for line in failures:
            print(f"      {line}")
        if verbose:
            for s in result.steps:
                print(f"        {s['step_no']:2} {s['phase']:12} {s['operation']:26} {s['system_id']}/{s['resource_id']} {s['evidence_id']}")
    print(f"\ncustom cases passed: {passed}/{len(CUSTOM_CASES)}")
    return 0 if passed == len(CUSTOM_CASES) else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", type=Path, default=SAMPLE_OUTPUT_CSV, help="labelled answers CSV")
    parser.add_argument("--requests", type=Path, default=SAMPLE_REQUESTS_CSV, help="requests to run the pipeline on")
    parser.add_argument("--pred", type=Path, help="score this predictions CSV instead of running the pipeline")
    parser.add_argument("--diff", action="store_true", help="print a diff for every imperfect case")
    parser.add_argument("--case", action="append", default=[], metavar="CASE_ID", help="print a diff for this case")
    parser.add_argument("--markdown", action="store_true", help="print metrics as Markdown tables")
    parser.add_argument("--ingest-coverage", action="store_true", help="check the evidence store against gold resources")
    parser.add_argument("--custom", action="store_true", help="run the hand-built cases in custom_cases.py")
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    gold = load_answers(args.gold)
    if args.ingest_coverage:
        ingest_coverage(gold)
        return 0
    if args.custom:
        return run_custom(gold, verbose=args.diff)
    pred = load_answers(args.pred) if args.pred else predict(args.requests)
    report = score(gold, pred)

    if unknown := [c for c in args.case if c not in gold]:
        parser.error(f"not in gold answers: {', '.join(unknown)}")
    if args.markdown:
        print_markdown(report)
        return 0
    diff_cases = [c.case_id for c in report.cases if not c.perfect] if args.diff else args.case
    print_text(report, diff_cases)
    if extra := sorted(pred.keys() - gold.keys(), key=case_sort_key):
        print(f"\nnote: {len(extra)} predicted case(s) have no gold answer and were ignored")
    return 0


if __name__ == "__main__":
    sys.exit(main())
