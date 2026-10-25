"""Per-case deletion audit report for Ghost Data Still Exists.

Read-only viewer over the generated `output.csv` and the participant dataset.
It never writes anything and does not import the audit pipeline.

    streamlit run app/streamlit_app.py
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
DATASET = ROOT / "dataset"
OUTPUT_CSV = ROOT / "output.csv"
REQUESTS_CSV = DATASET / "deletion_requests.csv"
EVIDENCE_CSV = DATASET / "evidence_files.csv"
ACTIVITY_CSV = DATASET / "system_activity.csv"
IDENTITIES_CSV = DATASET / "user_identities.csv"
AUDIT_LOG_CSV = DATASET / "user_audit_log.csv"

STATUSES = ["COMPLETE", "INCOMPLETE", "NOT_APPLICABLE"]
STATUS_COLORS = {"COMPLETE": "#15803d", "INCOMPLETE": "#b91c1c", "NOT_APPLICABLE": "#6b7280"}
PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]
PHASE_TITLES = {"FINDING": "Findings", "REMEDIATION": "Remediation", "VERIFICATION": "Verification"}
STEP_COLUMNS = ["step_no", "operation", "system_id", "resource_id", "state", "evidence_id"]
RECREATION_EVENTS = {"SYNC_RECORD", "RESTORE_BACKUP"}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"\+\d[\d ()-]{6,}\d")
RESOURCE_RE = re.compile(r"\b[a-z][a-z_]*_\d+\b")


# --------------------------------------------------------------------------- loading

def _read_csv(path: Path, required: list[str]) -> tuple[pd.DataFrame, str | None]:
    """Read a CSV as strings; return (frame, error) instead of raising."""
    if not path.exists():
        return pd.DataFrame(columns=required), f"`{path.relative_to(ROOT).as_posix()}` was not found."
    try:
        df = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    except Exception as exc:  # malformed CSV, bad encoding, ...
        return pd.DataFrame(columns=required), f"Could not read `{path.relative_to(ROOT).as_posix()}`: {exc}"
    missing = [c for c in required if c not in df.columns]
    if missing:
        return pd.DataFrame(columns=required), (
            f"`{path.relative_to(ROOT).as_posix()}` is missing columns: {', '.join(missing)}."
        )
    return df, None


def _parse_steps(raw: str) -> tuple[list[dict], str | None]:
    try:
        steps = json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError as exc:
        return [], f"steps_json is not valid JSON ({exc.msg} at position {exc.pos})."
    if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
        return [], "steps_json is not a JSON array of step objects."
    return steps, None


@st.cache_data(show_spinner="Loading audit output...")
def load_output() -> tuple[pd.DataFrame, str | None]:
    df, err = _read_csv(OUTPUT_CSV, ["case_id", "deletion_status", "steps_json"])
    parsed = [_parse_steps(raw) for raw in df["steps_json"]]
    df["steps"] = [steps for steps, _ in parsed]
    df["steps_error"] = [error or "" for _, error in parsed]
    for phase in PHASES:
        df[phase] = [sum(1 for s in steps if s.get("phase") == phase) for steps in df["steps"]]
    return df, err


@st.cache_data(show_spinner=False)
def load_requests() -> tuple[pd.DataFrame, str | None]:
    return _read_csv(
        REQUESTS_CSV,
        ["case_id", "email", "requested_at", "reported_status", "reported_at", "audit_at", "request_text"],
    )


@st.cache_data(show_spinner=False)
def load_evidence() -> tuple[pd.DataFrame, str | None]:
    return _read_csv(EVIDENCE_CSV, ["file_id", "file_path", "origin", "captured_at"])


@st.cache_data(show_spinner=False)
def load_activity() -> tuple[pd.DataFrame, str | None]:
    df, err = _read_csv(
        ACTIVITY_CSV,
        ["activity_id", "event_time", "event_type", "source_system", "target_system",
         "resource_id", "reported_result", "details"],
    )
    df["time"] = pd.to_datetime(df["event_time"], utc=True, errors="coerce")
    df["mentions"] = [frozenset(RESOURCE_RE.findall(d)) for d in df["details"]]
    return df, err


@st.cache_data(show_spinner=False)
def load_identity_index() -> dict[str, str]:
    """Lower-cased email (current or historical) -> user_id, for PII masking."""
    index: dict[str, str] = {}
    ids, _ = _read_csv(IDENTITIES_CSV, ["user_id", "email"])
    for user_id, email in zip(ids["user_id"], ids["email"]):
        if email:
            index[email.strip().lower()] = user_id
    log, _ = _read_csv(AUDIT_LOG_CSV, ["user_id", "field_name", "old_value", "new_value"])
    emails = log[log["field_name"] == "email"]
    for user_id, old, new in zip(emails["user_id"], emails["old_value"], emails["new_value"]):
        for value in (old, new):
            if value:
                index.setdefault(value.strip().lower(), user_id)
    return index


# --------------------------------------------------------------------------- helpers

def case_sort_key(case_id: str) -> tuple[str, int]:
    prefix, _, number = case_id.rpartition("_")
    return (prefix, int(number)) if number.isdigit() else (case_id, -1)


def to_time(value: str) -> pd.Timestamp | None:
    ts = pd.to_datetime(value, utc=True, errors="coerce")
    return None if pd.isna(ts) else ts


def email_hash_prefix(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()[:12]


def subject_label(email: str, mask: bool, identities: dict[str, str]) -> str:
    if not email:
        return "unknown subject"
    if not mask:
        return email
    user_id = identities.get(email.strip().lower())
    hashed = f"sha256:{email_hash_prefix(email)}…"
    return f"{user_id} ({hashed})" if user_id else hashed


def mask_text(text: str, mask: bool, identities: dict[str, str]) -> str:
    if not mask:
        return text
    text = EMAIL_RE.sub(lambda m: identities.get(m.group(0).lower()) or f"sha256:{email_hash_prefix(m.group(0))}…", text)
    return PHONE_RE.sub("[phone]", text)


def status_badge(status: str) -> str:
    color = STATUS_COLORS.get(status, "#6b7280")
    return (
        f"<span style='background:{color};color:#fff;padding:0.2rem 0.6rem;border-radius:0.4rem;"
        f"font-weight:600;font-size:0.9rem;white-space:nowrap'>{html.escape(status or 'UNKNOWN')}</span>"
    )


def related_activity(
    activity: pd.DataFrame, resources: set[str], identifiers: set[str]
) -> pd.DataFrame:
    """Activity rows touching the case: by resource_id, a resource named in the
    details text, or one of the subject's identifiers named in the details."""
    if activity.empty or not (resources or identifiers):
        return activity.iloc[0:0]
    lowered = [i.lower() for i in identifiers if i]
    mask = [
        rid in resources
        or bool(mentions & resources)
        or any(i in details.lower() for i in lowered)
        for rid, mentions, details in zip(activity["resource_id"], activity["mentions"], activity["details"])
    ]
    return activity[mask].sort_values("time")


