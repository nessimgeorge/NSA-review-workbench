"""Run: python -m streamlit run app.py --server.address 127.0.0.1"""
import html
import io
import json
import zipfile
from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from engine import (DATA, VERSION, LIMITATIONS, digest, json_bytes, read_cases,
                    run_batch, make_run_id, save_review, load_reviews, safe_csv)
from local_ai import draft_note

st.set_page_config(page_title="IDR Review Workbench", page_icon="📋", layout="wide")
st.title("IDR Review Workbench")
st.caption("Offer benchmarking • explicit comparison rules • human review")
st.info("Prototype for synthetic case uploads. Historical context only; no payment recommendations.")

with st.sidebar:
    st.header("1. Choose evidence")

    reference_options = ["SYNTHETIC DEMO"]

    cms_files_available = (
        (DATA / "cms_peers.csv").exists()
        and (DATA / "cms_manifest.json").exists()
    )

    if cms_files_available:
        reference_options.append("CMS public data")

    mode = st.selectbox(
        "Reference data",
        reference_options,
    )

    minimum = st.number_input(
        "Minimum peer count",
        min_value=2,
        max_value=1000,
        value=30,
    )

    tail = st.select_slider(
        "Lower percentile cutoff (upper = 100 minus this)",
        options=[5, 10, 15, 20, 25],
        value=10,
    )

    fallback = st.checkbox(
        "Allow national fallback when exact peers are insufficient",
        value=False,
    )

    st.caption(
        "Counts and percentile cutoffs are demo rules, not validated "
        "credibility standards. National fallback always receives a review flag."
    )

    st.header("2. Optional local AI")

    model = st.text_input(
        "Installed Ollama model name",
        value="gemma4:e2b",
    )

    st.caption(
        "Only computed evidence goes to Ollama at 127.0.0.1. "
        "The app works without it."
    )

prefix = "demo" if mode == "SYNTHETIC DEMO" else "cms"
path = DATA / f"{prefix}_peers.csv"
manifest_path = DATA / f"{prefix}_manifest.json"
if not path.exists() or not manifest_path.exists():
    st.warning("Run python make_demo.py for practice data, or follow the CMS import instructions in README.md.")
    st.stop()
try:
    peer_bytes = path.read_bytes()
    manifest = json.loads(manifest_path.read_text())
    if digest(peer_bytes) != manifest["subset_sha256"]:
        raise ValueError("Prepared file changed. Rerun the import rather than editing the evidence file.")
    peers = pd.read_csv(io.BytesIO(peer_bytes), dtype={c: str for c in ["service_code", "place_of_service", "region", "service_year", "modifier"]}, keep_default_na=False)
    for c in ["prevailing_offer", "provider_offer", "payer_offer"]:
        peers[c] = pd.to_numeric(peers[c], errors="coerce")
except Exception as exc:
    st.error(f"Cannot read prepared evidence: {exc}")
    st.stop()
if prefix == "demo":
    st.warning("SYNTHETIC DEMO: all historical amounts and case offers are invented. Do not present them as CMS findings.")
st.write(f"**Evidence:** {manifest['mode']} | **Reporting period:** {manifest['reporting_period']} | **Eligible rows:** {len(peers):,}")

with st.expander("Scope, matching rules, source audit, and limitations"):
    st.write("Scope: CPT 99284, POS 23, Single, Default Decision exactly No, and initiating party Health care provider. Initiating-party type is a coarse proxy, not a verified professional/facility claim classification.")
    st.write("Exact match: code + POS + geography + service year + modifier. If enabled, national fallback relaxes geography only, and only when national count meets the minimum. Unknown geography is never inferred from a city name.")
    st.write("Unusual = proposed amount strictly below the lower quantile or above the upper quantile of selected offers, only when minimum count is met. Percentile uses midrank for ties. Priorities: invalid/out of scope → weak/broadened comparison → unusual → no rule triggered. This is not a financial-exposure ranking.")
    st.write(LIMITATIONS)
    st.json(manifest)
    st.dataframe(peers.groupby(["region", "service_year", "modifier"]).size().reset_index(name="eligible_rows"), hide_index=True)

