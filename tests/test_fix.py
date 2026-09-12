import json
import unittest
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from minimalist_mvp.fix import (
    FixUnavailable, creative_copy, generated_draft_with_fix, replace_flagged,
    revise_external, revise_generated, suggest_replacement,
)
from minimalist_mvp.generation import GeneratedCreative, build_generation_allowlist
from minimalist_mvp.review import FindingOutcome, OverallStatus
from minimalist_mvp.scorer import ReviewContext, ReviewEvidence, ScorerFinding, score_creative
from tests.test_generation import assessment, valid_draft


class ReviewResponses:
    def __init__(self, replacement=""):
        self.replacement = replacement
        self.review_calls = []

    def create(self, **kwargs):
        schema_name = kwargs["text"]["format"]["name"]
        if schema_name == "minimalist_copy_fix":
            return SimpleNamespace(output_text=json.dumps({"replacement": self.replacement}))
        payload = json.loads(kwargs["input"][1]["content"][0]["text"])
        copy = payload["ad_copy"]
        self.review_calls.append((copy, kwargs["input"][1]["content"]))
        issues = []
        if "Guaranteed to cure acne" in copy:
            issues.append({"rule_id": "CLAIM-004", "basis": "KNOWN_VIOLATION",
                           "flagged_element": "Guaranteed to cure acne", "reason": "Unsupported guarantee",
                           "evidence_ids": [], "suggested_fix": "Remove this claim"})
        if "Clinically proven" in copy or "Scientifically proven" in copy:
            issues.append({"rule_id": "CLAIM-005", "basis": "MISSING_CONTEXT",
                           "flagged_element": "proven", "reason": "Missing study",
                           "evidence_ids": [], "suggested_fix": "Provide study or remove claim"})
        return SimpleNamespace(output_text=json.dumps({"observed_text": copy,
            "image_readability": "READABLE" if len(kwargs["input"][1]["content"]) > 1 else "NO_IMAGE",
            "issues": issues}))


def finding(flagged, rule="CLAIM-004", status=FindingOutcome.BLOCK):
    return ScorerFinding(rule_id=rule, area="Policy & Claims", status=status,
                         flagged_element=flagged, reason="Unsupported claim",
                         suggested_fix="Remove or replace with supported wording")


