"""Run the frozen golden cases against the current scorer without editing fixtures."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import streamlit as st
from openai import OpenAI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from minimalist_mvp.scorer import ReviewContext, ReviewEvidence, score_creative  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", action="append", help="Run one case ID; repeat for several. Default: all runnable cases.")
    args = parser.parse_args()
    golden = json.loads((ROOT / "evals/minimalist-scorer-evals-v1.json").read_text())
    if not golden["metadata"]["created_before_scorer_implementation_and_tuning"]:
        raise ValueError("The golden set is not marked as pre-scorer.")
    api_key = str(st.secrets.get("OPENAI_API_KEY", "")).strip()
    if not api_key:
        print("OpenAI API key is not configured on the server.")
        return 2
    client = OpenAI(api_key=api_key, timeout=60, max_retries=0)
    selected = set(args.case or [])
    failures = 0
    run_count = 0
    for case in golden["cases"]:
        if selected and case["case_id"] not in selected:
            continue
        if case["input_type"] == "visual_description":
            print(f"{case['case_id']}: SKIP (description only; no rendered asset)")
            continue
        refs = case["evidence_context"].get("fixture_refs", [])
        evidence = []
        for ref in refs:
            fixture = golden["evidence_fixtures"][ref]
            wording = fixture.get("source_wording", [fixture.get("exact_result", "")])
            for index, value in enumerate(wording, 1):
                evidence.append(ReviewEvidence(
                    evidence_id=f"{ref}-{index}", exact_text=value,
                    source_url=f"hypothetical-fixture:{ref}", captured_at="fixture-time",
                ))
        note = case["evidence_context"].get("note", "")
        page_repeats_claim = bool(re.search(
            r"\bproduct page\b.*\brepeat(?:s|ed)?\b", note, re.IGNORECASE,
        ))
        if page_repeats_claim and case.get("ad_input"):
            # These frozen cases provide page wording but explicitly withhold the
            # underlying study; the page statement is not study substantiation.
            evidence.append(ReviewEvidence(
                evidence_id="PAGE-1", exact_text=case["ad_input"],
                source_url=f"hypothetical-product-page:{case['case_id']}",
                captured_at="fixture-time",
            ))
        if re.search(r"\b(?:supplied|provided)\b.*\b(?:document|report)\b", note,
                     re.IGNORECASE):
            evidence.append(ReviewEvidence(
                evidence_id="DOCUMENT-1", exact_text=note,
                source_url=f"hypothetical-fixture:{case['case_id']}", captured_at="fixture-time",
            ))
        context = ReviewContext(
            product_name="Minimalist Demo Serum" if "P" in refs else "",
            variant="30 mL" if "P" in refs else "",
            product_image_reference="Synthetic Demo Serum 30 mL pack" if "P" in refs else "",
            review_notes=note,
            evidence=evidence, complete_eligible_set="P" in refs and not page_repeats_claim,
        )
        image_path = case.get("fixture_path")
        image_bytes = (ROOT / image_path).read_bytes() if image_path else None
        report = score_creative(client, ad_copy=case.get("ad_input", ""),
                                image_bytes=image_bytes, context=context)
        expected = {(item["rule_id"], item["status"]) for item in case["expected_findings"]}
        actual = {(item.rule_id, item.status.value) for item in report.findings}
        passed = (report.overall.value == case["expected_overall"] and
                  report.export_allowed == case["expected_exportable"] and
                  expected.issubset(actual))
        if not passed:
            failures += 1
        run_count += 1
        print(f"{case['case_id']}: {'OK' if passed else 'MISMATCH'} "
              f"expected={case['expected_overall']} actual={report.overall.value} "
              f"findings={sorted(actual)}")
        if not passed:
            for finding in report.findings:
                print(f"  {finding.rule_id} {finding.status.value}: {finding.reason}")
    unknown = selected - {case["case_id"] for case in golden["cases"]}
    if unknown:
        print(f"Unknown case IDs: {sorted(unknown)}")
        return 2
    print(f"{run_count - failures}/{run_count} matched; {failures} mismatch(es).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