st.subheader("3. Load a synthetic case batch")
if (DATA / "sample_cases.csv").exists():
    st.download_button("Download 20 sample cases", (DATA / "sample_cases.csv").read_bytes(), "sample_cases.csv", "text/csv")
    use_sample = st.checkbox("Use included sample cases", value=True)
else:
    use_sample = False
upload = st.file_uploader("Upload CSV (synthetic cases only, maximum 500 rows)", type=["csv"])
raw = upload.getvalue() if upload else ((DATA / "sample_cases.csv").read_bytes() if use_sample else None)
if raw is None:
    st.stop()
if len(raw) > 2_000_000:
    st.error("Use a case CSV under 2 MB. The large CMS file belongs in data/raw, not this uploader.")
    st.stop()
try:
    cases = read_cases(raw)
except Exception as exc:
    st.error(f"Cannot read case CSV: {exc}")
    st.stop()
settings = {"minimum_peer_count": int(minimum), "tail_percentile": tail, "allow_national": fallback}
run_id = make_run_id(raw, manifest, settings)
results = run_batch(cases, peers, int(minimum), tail, fallback)
frame = pd.DataFrame(results)
reviews = load_reviews(run_id)
latest = reviews.drop_duplicates("case_id", keep="last") if not reviews.empty else reviews
if latest.empty:
    frame["review_decision"] = "Not reviewed"
else:
    frame["review_decision"] = frame.case_id.map(latest.set_index("case_id").decision).fillna("Not reviewed")
for numeric_column in ["proposed_offer", "n", "median", "percentile"]:
    frame[numeric_column] = pd.to_numeric(frame[numeric_column], errors="coerce")
st.markdown("### Review overview")

overview_1, overview_2, overview_3, overview_4 = st.columns(4)

overview_1.metric(
    "Cases submitted",
    f"{len(frame):,}",
)

overview_2.metric(
    "Cases requiring attention",
    f"{int(frame['flags'].ne('No rule triggered').sum()):,}",
)

overview_3.metric(
    "Human reviews saved",
    f"{len(latest):,}",
)

overview_4.metric(
    "Reference observations",
    f"{len(peers):,}",
)
st.subheader("Case review queue")
st.caption("Higher review priority appears first. 'No rule triggered' does not mean an offer is appropriate.")
queue_columns = [
    "case_id",
    "proposed_offer",
    "flags",
    "comparison",
    "n",
    "median",
    "percentile",
    "review_decision",
]

st.dataframe(
    frame[queue_columns],
    hide_index=True,
    width="stretch",
    column_config={
        "case_id": st.column_config.TextColumn(
            "Case",
            help="Synthetic, non-identifying case label",
            width="medium",
        ),
        "proposed_offer": st.column_config.NumberColumn(
            "Proposed offer",
            help="Invented offer amount supplied in the synthetic case file",
            format="dollar",
        ),
        "flags": st.column_config.TextColumn(
            "Review status",
            help="Deterministic rule-based review flag",
            width="medium",
        ),
        "comparison": st.column_config.TextColumn(
            "Comparison basis",
            help="Exact-region benchmark or disclosed national fallback",
            width="large",
        ),
        "n": st.column_config.NumberColumn(
            "Peers used",
            help="Number of historical observations used",
            format="localized",
        ),
        "median": st.column_config.NumberColumn(
            "Median selected offer",
            help="Median historical selected offer in the comparison group",
            format="dollar",
        ),
        "percentile": st.column_config.NumberColumn(
            "Offer percentile",
            help="Midrank percentile of the proposed offer",
            format="%.1f%%",
        ),
        "review_decision": st.column_config.TextColumn(
            "Human review",
            width="medium",
        ),
    },
)

