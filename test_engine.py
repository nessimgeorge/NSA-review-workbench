"""Known-answer checks. Run: python -m unittest -v"""
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from engine import benchmark, read_cases, make_run_id, save_review, load_reviews, safe_csv
from prepare_data import normalize, MAP, EXTRA


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.case = dict(case_id="TEST", service_code="99284", place_of_service="23",
                         region="A", service_year="2025", modifier="N/R", proposed_offer="250")
        self.peers = pd.DataFrame([dict(service_code="99284", place_of_service="23", region="A",
            service_year="2025", modifier="N/R", prevailing_offer=x, provider_offer=x, payer_offer=0)
            for x in [100, 200, 300, 400]])

    def test_known_quantiles_and_midrank(self):
        r = benchmark(self.case, self.peers, 4, 25)
        self.assertEqual((r["p_low"], r["median"], r["p_high"], r["percentile"]), (175, 250, 325, 50))
        self.assertEqual(r["payer_median"], 0)

    def test_ties(self):
        self.case["proposed_offer"] = "200"
        self.assertEqual(benchmark(self.case, self.peers, 4)["percentile"], 37.5)

    def test_insufficient_prevents_unusual(self):
        self.case["proposed_offer"] = "9999"
        r = benchmark(self.case, self.peers, 30)
        self.assertEqual(r["flags"], "Insufficient comparables")

    def test_unusual(self):
        self.case["proposed_offer"] = "9999"
        self.assertEqual(benchmark(self.case, self.peers, 4)["flags"], "Unusual offer")

    def test_missing_amount_and_infinity(self):
        for amount in ["", "NaN", "inf", "-1", "hello"]:
            with self.subTest(amount=amount):
                self.case["proposed_offer"] = amount
                self.assertEqual(benchmark(self.case, self.peers)["flags"], "Missing/invalid input")

    def test_national_is_opt_in_and_flagged(self):
        self.case["region"] = "B"
        self.assertEqual(benchmark(self.case, self.peers, 4)["n"], 0)
        r = benchmark(self.case, self.peers, 4, allow_national=True)
        self.assertEqual(r["n"], 4)
        self.assertIn("Geography broadened", r["flags"])

    def test_year_and_modifier_not_relaxed(self):
        for key, value in [("service_year", "2024"), ("modifier", "25")]:
            case = dict(self.case, **{key:value})
            self.assertEqual(benchmark(case, self.peers, 4, allow_national=True)["n"], 0)

    def test_scope(self):
        self.case["service_code"] = "99285"
        self.assertEqual(benchmark(self.case, self.peers)["flags"], "Outside prototype scope")

    def test_duplicate_case_id_rejected(self):
        raw = pd.DataFrame([self.case, self.case]).to_csv(index=False).encode()
        with self.assertRaises(ValueError):
            read_cases(raw)

    def test_run_changes_with_settings(self):
        self.assertNotEqual(make_run_id(b"a", {}, {"n":30}), make_run_id(b"a", {}, {"n":31}))

    def test_reviews_persist_and_are_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory)/"reviews.sqlite"
            save_review("run-a", benchmark(self.case, self.peers), "GN", "Needs more information", "Too few peers", db=db)
            self.assertEqual(len(load_reviews("run-a", db)), 1)
            self.assertEqual(len(load_reviews("run-b", db)), 0)

    def test_import_excludes_unknown_defaults_and_suppressed_values(self):
        base = {c:"N/R" for c in list(MAP)+EXTRA}
        base.update({"Service Code":"99284", "Place of Service Code":"23", "Type of Service Code":"CPT",
                     "Dispute Line Item Type":"Single", "Default Decision":"No", "Initiating Party":"Health care provider",
                     "Year of Service":"2025", "Geographical Region":"A", "Prevailing Offer":"100",
                     "Provider/Facility Offer":"^", "Health Plan/Issuer Offer":"0"})
        rows = [base, dict(base), dict(base, **{"Default Decision":"N/R"}), dict(base, **{"Prevailing Offer":"^"})]
        clean, audit = normalize(pd.DataFrame(rows))
        self.assertEqual(len(clean), 2)  # Identical observations are retained, not deduplicated.
        self.assertTrue(clean.provider_offer.isna().all())
        self.assertTrue(clean.payer_offer.eq(0).all())
        self.assertEqual(sum(audit[k] for k in ["retained","outside_scope","invalid_prevailing_offer","missing_match_fields"]), audit["read"])

    def test_export_neutralizes_formulas(self):
        text = safe_csv(pd.DataFrame({"note":["=1+1"]})).decode("utf-8-sig")
        self.assertIn("'=1+1", text)


if __name__ == "__main__":
    unittest.main()
