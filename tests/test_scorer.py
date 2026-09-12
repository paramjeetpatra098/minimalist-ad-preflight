import json
import unittest
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from minimalist_mvp.review import OverallStatus
from minimalist_mvp.scorer import (ReviewContext, ReviewEvidence, ScorerFinding,
                                   dimension_status, load_rulebook, score_creative)
from minimalist_mvp.review import FindingOutcome


class FakeResponses:
    def __init__(self, issues, readability="NO_IMAGE", observed=""):
        self.payload = {"observed_text": observed, "image_readability": readability,
                        "issues": issues}
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(output_text=json.dumps(self.payload))


def issue(rule_id, basis="KNOWN_VIOLATION", evidence_ids=None):
    return {"rule_id": rule_id, "basis": basis, "flagged_element": "flagged copy",
            "reason": "reason", "evidence_ids": evidence_ids or [],
            "suggested_fix": "fix"}


class ScorerTests(unittest.TestCase):
    def setUp(self):
        self.context = ReviewContext(
            product_name="Demo Serum", complete_eligible_set=True,
            evidence=[ReviewEvidence(evidence_id="EV-001", exact_text="2% Salicylic Acid",
                                     source_url="https://beminimalist.co/products/demo",
                                     captured_at="2026-09-12T00:00:00")],
        )

    def score(self, issues, *, context=None, readability="NO_IMAGE", image_bytes=None):
        responses = FakeResponses(issues, readability)
        report = score_creative(SimpleNamespace(responses=responses),
                                ad_copy="Demo Serum. Shop now.", image_bytes=image_bytes,
                                context=context or self.context)
        return report, responses.kwargs

    def test_rulebook_ids_and_sources_are_valid(self):
        self.assertEqual(len(load_rulebook()["rules"]), 19)

    def test_dimension_status_uses_highest_relevant_severity(self):
        findings = [
            ScorerFinding(rule_id="TONE-003", area="Brand Tone", status=FindingOutcome.WARN,
                          flagged_element="magic", reason="vague", suggested_fix="Be specific", material=False),
            ScorerFinding(rule_id="CLAIM-006", area="Policy & Claims",
                          status=FindingOutcome.NOT_ASSESSABLE, flagged_element="60%",
                          reason="No study", suggested_fix="Supply study"),
            ScorerFinding(rule_id="META-001", area="Policy & Claims", status=FindingOutcome.BLOCK,
                          flagged_element="You have acne", reason="attribute", suggested_fix="Rewrite"),
        ]
        self.assertEqual(dimension_status(findings, "Policy & Claims"), "BLOCK")
        self.assertEqual(dimension_status(findings, "Brand Tone"), "WARN")
        self.assertEqual(dimension_status(findings, "Brand Language"), "PASS")
        self.assertEqual(dimension_status(findings[:2], "Policy & Claims"), "REVIEW")
        nonmaterial_gap = ScorerFinding(
            rule_id="LANG-001", area="Brand Language", status=FindingOutcome.NOT_ASSESSABLE,
            flagged_element="Unreadable styling", reason="Unclear", suggested_fix="Improve legibility",
            material=False,
        )
        self.assertEqual(dimension_status([nonmaterial_gap], "Brand Language"), "WARN")

    def test_clean_creative_passes_and_schema_enumerates_rule_ids(self):
        report, request = self.score([])
        self.assertEqual(report.overall, OverallStatus.PASS)
        self.assertTrue(report.export_allowed)
        self.assertFalse(request["store"])
        self.assertIn("CLAIM-001", request["text"]["format"]["schema"]["properties"]["issues"]["items"]["properties"]["rule_id"]["enum"])
        self.assertNotIn("TONE-004", request["text"]["format"]["schema"]["properties"]["issues"]["items"]["properties"]["rule_id"]["enum"])

    def test_tone_warning_is_non_gating(self):
        report, _ = self.score([issue("TONE-003")])
        self.assertEqual(report.overall, OverallStatus.PASS_WITH_WARNINGS)
        self.assertTrue(report.export_allowed)
        self.assertEqual(report.findings[0].area, "Brand Tone")

    def test_missing_context_brand_language_finding_is_warn_not_not_assessable(self):
        report, _ = self.score([issue("LANG-002", "MISSING_CONTEXT")])
        self.assertEqual(report.overall, OverallStatus.PASS_WITH_WARNINGS)
        self.assertTrue(report.export_allowed)
        self.assertEqual(report.findings[0].status, FindingOutcome.WARN)

    def test_known_wrong_concentration_blocks(self):
        report, _ = self.score([issue("CLAIM-001", evidence_ids=["EV-001"])])
        self.assertEqual(report.overall, OverallStatus.BLOCK)
        self.assertFalse(report.export_allowed)
        self.assertIn("2% Salicylic Acid", report.findings[0].evidence[0])

    def test_obvious_numeric_mismatch_blocks_even_when_model_omits_it(self):
        responses = FakeResponses([])
        report = score_creative(SimpleNamespace(responses=responses),
                                ad_copy="5% Salicylic Acid. Shop now.", context=self.context)
        self.assertEqual(report.overall, OverallStatus.BLOCK)
        self.assertEqual(report.findings[0].rule_id, "CLAIM-001")

    def test_missing_external_context_reviews_not_passes(self):
        report, _ = self.score([], context=ReviewContext())
        self.assertEqual(report.overall, OverallStatus.REVIEW)
        self.assertEqual(report.findings[0].rule_id, "SYSTEM-001")
        self.assertEqual(report.findings[0].status.value, "NOT_ASSESSABLE")
        self.assertFalse(report.export_allowed)

    def test_missing_substantiation_is_not_block(self):
        report, _ = self.score([issue("CLAIM-006", "MISSING_CONTEXT")])
        self.assertEqual(report.overall, OverallStatus.REVIEW)
        self.assertEqual(report.findings[0].status.value, "NOT_ASSESSABLE")

    def test_unlisted_benefit_is_block_for_complete_generation_allowlist(self):
        report, _ = self.score([issue("CLAIM-002", "MISSING_CONTEXT")])
        self.assertEqual(report.overall, OverallStatus.BLOCK)
        self.assertEqual(report.findings[0].status.value, "BLOCK")

    def test_unsubstantiated_ranking_is_review_not_known_false(self):
        report, _ = self.score([issue("CLAIM-007", evidence_ids=["EV-001"])])
        self.assertEqual(report.overall, OverallStatus.REVIEW)
        self.assertEqual(report.findings[0].status.value, "NOT_ASSESSABLE")

    def test_vague_beauty_magic_is_a_stable_warning(self):
        responses = FakeResponses([])
        report = score_creative(SimpleNamespace(responses=responses),
                                ad_copy="Demo Serum — a little skincare magic.",
                                context=self.context)
        self.assertEqual(report.overall, OverallStatus.PASS_WITH_WARNINGS)
        self.assertEqual(report.findings[0].rule_id, "TONE-003")

    def test_other_vague_hyperbole_is_warn_only(self):
        for copy in ("A miracle for your skin", "Transform your skin like never before"):
            with self.subTest(copy=copy):
                report = score_creative(SimpleNamespace(responses=FakeResponses([])),
                                        ad_copy=copy, context=self.context)
                self.assertEqual(report.overall, OverallStatus.PASS_WITH_WARNINGS)
                self.assertTrue(report.export_allowed)
                self.assertEqual(report.findings[0].rule_id, "TONE-003")
                self.assertEqual(report.findings[0].status, FindingOutcome.WARN)

    def test_guarantee_without_exact_evidence_is_block(self):
        responses = FakeResponses([])
        report = score_creative(SimpleNamespace(responses=responses),
                                ad_copy="Guaranteed blackhead-free skin in 7 days",
                                context=self.context)
        self.assertEqual(report.overall, OverallStatus.BLOCK)
        self.assertEqual(report.findings[0].rule_id, "CLAIM-004")

    def test_guarantee_checks_the_whole_promise_not_only_the_keyword(self):
        context = self.context.model_copy(deep=True)
        context.evidence.append(ReviewEvidence(
            evidence_id="EV-002", exact_text="Some shipping results guaranteed",
            source_url="https://example.test/shipping", captured_at="2026-09-12T00:00:00",
        ))
        report = score_creative(SimpleNamespace(responses=FakeResponses([])),
                                ad_copy="Clear skin guaranteed", context=context)
        self.assertEqual(report.overall, OverallStatus.BLOCK)
        self.assertEqual(report.findings[0].rule_id, "CLAIM-004")

    def test_scientific_proof_without_underlying_study_is_review(self):
        report = score_creative(SimpleNamespace(responses=FakeResponses([])),
                                ad_copy="Scientifically proven results", context=self.context)
        self.assertEqual(report.overall, OverallStatus.REVIEW)
        self.assertFalse(report.export_allowed)
        self.assertEqual(report.findings[0].rule_id, "CLAIM-005")
        self.assertEqual(report.findings[0].status, FindingOutcome.NOT_ASSESSABLE)

    def test_proof_contradiction_uses_supplied_evidence_generally(self):
        for copy, wording in (
            ("Clinically tested to firm skin", "Self-reported feedback only; not a clinical trial"),
            ("Scientifically verified improvement", "Marketing survey, not a scientific study"),
        ):
            with self.subTest(copy=copy):
                context = self.context.model_copy(deep=True)
                context.evidence.append(ReviewEvidence(
                    evidence_id="EV-002", exact_text=wording,
                    source_url="https://example.test/evidence", captured_at="2026-09-12T00:00:00",
                ))
                report = score_creative(SimpleNamespace(responses=FakeResponses([])),
                                        ad_copy=copy, context=context)
                self.assertEqual(report.overall, OverallStatus.BLOCK)
                self.assertEqual(report.findings[0].rule_id, "CLAIM-005")
                self.assertIn(wording, report.findings[0].evidence[0])

    def test_known_survey_not_clinical_is_block(self):
        responses = FakeResponses([issue("CLAIM-005", "MISSING_CONTEXT")])
        context = self.context.model_copy(deep=True)
        context.evidence.append(ReviewEvidence(
            evidence_id="EV-002", exact_text="Consumer survey, not clinical study",
            source_url="https://example.test/survey", captured_at="2026-09-12T00:00:00",
        ))
        report = score_creative(SimpleNamespace(responses=responses),
                                ad_copy="Clinically proven to reduce blackheads",
                                context=context)
        self.assertEqual(report.overall, OverallStatus.BLOCK)
        self.assertEqual(report.findings[0].rule_id, "CLAIM-005")

    def test_exceptional_claim_without_exact_evidence_blocks(self):
        report, _ = self.score([issue("CLAIM-004")], context=ReviewContext())
        self.assertEqual(report.overall, OverallStatus.BLOCK)

    def test_unknown_rule_id_fails_closed(self):
        report, _ = self.score([issue("INVENTED-001")])
        self.assertEqual(report.overall, OverallStatus.REVIEW)
        self.assertEqual(report.findings[0].rule_id, "SYSTEM-001")

    def test_image_is_sent_and_unreadable_content_reviews(self):
        image = BytesIO()
        Image.new("RGB", (100, 100), "white").save(image, format="PNG")
        responses = FakeResponses([], "PARTIAL", "Demo Serum")
        report = score_creative(SimpleNamespace(responses=responses), ad_copy="Demo Serum",
                                image_bytes=image.getvalue(), reference_image_bytes=image.getvalue(),
                                context=self.context)
        request = responses.kwargs
        self.assertEqual(report.overall, OverallStatus.REVIEW)
        self.assertEqual(request["input"][1]["content"][1]["type"], "input_image")
        self.assertEqual(request["input"][1]["content"][3]["type"], "input_image")

    def test_image_without_transcription_cannot_pass(self):
        image = BytesIO()
        Image.new("RGB", (100, 100), "white").save(image, format="PNG")
        report, _ = self.score([], readability="READABLE", image_bytes=image.getvalue())
        self.assertEqual(report.overall, OverallStatus.REVIEW)

    def test_duplicate_qualifier_warning_is_removed(self):
        report, _ = self.score([issue("CLAIM-009", evidence_ids=["EV-001"]),
                                issue("LANG-004")])
        self.assertEqual([f.rule_id for f in report.findings], ["CLAIM-009"])


if __name__ == "__main__":
    unittest.main()
