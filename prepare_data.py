"""Run once to make a small, auditable subset of the CMS offers CSV."""
import argparse
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from engine import DATA, digest, json_bytes

MAP = {"Service Code": "service_code", "Place of Service Code": "place_of_service",
       "Geographical Region": "region", "Year of Service": "service_year",
       "Service Code Modifier(s)": "modifier", "Prevailing Offer": "prevailing_offer",
       "Provider/Facility Offer": "provider_offer", "Health Plan/Issuer Offer": "payer_offer"}
EXTRA = ["Type of Service Code", "Dispute Line Item Type", "Default Decision", "Initiating Party"]


def normalize(chunk):
    chunk = chunk.copy()
    for c in chunk.columns:
        chunk[c] = chunk[c].astype(str).str.strip()
    masks = [chunk["Service Code"].eq("99284"), chunk["Place of Service Code"].eq("23"),
             chunk["Type of Service Code"].eq("CPT"), chunk["Dispute Line Item Type"].eq("Single"),
             chunk["Default Decision"].eq("No"), chunk["Initiating Party"].eq("Health care provider")]
    keep = masks[0]
    for m in masks[1:]:
        keep = keep & m
    selected = chunk.loc[keep, list(MAP)].rename(columns=MAP).copy()
    before = len(selected)
    for c in ["prevailing_offer", "provider_offer", "payer_offer"]:
        selected[c] = pd.to_numeric(selected[c], errors="coerce")
        selected.loc[~selected[c].map(lambda x: pd.notna(x) and float('-inf') < x < float('inf')) | (selected[c] < 0), c] = float("nan")
    # Missing/suppressed amounts are not zero. Positive selected offer required.
    selected = selected[selected.prevailing_offer.gt(0)]
    bad_amount = before - len(selected)
    before = len(selected)
    selected = selected[selected.service_year.str.fullmatch(r"\d{4}")
                        & ~selected.region.isin(["", "N/R", "N/A", "^"])
                        & selected.modifier.ne("")]
    return selected, Counter(read=len(chunk), outside_scope=len(chunk)-int(keep.sum()),
                             invalid_prevailing_offer=bad_amount,
                             missing_match_fields=before-len(selected), retained=len(selected))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--period", required=True, help="Reporting period from the file label, e.g. 2025-Q4")
    args = parser.parse_args()
    if not args.input.exists():
        raise SystemExit("File not found. Put the unzipped QPA and offers CSV in data/raw first.")
    # Read exact headers rather than guessing a join to other PUFs.
    header = pd.read_csv(args.input, nrows=0).columns
    missing = set(MAP).union(EXTRA) - set(header)
    if missing:
        raise SystemExit("Wrong file or changed CMS schema. Missing: " + ", ".join(sorted(missing)))
    DATA.mkdir(exist_ok=True)
    parts, counts = [], Counter()
    for i, chunk in enumerate(pd.read_csv(args.input, dtype=str, keep_default_na=False,
                                         usecols=list(MAP)+EXTRA, chunksize=50000)):
        selected, audit = normalize(chunk)
        parts.append(selected)
        counts.update(audit)
        print(f"Read {counts['read']:,} rows; retained {counts['retained']:,}.", flush=True)
    frame = pd.concat(parts, ignore_index=True)
    if frame.empty:
        raise SystemExit("No eligible rows. Existing prepared data was not changed.")
    raw = frame.to_csv(index=False).encode()
    import hashlib
    h = hashlib.sha256()
    with args.input.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    manifest = {"mode": "CMS public data", "reporting_period": args.period,
                "source_filename": args.input.name, "source_sha256": h.hexdigest(),
                "subset_sha256": digest(raw), "created_utc": datetime.now(timezone.utc).isoformat(),
                "source_url": "https://www.cms.gov/nosurprises/policies-and-resources/reports",
                "filters": "CPT 99284; POS 23; Single; Default Decision exactly No; Initiating Party Health care provider; positive numeric Prevailing Offer; known region and service year; nonempty modifier",
                "counts": dict(counts), "duplicates": "Retained: source lacks unique line-item IDs"}
    (DATA / "cms_peers.csv").write_bytes(raw)
    (DATA / "cms_manifest.json").write_bytes(json_bytes(manifest))
    print("Done. In the app, select CMS public data. See cms_manifest.json for the import audit.")


if __name__ == "__main__":
    main()