# --------------------------------------------------------------------------- sections

def render_header(case: pd.Series, request: pd.Series | None, mask: bool, identities: dict[str, str]) -> None:
    st.markdown(
        f"<div style='display:flex;flex-wrap:wrap;align-items:center;gap:0.6rem'>"
        f"<h2 style='margin:0;padding:0'>{html.escape(case['case_id'])}</h2>"
        f"{status_badge(case['deletion_status'])}</div>",
        unsafe_allow_html=True,
    )
    if request is None:
        st.warning("This case is not in `dataset/deletion_requests.csv`, so request details are unavailable.")
        return
    st.caption(
        f"Subject: {subject_label(request['email'], mask, identities)} · "
        f"workflow reported **{request['reported_status'] or 'n/a'}**"
    )
    st.markdown(
        f"**Requested** `{request['requested_at'] or '—'}` → "
        f"**Reported** `{request['reported_at'] or '—'}` → "
        f"**Audit** `{request['audit_at'] or '—'}`"
    )
    with st.expander("Request text"):
        st.write(mask_text(request["request_text"], mask, identities) or "_(empty)_")


def render_summary_cards(case: pd.Series) -> None:
    # A wrapping flex row instead of st.columns, which stacks into three
    # full-width blocks on a phone.
    cards = "".join(
        "<div style='flex:1 1 6.5rem;border:1px solid rgba(128,128,128,0.35);border-radius:0.5rem;"
        "padding:0.5rem 0.75rem'>"
        f"<div style='font-size:0.8rem;opacity:0.75'>{label}</div>"
        f"<div style='font-size:1.6rem;font-weight:600;line-height:1.2'>{int(case[phase])}</div></div>"
        for label, phase in (("Findings", "FINDING"), ("Remediation actions", "REMEDIATION"),
                             ("Verification checks", "VERIFICATION"))
    )
    st.markdown(f"<div style='display:flex;flex-wrap:wrap;gap:0.5rem;margin:0.5rem 0 1rem'>{cards}</div>",
                unsafe_allow_html=True)


