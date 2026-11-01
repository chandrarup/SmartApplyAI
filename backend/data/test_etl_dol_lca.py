from __future__ import annotations

import csv

from backend.data.etl_dol_lca import aggregate_lca_records, load_lca_csv, normalize_domain, normalize_employer, normalize_soc


def test_normalizers_produce_stable_lookup_keys():
    assert normalize_employer("  Acme, Inc.  ") == "acme"
    assert normalize_domain(" Machine Learning / AI ") == "machine learning ai"
    assert normalize_soc("15-1252.00") == "15-1252"


def test_load_and_aggregate_synthetic_lca_csv(tmp_path):
    path = tmp_path / "lca.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["CASE_STATUS", "EMPLOYER_NAME", "SOC_CODE", "SOC_TITLE", "RECEIVED_DATE", "DECISION_DATE"])
        writer.writeheader()
        writer.writerows([
            {"CASE_STATUS": "Certified", "EMPLOYER_NAME": "Acme, Inc.", "SOC_CODE": "15-1252.00", "SOC_TITLE": "Software Developers", "RECEIVED_DATE": "2024-01-03", "DECISION_DATE": "2024-02-01"},
            {"CASE_STATUS": "Certified", "EMPLOYER_NAME": "ACME INC", "SOC_CODE": "15-1252", "SOC_TITLE": "Software Developers", "RECEIVED_DATE": "2024-03-03", "DECISION_DATE": "2024-04-01"},
            {"CASE_STATUS": "Denied", "EMPLOYER_NAME": "Other LLC", "SOC_CODE": "11-1021", "SOC_TITLE": "General Managers", "RECEIVED_DATE": "2024-01-01", "DECISION_DATE": "2024-02-01"},
        ])
    records = load_lca_csv(path)
    assert records[0]["employer"] == "acme"
    assert records[0]["soc"] == "15-1252"
    aggregate = aggregate_lca_records(records)
    assert aggregate["acme"]["total_cases"] == 2
    assert aggregate["acme"]["certified_cases"] == 2
    assert aggregate["acme"]["soc_counts"]["15-1252"] == 2
    assert aggregate["other"]["denied_cases"] == 1
