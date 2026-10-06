"""Deterministic evidence calculation. No model makes numerical decisions here."""
import hashlib
import io
import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DB = DATA / "reviews.sqlite"
REQUIRED = ["case_id", "service_code", "place_of_service", "region",
            "service_year", "modifier", "proposed_offer"]
LIMITATIONS = (
    "Historical decided-dispute benchmarking only; no fair-payment, settlement, "
    "win-probability, or savings recommendation. These records are selected disputes, "
    "not the full claims population. Matching cannot adjust for all clinical severity, "
    "provider, payer, or contract differences. Counts are line items, not independent "
    "providers or patients. Identical rows are retained because the offers file lacks "
    "unique line-item IDs. N/R modifiers are unknown, not proof of no modifier. "
    "National fallback mixes geographic markets. Minimum count and tail thresholds "
    "are demonstration rules, not actuarially validated credibility standards."
)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False).encode("utf-8")


def finite_number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def read_cases(raw):
    """Read strings to preserve codes. Missing cells become empty strings."""
    frame = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)
    frame.columns = frame.columns.str.strip()
    missing = sorted(set(REQUIRED) - set(frame.columns))
    if missing:
        raise ValueError("Missing column(s): " + ", ".join(missing))
    if not 1 <= len(frame) <= 500:
        raise ValueError("Use 1 to 500 cases per batch for this prototype.")
    frame = frame[REQUIRED].copy()
    for c in REQUIRED:
        frame[c] = frame[c].str.strip()
    if frame.case_id.eq("").any() or frame.case_id.duplicated().any():
        raise ValueError("Every case_id must be nonempty and unique within the batch.")
    if not frame.case_id.str.fullmatch(r"[A-Za-z0-9_-]{1,60}").all():
        raise ValueError("Use case IDs containing only letters, numbers, _ and - (max 60).")
    return frame


def benchmark(case, peers, min_n=30, tail=10, allow_national=False):
    out = dict(case)
    out.update(flags="", comparison="None", exact_n=0, n=0,
               p_low=None, median=None, p_high=None, percentile=None,
               provider_median=None, payer_median=None,
               provider_n=0, payer_n=0, priority=0, note="")
    offer = finite_number(case["proposed_offer"])
    missing = [c for c in REQUIRED[1:] if not str(case[c]).strip()]
    if missing or offer is None or offer < 0:
        out.update(flags="Missing/invalid input", priority=3,
                   note="Fix inputs: " + ", ".join(missing or ["proposed_offer"]))
        return out
    if case["service_code"] != "99284" or case["place_of_service"] != "23":
        out.update(flags="Outside prototype scope", priority=3,
                   note="This version supports CPT 99284, POS 23 only.")
        return out
    if not str(case["service_year"]).isdigit() or len(str(case["service_year"])) != 4:
        out.update(flags="Missing/invalid input", priority=3, note="Use a four-digit service_year.")
        return out
    eligible = peers[
        peers.service_code.eq(case["service_code"])
        & peers.place_of_service.eq(case["place_of_service"])
        & peers.service_year.eq(case["service_year"])
        & peers.modifier.eq(case["modifier"])
    ]
    exact = eligible[eligible.region.eq(case["region"])]
    out["exact_n"] = len(exact)
    selected = exact
    out["comparison"] = "Exact region"
    flags = []
    if len(exact) < min_n and allow_national and len(eligible) >= min_n:
        selected = eligible
        out["comparison"] = "National fallback — geography relaxed"
        flags.append("Geography broadened")
    out["n"] = len(selected)
    out["proposed_offer"] = offer
    if selected.empty:
        flags.append("Insufficient comparables")
        out.update(flags="; ".join(flags), priority=2,
                   note="No matching observations. No benchmark or unusual-offer flag generated.")
        return out
    values = selected.prevailing_offer
    out["p_low"] = float(values.quantile(tail / 100, interpolation="linear"))
    out["median"] = float(values.median())
    out["p_high"] = float(values.quantile(1 - tail / 100, interpolation="linear"))
    for label in ["provider", "payer"]:
        series = selected[f"{label}_offer"].dropna()
        out[f"{label}_n"] = len(series)
        out[f"{label}_median"] = float(series.median()) if len(series) else None
    # Midrank: half weight to ties. This is descriptive, not a probability.
    out["percentile"] = float(100 * ((values < offer).sum() + .5 * (values == offer).sum()) / len(values))
    if len(selected) < min_n:
        flags.append("Insufficient comparables")
    elif offer < out["p_low"] or offer > out["p_high"]:
        flags.append("Unusual offer")
    out["flags"] = "; ".join(flags) or "No rule triggered"
    out["priority"] = 2 if any(x in flags for x in ["Insufficient comparables", "Geography broadened"]) else (1 if flags else 0)
    out["note"] = ("Modifier is unreported; residual comparability risk. " if case["modifier"] == "N/R" else "")
    out["note"] += "Offer position is relative to historical selected offers; it is not a payment recommendation."
    return out


def run_batch(cases, peers, min_n, tail, allow_national):
    results = [benchmark(c, peers, min_n, tail, allow_national) for c in cases.to_dict("records")]
    return sorted(results, key=lambda r: (-r["priority"], r["case_id"]))


def make_run_id(raw, manifest, settings):
    return digest(raw + json_bytes(manifest) + json_bytes(settings) + VERSION.encode())


def connection(db=DB):
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE IF NOT EXISTS reviews (
        event_id INTEGER PRIMARY KEY, run_id TEXT, case_id TEXT, reviewed_utc TEXT,
        reviewer TEXT, decision TEXT, rationale TEXT, evidence_json TEXT, ai_json TEXT)""")
    return con


def save_review(run_id, result, reviewer, decision, rationale, ai=None, db=DB):
    con = connection(db)
    with con:
        con.execute("INSERT INTO reviews VALUES (NULL,?,?,?,?,?,?,?,?)",
                    (run_id, result["case_id"], datetime.now(timezone.utc).isoformat(),
                     reviewer, decision, rationale, json_bytes(result).decode(),
                     json_bytes(ai).decode() if ai else ""))
    con.close()


def load_reviews(run_id, db=DB):
    con = connection(db)
    rows = pd.read_sql_query("SELECT * FROM reviews WHERE run_id=? ORDER BY event_id", con, params=[run_id])
    con.close()
    return rows


def safe_csv(frame):
    # Neutralize spreadsheet formula prefixes on exported text cells.
    frame = frame.copy()
    for col in frame.select_dtypes(include=["object", "string"]):
        frame[col] = frame[col].map(lambda x: "'" + x if isinstance(x, str) and x.lstrip().startswith(("=", "+", "-", "@")) else x)
    return frame.to_csv(index=False).encode("utf-8-sig")