case_options = frame.case_id.tolist()
preferred_default_case = "DEMO-19"

default_case_index = (
    case_options.index(preferred_default_case)
    if preferred_default_case in case_options
    else 0
)

selected_id = st.selectbox(
    "Open case",
    case_options,
    index=default_case_index,
)
result = next(r for r in results if r["case_id"] == selected_id)
st.subheader(f"Case {selected_id}")
flag_text = result["flags"]

if flag_text in {"Missing/invalid input", "Outside prototype scope"}:
    st.error(f"Action required: {flag_text}")
elif "Insufficient comparables" in flag_text:
    st.warning(f"Review limitation: {flag_text}")
elif "Geography broadened" in flag_text:
    st.warning(f"Broader comparison used: {flag_text}")
elif "Unusual offer" in flag_text:
    st.warning(f"Offer outside the configured benchmark range: {flag_text}")
else:
    st.success(
        "No deterministic review rule triggered. "
        "This does not establish that the offer is appropriate."
    )

st.caption(f"Comparison basis: {result['comparison']}")
st.write(result["note"])

def numeric_value(value):
    """Return a usable number or None for blank, missing, or invalid values."""
    if value is None:
        return None

    if isinstance(value, str) and not value.strip():
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if pd.isna(number):
        return None

    return number


def format_currency(value):
    number = numeric_value(value)

    if number is None:
        return "Unavailable"

    return f"${number:,.2f}"


def format_count(value):
    number = numeric_value(value)

    if number is None:
        return "Unavailable"

    return f"{int(number):,}"


def format_percentile(value):
    number = numeric_value(value)

    if number is None:
        return "Unavailable"

    return f"{number:.1f}%"

st.markdown("#### Case summary")

summary_1, summary_2, summary_3, summary_4 = st.columns(4)

summary_1.metric(
    "Proposed offer",
    format_currency(result["proposed_offer"]),
)
summary_2.metric(
    "Historical peers used",
    format_count(result["n"]),
)
summary_3.metric(
    "Median selected offer",
    format_currency(result["median"]),
)
summary_4.metric(
    "Offer percentile",
    format_percentile(result["percentile"]),
)
percentile_value = numeric_value(result["percentile"])

if percentile_value is not None:
    st.markdown("#### Position within the historical distribution")

    progress_value = min(max(percentile_value / 100, 0.0), 1.0)

    st.progress(
        progress_value,
        text=f"Historical selected-offer percentile: {percentile_value:.1f}%",
    )

    st.caption(
        f"The configured descriptive review band is P{tail} through "
        f"P{100-tail}. Values outside that band receive an unusual-offer flag "
        "only when the minimum peer-count requirement is met."
    )
else:
    st.info(
        "Offer position is unavailable because a valid benchmark and proposed "
        "offer are required."
    )

st.markdown("#### Historical benchmark range")
st.markdown("#### Historical benchmark range")

range_1, range_2, range_3 = st.columns(3)

range_1.metric(
    f"P{tail} selected offer",
    format_currency(result["p_low"]),
)
range_2.metric(
    "Median selected offer",
    format_currency(result["median"]),
)
range_3.metric(
    f"P{100-tail} selected offer",
    format_currency(result["p_high"]),
)

with st.expander("Additional comparison evidence"):
    additional_evidence = pd.DataFrame(
        {
            "Metric": [
                "Comparison basis",
                "Exact-region observations",
                "Total observations used",
                "Provider offer median",
                "Provider valid observations",
                "Payer offer median",
                "Payer valid observations",
            ],
            "Value": [
                result["comparison"],
                format_count(result["exact_n"]),
                format_count(result["n"]),
                format_currency(result["provider_median"]),
                format_count(result["provider_n"]),
                format_currency(result["payer_median"]),
                format_count(result["payer_n"]),
            ],
        }
    )

    st.dataframe(
        additional_evidence,
        hide_index=True,
        width="stretch",
    )

