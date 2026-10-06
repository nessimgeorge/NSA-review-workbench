"""Invented values only. Reproducible practice inputs, not CMS estimates."""
import random
import pandas as pd
from engine import DATA, digest, json_bytes


def main():
    DATA.mkdir(exist_ok=True)
    rng = random.Random(21)
    records = []
    for region, n, center in [("Atlanta-Sandy Springs-Alpharetta, GA", 100, 600),
                              ("Minneapolis-St. Paul-Bloomington, MN-WI", 8, 680),
                              ("Chicago-Naperville-Elgin, IL-IN-WI", 60, 650)]:
        for _ in range(n):
            provider = round(rng.uniform(center*.8, center*1.4), 2)
            payer = round(rng.uniform(center*.35, center*.7), 2)
            records.append(dict(service_code="99284", place_of_service="23", region=region,
                                service_year="2025", modifier="N/R", provider_offer=provider,
                                payer_offer=payer, prevailing_offer=provider if rng.random()<.7 else payer))
   raw = (
    pd.DataFrame(records)
    .to_csv(index=False, lineterminator="\n")
    .encode("utf-8")
)
    (DATA / "demo_peers.csv").write_bytes(raw)
    (DATA / "demo_manifest.json").write_bytes(json_bytes({"mode": "SYNTHETIC DEMO", "reporting_period": "Invented practice data",
        "source_filename": "make_demo.py seed 21", "source_sha256": digest(raw), "subset_sha256": digest(raw),
        "filters": "Invented eligible peers; no actual CMS observations", "counts": {"retained":len(records)}}))
    cases = []
    for i in range(20):
        cases.append(dict(case_id=f"DEMO-{i+1:02d}", service_code="99284", place_of_service="23",
                          region="Atlanta-Sandy Springs-Alpharetta, GA", service_year="2025", modifier="N/R",
                          proposed_offer=500 if i % 2 == 0 else 550))
    cases[0]["proposed_offer"] = 50
    cases[1]["proposed_offer"] = 2000
    cases[2]["region"] = "Minneapolis-St. Paul-Bloomington, MN-WI"
    cases[3]["proposed_offer"] = ""
    cases[4]["region"] = "Unknown practice region"
    cases[5]["service_code"] = "99285"
    cases[6]["proposed_offer"] = -20
    cases[7]["service_year"] = "2030"
  pd.DataFrame(cases).to_csv(
    DATA / "sample_cases.csv",
    index=False,
    lineterminator="\n",
)
    (DATA / "raw").mkdir(exist_ok=True)
    print("Created invented peers and 20 synthetic cases in data/.")


if __name__ == "__main__":
    main()