def render_timeline(
    case: pd.Series, request: pd.Series | None, activity: pd.DataFrame, activity_err: str | None,
    mask: bool, identities: dict[str, str],
) -> None:
    st.subheader("Timeline")
    if activity_err:
        st.info(f"Timeline unavailable: {activity_err}")
        return
    resources = {str(s.get("resource_id", "")) for s in case["steps"]} - {""}
    identifiers = set()
    if request is not None and request["email"]:
        identifiers.add(request["email"])
        user_id = identities.get(request["email"].strip().lower())
        if user_id:
            identifiers.add(user_id)
    events = related_activity(activity, resources, identifiers)

    audit_at = to_time(request["audit_at"]) if request is not None else None
    if audit_at is not None:
        later = int((events["time"] > audit_at).sum())
        show_later = st.checkbox(
            f"Include events after audit_at ({later})", value=False, key=f"later_{case['case_id']}",
            disabled=later == 0,
        )
        if not show_later:
            events = events[~(events["time"] > audit_at)]

    milestones = []
    if request is not None:
        for label, column, color in (
            ("Request", "requested_at", "#2563eb"),
            ("Reported", "reported_at", "#7c3aed"),
            ("Audit", "audit_at", "#0d9488"),
        ):
            ts = to_time(request[column])
            if ts is not None:
                milestones.append((label, ts, color))

    if events.empty and not milestones:
        st.info("No activity or timestamps are linked to this case.")
        return

    deletes = events[events["event_type"] == "DELETE_RECORD"]
    account_deletes = deletes[deletes["target_system"] == "account_service"]
    deletion = (account_deletes if not account_deletes.empty else deletes).head(1)
    deletion_time = deletion["time"].iloc[0] if not deletion.empty else None
    is_deletion = events["activity_id"].isin(deletion["activity_id"])
    # Without a linked DELETE_RECORD, the workflow's reported_at stands in as the deletion time.
    reference = deletion_time
    if reference is None and request is not None:
        reference = to_time(request["reported_at"])
    is_risk = events["event_type"].isin(RECREATION_EVENTS) & (
        events["time"] > reference if reference is not None else False
    )

    hover = [
        "<br>".join([
            f"<b>{html.escape(r.activity_id)}</b> · {html.escape(r.event_type)}",
            html.escape(r.event_time),
            f"{html.escape(r.source_system)} → {html.escape(r.target_system)}",
            f"resource: {html.escape(r.resource_id)} · result: {html.escape(r.reported_result)}",
            html.escape(mask_text(r.details, mask, identities)),
        ])
        for r in events.itertuples()
    ]
    events = events.assign(hover=hover)

    fig = go.Figure()
    groups = (
        ("Other activity", ~is_deletion & ~is_risk, dict(color="#9ca3af", size=9, symbol="circle")),
        ("Deletion", is_deletion, dict(color="#dc2626", size=15, symbol="x")),
        ("Sync / restore after deletion", is_risk, dict(color="#f59e0b", size=14, symbol="diamond",
                                                         line=dict(color="#92400e", width=1))),
    )
    for name, selector, marker in groups:
        subset = events[selector]
        if subset.empty:
            continue
        fig.add_trace(go.Scatter(
            x=subset["time"], y=subset["event_type"], mode="markers", name=name, marker=marker,
            text=subset["hover"], hovertemplate="%{text}<extra></extra>",
        ))
    if events.empty:
        # Keep an axis to draw the milestone lines on.
        fig.add_trace(go.Scatter(x=[m[1] for m in milestones], y=["(no activity)"] * len(milestones),
                                 mode="markers", marker=dict(opacity=0), showlegend=False, hoverinfo="skip"))
    # Request, reported, and audit are often hours apart, so stagger the labels.
    for row, (label, ts, color) in enumerate(milestones):
        fig.add_shape(type="line", x0=ts, x1=ts, y0=0, y1=1, yref="paper",
                      line=dict(color=color, width=1.5, dash="dot"))
        fig.add_annotation(x=ts, y=1, yref="paper", yshift=2 + 15 * row, text=label, showarrow=False,
                           yanchor="bottom", xanchor="right" if row == len(milestones) - 1 else "center",
                           font=dict(color=color, size=11))
    # Autorange only looks at traces, so widen it to keep every milestone line visible.
    times = list(events["time"].dropna()) + [m[1] for m in milestones]
    start, end = min(times), max(times)
    pad = max(end - start, pd.Timedelta(hours=12)) * 0.06
    fig.update_xaxes(range=[start - pad, end + pad])
    lanes = events["event_type"].nunique() if not events.empty else 1
    height, top, bottom = max(260, 120 + 34 * lanes), 20 + 15 * len(milestones), 60
    fig.update_layout(
        height=height,
        margin=dict(l=8, r=8, t=top, b=bottom),
        # Legend goes below the date tick labels (~40 px); y is a fraction of the plot area.
        legend=dict(orientation="h", yanchor="top", y=-42 / (height - top - bottom), x=0),
        xaxis=dict(title=None),
        yaxis=dict(title=None, automargin=True),
        hovermode="closest",
    )
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})
    if deletion_time is None:
        st.caption("No DELETE_RECORD event is linked to this case's resources; "
                   "sync/restore risk is measured from reported_at.")

    if not events.empty:
        with st.expander(f"Linked activity ({len(events)} events)"):
            table = events[["activity_id", "event_time", "event_type", "source_system", "target_system",
                            "resource_id", "reported_result"]].copy()
            table["details"] = [mask_text(d, mask, identities) for d in events["details"]]
            st.dataframe(table, hide_index=True, width="stretch")
    st.caption(
        "Activity is linked through the resources in this case's steps, resources named in event details, "
        "and the subject's identifiers. A reported_result is a claim, not proof of state."
    )