class FixTests(unittest.TestCase):
    def setUp(self):
        self.responses = ReviewResponses()
        self.client = SimpleNamespace(responses=self.responses)
        self.context = ReviewContext(product_name="Test Serum", complete_eligible_set=True,
            evidence=[ReviewEvidence(evidence_id="EV-002", exact_text="2% active helps reduce excess oil")])
        out = BytesIO()
        Image.new("RGB", (300, 600), "white").save(out, "PNG")
        self.product_image = out.getvalue()

    def test_block_to_supported_fix_rerenders_and_full_review_passes(self):
        bad = valid_draft().model_copy(deep=True)
        bad.supporting_copy.text = "Guaranteed to cure acne"
        creative = GeneratedCreative(draft=bad, evidence=build_generation_allowlist(assessment()),
                                    product_image_reference="fixture.png", model="test")
        initial = score_creative(self.client, ad_copy=creative_copy(bad),
                                 context=self.context)
        self.assertEqual(initial.overall, OverallStatus.BLOCK)
        self.responses.replacement = "2% active helps reduce excess oil"
        replacement, evidence_id = suggest_replacement(
            self.client, initial.findings[0], creative_copy(bad), self.context)
        fixed = generated_draft_with_fix(bad, initial.findings[0].flagged_element,
                                         replacement, evidence_id)
        updated, preview, report = revise_generated(self.client, creative, fixed, self.product_image)
        self.assertEqual(updated.draft.supporting_copy.text, replacement)
        self.assertEqual(updated.draft.headline.text, bad.headline.text)
        self.assertTrue(preview)
        self.assertEqual(report.overall, OverallStatus.PASS)
        self.assertTrue(report.export_allowed)
        self.assertGreater(len(self.responses.review_calls[-1][1]), 1)  # final pixels and pack

    def test_missing_substantiation_cannot_be_acknowledged_into_pass(self):
        original = "Test Serum. Clinically proven. Shop now."
        first = revise_external(self.client, original, None, self.context)
        self.assertEqual(first.overall, OverallStatus.REVIEW)
        self.assertFalse(first.export_allowed)
        self.responses.replacement = "Acknowledged"
        with self.assertRaises(FixUnavailable):
            suggest_replacement(self.client, first.findings[0], original, self.context)
        again = revise_external(self.client, original, None, self.context)
        self.assertEqual(again.overall, OverallStatus.REVIEW)

    def test_bad_manual_fix_remains_blocked_and_full_review_runs(self):
        first = revise_external(self.client, "Test Serum. Clinically proven. Shop now.",
                                None, self.context)
        self.assertEqual(first.overall, OverallStatus.REVIEW)
        partial = revise_external(self.client, "Test Serum. Scientifically proven. Shop now.",
                                  None, self.context)
        self.assertEqual(partial.overall, OverallStatus.REVIEW)
        self.assertFalse(partial.export_allowed)
        self.assertEqual(len(self.responses.review_calls), 2)

    def test_generated_manual_edit_cannot_bypass_evidence_guard(self):
        creative = GeneratedCreative(draft=valid_draft(), evidence=build_generation_allowlist(assessment()),
                                    product_image_reference="fixture.png", model="test")
        bad = valid_draft().model_copy(deep=True)
        bad.supporting_copy.text = "Miracle results guaranteed"
        with self.assertRaises(FixUnavailable):
            revise_generated(self.client, creative, bad, self.product_image)
        self.assertEqual(self.responses.review_calls, [])

    def test_pasted_external_copy_suggested_fix_then_re_review(self):
        original = "Test Serum. Guaranteed to cure acne. Shop now."
        first = revise_external(self.client, original, None, self.context)
        self.responses.replacement = ""
        replacement, _ = suggest_replacement(self.client, first.findings[0], original, self.context)
        fixed = replace_flagged(original, first.findings[0].flagged_element, replacement)
        second = revise_external(self.client, fixed, None, self.context)
        self.assertEqual(second.overall, OverallStatus.PASS)
        self.assertTrue(second.export_allowed)
        self.assertEqual(len(self.responses.review_calls), 2)

    def test_image_only_cannot_be_pretended_editable(self):
        with self.assertRaisesRegex(FixUnavailable, "inside the image"):
            suggest_replacement(self.client, finding("Fades dark spots"), "", self.context)
        self.assertEqual(self.responses.review_calls, [])
        # A newly uploaded image, not a text mutation, is sent through full review.
        report = revise_external(self.client, "", self.product_image, self.context)
        self.assertEqual(report.overall, OverallStatus.REVIEW)
        self.assertFalse(report.export_allowed)
        self.assertGreater(len(self.responses.review_calls[0][1]), 1)

    def test_suggested_fix_rejects_invented_replacement(self):
        self.responses.replacement = "New miraculous result"
        with self.assertRaises(FixUnavailable):
            suggest_replacement(self.client, finding("bad"), "A bad claim", self.context)

    def test_fix_changes_one_span_not_whole_copy(self):
        self.assertEqual(replace_flagged("A bad claim. Bad appears again.", "bad", "sourced"),
                         "A sourced claim. Bad appears again.")

    def test_external_reference_only_evidence_cannot_become_automatic_copy(self):
        external_context = ReviewContext(evidence=[ReviewEvidence(
            evidence_id="SRC-001", exact_text="₹499 today only")])
        self.responses.replacement = "₹499 today only"
        with self.assertRaises(FixUnavailable):
            suggest_replacement(self.client, finding("bad"), "A bad claim", external_context)


if __name__ == "__main__":
    unittest.main()