if result["n"] < minimum:
    st.warning("Insufficient comparables: descriptive numbers may be shown, but no unusual-offer classification is made.")
with st.expander("Exact input and computed evidence"):
    st.json(result)

ai_key = f"ai:{run_id}:{selected_id}"
if st.button("Draft explanation with local Ollama"):
    with st.spinner("Drafting locally; the first request may take up to three minutes..."):
        try:
            st.session_state[ai_key] = draft_note(result, manifest, model)
        except Exception as exc:
            st.error(f"Local AI could not respond: {exc}. Check Ollama is running and the model name matches ollama list. Your calculations still work.")
ai = st.session_state.get(ai_key)
if ai:
    st.warning("Unverified AI draft. Check each statement against the evidence before using it.")
    st.write(ai["text"])
    st.caption(f"Model: {ai['model']} | seconds: {ai['latency_seconds']} | output tokens: {ai['output_tokens']}")

with st.form(f"review:{run_id}:{selected_id}"):
    st.subheader("5. Save a human review")
    reviewer = st.text_input("Reviewer initials", max_chars=20)
    decision = st.selectbox("Decision about this comparison (not the payment)", ["Needs more information", "Comparison usable for descriptive context", "Comparison unsuitable"])
    rationale = st.text_area("Reason and checks performed", max_chars=2000)
    if st.form_submit_button("Save review"):
        if not reviewer.strip() or not rationale.strip():
            st.error("Enter reviewer initials and a reason.")
        else:
            save_review(run_id, result, reviewer.strip(), decision, rationale.strip(), ai)
            st.success("Review saved to local data/reviews.sqlite. Reloading queue...")
            st.rerun()
st.caption("Reviews append locally. Changing inputs, evidence, methodology version, or settings creates a different run. Old reviews remain stored but are not applied to the new run.")
if not reviews.empty:
    with st.expander("Review history for this run"):
        st.dataframe(reviews[["case_id", "reviewed_utc", "reviewer", "decision", "rationale"]], hide_index=True)

st.subheader("6. Export evidence packet")
record = {"run_id": run_id, "app_version": VERSION, "exported_utc": datetime.now(timezone.utc).isoformat(),
          "case_file_sha256": digest(raw), "settings": settings, "source": manifest,
          "limitations": LIMITATIONS, "results": results}
report = ("<!doctype html><html><meta charset='utf-8'><title>IDR evidence packet</title>"
          "<style>body{font:15px Arial;margin:32px}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #ccc}pre{white-space:pre-wrap}</style>"
          f"<h1>IDR review evidence packet</h1><p>{html.escape(mode)} — {html.escape(manifest['reporting_period'])}</p>"
          f"<p>{html.escape(LIMITATIONS)}</p><p>Run: {run_id}</p>"
          + frame.to_html(index=False, escape=True) + "<h2>Source and settings</h2><pre>"
          + html.escape(json.dumps({"source":manifest,"settings":settings}, indent=2)) + "</pre></html>")
packet = io.BytesIO()
with zipfile.ZipFile(packet, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("report.html", report)
    z.writestr("queue.csv", safe_csv(frame))
    z.writestr("evidence.json", json_bytes(record))
    z.writestr("reviews.csv", safe_csv(reviews))
    z.writestr("input_cases.csv", safe_csv(cases))
    z.writestr("reference_subset.csv", peer_bytes)
    z.writestr("ai_drafts.json", json_bytes({k: v for k,v in st.session_state.items() if k.startswith(f"ai:{run_id}:")}))
st.download_button("Download complete evidence packet", packet.getvalue(), f"idr_review_{run_id[:10]}.zip", "application/zip")
st.caption("The packet includes the exact prepared reference subset, settings, results, current-run reviews, and generated AI drafts. AI drafts remain explicitly unverified.")