def render_steps(case: pd.Series, evidence: pd.DataFrame, activity: pd.DataFrame) -> None:
    st.subheader("Audit steps")
    if case["steps_error"]:
        st.error(f"Could not read this case's steps: {case['steps_error']}")
        return
    steps = case["steps"]
    if not steps:
        if case["deletion_status"] == "NOT_APPLICABLE":
            st.info("No deletion was requested, so no audit steps apply.")
        else:
            st.warning(f"This case is {case['deletion_status']} but has no steps.")
        return

    paths = dict(zip(evidence["file_id"], evidence["file_path"]))
    activity_ids = set(activity["activity_id"]) if not activity.empty else set()

    def evidence_target(evidence_id: str) -> str:
        if not evidence_id:
            return ""
        if evidence_id in paths:
            return f"dataset/{paths[evidence_id]}"
        if evidence_id in activity_ids:
            return "dataset/system_activity.csv"
        if evidence_id.startswith("audit_"):
            return "dataset/user_audit_log.csv"
        return "(not in evidence manifest)"

    frame = pd.DataFrame(
        [{col: s.get(col, "") for col in STEP_COLUMNS} | {"phase": s.get("phase", "")} for s in steps]
    )
    frame["evidence_file"] = [evidence_target(str(e)) for e in frame["evidence_id"]]

    order = [p for p in PHASES if p in set(frame["phase"])]
    order += [p for p in dict.fromkeys(frame["phase"]) if p not in PHASES]
    for phase in order:
        rows = frame[frame["phase"] == phase]
        title = PHASE_TITLES.get(phase, f"Unknown phase: {phase or '(blank)'}")
        st.markdown(f"**{title}** ({len(rows)})")
        st.dataframe(
            rows[STEP_COLUMNS + ["evidence_file"]],
            hide_index=True,
            width="stretch",
            column_config={
                "step_no": st.column_config.NumberColumn("step", width="small"),
                "evidence_file": st.column_config.TextColumn("evidence file"),
            },
        )

    expected = list(range(1, len(steps) + 1))
    if [s.get("step_no") for s in steps] != expected:
        st.warning("step_no values are not numbered consecutively from 1 in array order.")

    cited = [e for e in dict.fromkeys(frame["evidence_id"]) if e]
    if cited:
        st.markdown("**Cited evidence**")
        meta = evidence.set_index("file_id") if not evidence.empty else None
        for evidence_id in cited:
            target = evidence_target(evidence_id)
            if meta is not None and evidence_id in meta.index:
                row = meta.loc[evidence_id]
                file_path = DATASET / row["file_path"]
                cols = st.columns([3, 1])
                cols[0].markdown(
                    f"`{evidence_id}` · `{target}`  \n"
                    f"<small>origin {html.escape(row['origin'])} · captured {html.escape(row['captured_at'])}</small>",
                    unsafe_allow_html=True,
                )
                if file_path.is_file():
                    cols[1].download_button(
                        "Open file", data=file_path.read_bytes(), file_name=file_path.name,
                        key=f"dl_{case['case_id']}_{evidence_id}", width="stretch",
                    )
                else:
                    cols[1].caption("file missing")
            else:
                st.markdown(f"`{evidence_id}` · {target}")


def render_case_tab(
    cases: pd.DataFrame, case_id: str | None, requests: pd.DataFrame, evidence: pd.DataFrame,
    activity: pd.DataFrame, activity_err: str | None, mask: bool, identities: dict[str, str],
) -> None:
    if case_id is None:
        st.info("No case matches the current filters.")
        return
    case = cases[cases["case_id"] == case_id].iloc[0]
    matches = requests[requests["case_id"] == case_id]
    request = matches.iloc[0] if not matches.empty else None

    for section, render in (
        ("header", lambda: render_header(case, request, mask, identities)),
        ("summary", lambda: render_summary_cards(case)),
        ("timeline", lambda: render_timeline(case, request, activity, activity_err, mask, identities)),
        ("steps", lambda: render_steps(case, evidence, activity)),
    ):
        try:
            render()
        except Exception as exc:  # keep the rest of the page alive
            st.error(f"The {section} section could not be rendered: {exc}")


def render_overview(cases: pd.DataFrame, requests: pd.DataFrame, statuses: list[str]) -> None:
    st.subheader("All cases")
    counts = cases["deletion_status"].value_counts()
    cols = st.columns(len(STATUSES) + 1)
    cols[0].metric("Cases", len(cases))
    for col, status in zip(cols[1:], STATUSES):
        col.metric(status.replace("_", " ").title(), int(counts.get(status, 0)))

    bar_statuses = STATUSES + sorted(set(counts.index) - set(STATUSES))
    fig = go.Figure(go.Bar(
        x=bar_statuses,
        y=[int(counts.get(s, 0)) for s in bar_statuses],
        marker_color=[STATUS_COLORS.get(s, "#6b7280") for s in bar_statuses],
        text=[int(counts.get(s, 0)) for s in bar_statuses],
        textposition="outside",
        hovertemplate="%{x}: %{y}<extra></extra>",
    ))
    fig.update_layout(height=300, margin=dict(l=8, r=8, t=24, b=8), yaxis=dict(title="cases"))
    st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    table = cases[["case_id", "deletion_status", "FINDING", "REMEDIATION", "VERIFICATION"]].merge(
        requests[["case_id", "reported_status", "audit_at"]], on="case_id", how="left"
    ).fillna("")
    table = table[table["deletion_status"].isin(statuses)]
    table = table.rename(columns={"FINDING": "findings", "REMEDIATION": "remediation",
                                  "VERIFICATION": "verification"})
    st.caption(f"{len(table)} cases shown (sidebar status filter applies).")
    st.dataframe(table, hide_index=True, width="stretch")

    unparsed = cases[cases["steps_error"] != ""]
    if not unparsed.empty:
        st.error(f"{len(unparsed)} case(s) have unreadable steps_json: {', '.join(unparsed['case_id'])}")
    missing = set(requests["case_id"]) - set(cases["case_id"])
    if missing:
        st.warning(f"{len(missing)} request(s) have no row in output.csv.")


# --------------------------------------------------------------------------- page

def main() -> None:
    st.set_page_config(page_title="Deletion Audit Report", page_icon="🧾", layout="wide",
                       initial_sidebar_state="auto")

    cases, output_err = load_output()
    requests, requests_err = load_requests()
    evidence, evidence_err = load_evidence()
    activity, activity_err = load_activity()
    identities = load_identity_index()

    for err in (output_err, requests_err, evidence_err):
        if err:
            st.error(err)
    if cases.empty:
        st.stop()

    with st.sidebar:
        st.header("Case")
        query = st.text_input("Search case_id", placeholder="e.g. case_33").strip().lower()
        statuses = st.multiselect("Deletion status", STATUSES, default=STATUSES)
        mask = st.toggle("Mask PII", value=True, help="Show user_id or an email hash prefix instead of emails and phones.")

        filtered = cases[cases["deletion_status"].isin(statuses)]
        if query:
            filtered = filtered[filtered["case_id"].str.lower().str.contains(query, regex=False)]
        options = sorted(filtered["case_id"], key=case_sort_key)
        case_id = st.selectbox(f"Select case ({len(options)})", options, index=0 if options else None)

    report, overview = st.tabs(["Case report", "Overview"])
    with report:
        render_case_tab(cases, case_id, requests, evidence, activity, activity_err, mask, identities)
    with overview:
        try:
            render_overview(cases, requests, statuses)
        except Exception as exc:
            st.error(f"The overview could not be rendered: {exc}")


main()
