import unittest
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image
from streamlit.testing.v1 import AppTest

from minimalist_mvp.product import manual_product_extraction
from tests.test_generation import FakeClient, valid_draft
from tests.test_fix import ReviewResponses
from tests.test_product import PRODUCT_HTML


class PassingReviewClient(FakeClient):
    def __init__(self):
        super().__init__(valid_draft())
        self.responses.create = lambda **kwargs: SimpleNamespace(output_text=json.dumps({
            "observed_text": "Test Serum 2% active helps reduce excess oil Shop now",
            "image_readability": "READABLE",
            "issues": [],
        }))


class ServerSideSecretTests(unittest.TestCase):
    def setUp(self) -> None:
        image = BytesIO()
        Image.new("RGB", (300, 600), "#ffffff").save(image, "PNG")
        product = manual_product_extraction(
            source_url="https://beminimalist.co/products/test",
            product_name="Test Serum",
            variant="",
            facts_text="2% active helps reduce excess oil",
            claims_text="",
            evidence_text="",
            commercial_text="",
            social_proof_text="",
            image_name="manual.png",
        )
        self.app = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "streamlit_app.py")
        )
        self.app.secrets["OPENAI_API_KEY"] = ""
        self.app.session_state.product_extraction = product
        self.app.session_state.manual_product_image = image.getvalue()
        self.app.run(timeout=20)
        self.assertFalse(self.app.exception)
        next(
            widget
            for widget in self.app.checkbox
            if widget.label.startswith("I confirm this is the right product")
        ).check().run(timeout=20)
        self.assertFalse(self.app.exception)

    def test_two_marketer_paths_and_collapsed_product_details(self) -> None:
        self.assertEqual([tab.label for tab in self.app.tabs],
                         ["Create & Review", "Review Existing Ad"])
        headings = [item.value for item in self.app.subheader]
        self.assertIn("Confirm this product", headings)
        self.assertIn("Evidence available for generation", headings)
        self.assertIn("Create your ad", headings)
        self.assertIn("Add Creative", headings)
        expanders = [item.label for item in self.app.expander]
        self.assertIn("View full extracted details", expanders)
        self.assertTrue(any("Ready to use" in item.value for item in self.app.markdown))
        self.assertTrue(any("Use with conditions" in item.value for item in self.app.markdown))
        self.assertTrue(any("Captured" in item.value for item in self.app.caption))

    def test_missing_key_has_no_frontend_input_or_generation(self) -> None:
        self.assertTrue(
            any(
                error.value == "OpenAI API key is not configured on the server."
                for error in self.app.error
            )
        )
        self.assertFalse(
            any(widget.label == "OpenAI API key" for widget in self.app.text_input)
        )
        button = next(
            widget for widget in self.app.button if widget.label == "Generate one creative"
        )
        self.assertTrue(button.disabled)

    def test_configured_server_secret_enables_generation_without_exposing_key(self) -> None:
        placeholder_key = "unit-test-server-key"
        self.app.secrets["OPENAI_API_KEY"] = placeholder_key
        with patch("openai.OpenAI", return_value=FakeClient(valid_draft())) as client:
            self.app.run(timeout=20)
            self.assertFalse(self.app.exception)
            button = next(
                widget for widget in self.app.button if widget.label == "Generate one creative"
            )
            self.assertFalse(button.disabled)
            button.click().run(timeout=20)

        self.assertFalse(self.app.exception)
        client.assert_called_once_with(api_key=placeholder_key, timeout=60, max_retries=0)
        self.assertEqual(
            self.app.session_state.generated_creative["draft"]["headline"]["text"],
            "Test Serum",
        )
        self.assertTrue(self.app.session_state.generated_preview)
        self.assertEqual(self.app.session_state.generated_review["overall"], "REVIEW")
        self.assertFalse(self.app.session_state.generated_review["export_allowed"])
        self.assertNotIn(placeholder_key, str(self.app.session_state.generated_creative))
        self.assertNotIn(placeholder_key.encode(), self.app.session_state.generated_preview)
        self.assertNotIn(placeholder_key, str(self.app.session_state.generated_review))
        self.assertFalse(
            any(widget.label == "OpenAI API key" for widget in self.app.text_input)
        )

    def test_verified_product_through_generation_review_and_export_has_no_exception(self) -> None:
        self.app.secrets["OPENAI_API_KEY"] = "unit-test-server-key"
        with patch("openai.OpenAI", return_value=PassingReviewClient()):
            self.app.run(timeout=20)
            self.assertFalse(self.app.exception)
            next(widget for widget in self.app.button
                 if widget.label == "Generate one creative").click().run(timeout=20)
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state.generated_review["overall"], "PASS")
        self.assertTrue(self.app.session_state.generated_review["export_allowed"])
        self.assertEqual(
            self.app.session_state.verified_review_context["product_image_reference"],
            "manual.png",
        )
        export = next(widget for widget in self.app.download_button
                      if widget.label == "Export creative")
        export.click().run(timeout=20)
        self.assertFalse(self.app.exception)
        self.assertTrue(any("Policy & Claims — PASS" in item.value for item in self.app.subheader))
        self.assertTrue(any("Brand Tone — PASS" in item.value for item in self.app.subheader))
        self.assertTrue(any("Brand Language — PASS" in item.value for item in self.app.subheader))

    def test_external_copy_without_context_is_reviewed_and_export_locked(self) -> None:
        self.app.secrets["OPENAI_API_KEY"] = "unit-test-server-key"
        with patch("openai.OpenAI", return_value=PassingReviewClient()):
            self.app.run(timeout=20)
            next(widget for widget in self.app.text_area
                 if widget.label == "Ad copy (optional if an image is uploaded)").input(
                     "Test Serum — 2% active helps reduce excess oil"
                 ).run(timeout=20)
            next(widget for widget in self.app.button
                 if widget.label == "Continue to context").click().run(timeout=20)
            next(widget for widget in self.app.radio
                 if widget.label == "Choose product context").set_value(
                     "Review without product context").run(timeout=20)
            next(widget for widget in self.app.button
                 if widget.label == "Continue to review").click().run(timeout=20)
            next(widget for widget in self.app.button
                 if widget.label == "Run pre-flight review").click().run(timeout=20)
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state.external_review["overall"], "REVIEW")
        self.assertFalse(self.app.session_state.external_review["export_allowed"])
        self.assertFalse(any(widget.label == "Export creative"
                             for widget in self.app.download_button))

    def test_external_copy_fix_rechecks_but_copy_only_has_no_asset_export(self) -> None:
        self.app.secrets["OPENAI_API_KEY"] = "unit-test-server-key"
        responses = ReviewResponses(proposal="Test Serum. Shop now.")
        with patch("openai.OpenAI", return_value=SimpleNamespace(responses=responses)):
            self.app.run(timeout=20)
            next(widget for widget in self.app.text_area
                 if widget.label == "Ad copy (optional if an image is uploaded)").input(
                     "Test Serum. Guaranteed to cure acne. Shop now."
                 ).run(timeout=20)
            next(widget for widget in self.app.button
                 if widget.label == "Continue to context").click().run(timeout=20)
            next(widget for widget in self.app.radio
                 if widget.label == "Choose product context").set_value(
                     "Use the product confirmed in Create & Review").run(timeout=20)
            self.assertTrue(any("Using product context: Test Serum" in item.value
                                for item in self.app.info))
            next(widget for widget in self.app.button
                 if widget.label == "Continue to review").click().run(timeout=20)
            next(widget for widget in self.app.button
                 if widget.label == "Run pre-flight review").click().run(timeout=20)
            self.assertFalse(self.app.exception)
            self.assertEqual(self.app.session_state.external_review["overall"], "BLOCK")
            self.assertFalse(any(widget.label == "Export creative"
                                 for widget in self.app.download_button))
            next(widget for widget in self.app.button
                 if widget.label == "Create supported revision").click().run(timeout=20)
            self.assertEqual(next(widget.value for widget in self.app.text_area
                                  if widget.label == "Current ad copy"), "Test Serum. Shop now.")
            next(widget for widget in self.app.button
                 if widget.label == "Apply & re-review").click().run(timeout=20)
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state.external_review["overall"], "PASS")
        self.assertTrue(self.app.session_state.external_review["export_allowed"])
        self.assertEqual(self.app.session_state.external_current_copy, "Test Serum. Shop now.")
        self.assertGreaterEqual(len(responses.review_calls), 2)
        self.assertFalse(any(widget.label == "Export creative"
                             for widget in self.app.download_button))
        self.assertTrue(any(widget.label == "Add final creative"
                            for widget in self.app.button))
        self.assertTrue(any("Copy-only review" in item.value for item in self.app.info))
        self.assertTrue(any("Visual creative: not supplied" in item.value
                            for item in self.app.caption))
        next(widget for widget in self.app.button
             if widget.label == "Add final creative").click().run(timeout=20)
        self.assertEqual(self.app.session_state.external_stage, "creative")
        self.assertEqual(next(widget.value for widget in self.app.text_area
                              if widget.label == "Ad copy (optional if an image is uploaded)"),
                         "Test Serum. Shop now.")

    def test_external_image_pass_has_asset_export_and_coverage(self) -> None:
        image = BytesIO()
        Image.new("RGB", (300, 300), "#ffffff").save(image, "PNG")
        self.app.session_state.external_stage = "result"
        self.app.session_state.external_current_copy = "Test Serum. Shop now."
        self.app.session_state.external_image_bytes = image.getvalue()
        self.app.session_state.external_image_name = "test-ad.png"
        self.app.session_state.external_review_context = self.app.session_state.verified_review_context
        self.app.session_state.external_review = {
            "overall": "PASS", "export_allowed": True, "findings": [],
            "observed_text": "Test Serum. Shop now.", "image_readability": "READABLE",
        }
        self.app.run(timeout=20)
        self.assertFalse(self.app.exception)
        self.assertTrue(any(widget.label == "Export creative"
                            for widget in self.app.download_button))
        self.assertTrue(any("Visual creative: reviewed" in item.value
                            for item in self.app.caption))
        self.assertTrue(any("Creative being reviewed" in item.value
                            for item in self.app.markdown))

    def test_unavailable_copy_fix_has_direct_evidence_action(self) -> None:
        self.app.session_state.external_stage = "result"
        self.app.session_state.external_current_copy = "Test Serum. Shop now."
        self.app.session_state.external_review_context = {}
        self.app.session_state.external_review = {
            "overall": "REVIEW", "export_allowed": False, "observed_text": "Test Serum. Shop now.",
            "findings": [{
                "rule_id": "CLAIM-001", "area": "Policy & Claims", "status": "REVIEW",
                "flagged_element": "Unverified image qualifier", "reason": "Evidence is unavailable.",
                "suggested_fix": "Supply evidence or remove the claim.", "material": True,
            }],
        }
        self.app.run(timeout=20)
        self.assertFalse(self.app.exception)
        self.assertTrue(any("We can’t safely create a supported revision" in item.value
                            for item in self.app.warning))
        self.assertTrue(any(widget.label == "Add product evidence" for widget in self.app.button))
        next(widget for widget in self.app.button
             if widget.label == "Add product evidence").click().run(timeout=20)
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state.external_stage, "context")
        self.assertTrue(any(widget.label == "Additional exact product/evidence wording (optional)"
                            for widget in self.app.text_area))

    def test_multiple_findings_have_one_proposed_revision_action(self) -> None:
        self.app.secrets["OPENAI_API_KEY"] = "unit-test-server-key"
        responses = ReviewResponses(proposal="Powered by 2% Salicylic Acid.")
        with patch("openai.OpenAI", return_value=SimpleNamespace(responses=responses)):
            self.app.run(timeout=20)
            next(widget for widget in self.app.text_area
                 if widget.label == "Ad copy (optional if an image is uploaded)").input(
                     "Guaranteed blackhead-free skin in 7 days.\nPowered by 2% Salicylic Acid."
                 ).run(timeout=20)
            next(widget for widget in self.app.button
                 if widget.label == "Continue to context").click().run(timeout=20)
            next(widget for widget in self.app.button
                 if widget.label == "Continue to review").click().run(timeout=20)
            next(widget for widget in self.app.button
                 if widget.label == "Run pre-flight review").click().run(timeout=20)
            self.assertEqual(self.app.session_state.external_review["overall"], "BLOCK")
            self.assertEqual(sum(widget.label == "Create supported revision"
                                 for widget in self.app.button), 1)
            self.assertFalse(any(widget.label.startswith("Apply suggested fix")
                                 for widget in self.app.button))
            self.assertTrue(any("Resolve this review" in item.value for item in self.app.markdown))
            next(widget for widget in self.app.button
                 if widget.label == "Create supported revision").click().run(timeout=20)
            self.assertEqual(next(widget.value for widget in self.app.text_area
                                  if widget.label == "Current ad copy"),
                             "Powered by 2% Salicylic Acid.")
        self.assertFalse(self.app.exception)

    def test_punctuation_only_manual_revision_stays_non_exportable(self) -> None:
        self.app.secrets["OPENAI_API_KEY"] = "unit-test-server-key"
        responses = ReviewResponses()
        with patch("openai.OpenAI", return_value=SimpleNamespace(responses=responses)):
            self.app.run(timeout=20)
            next(widget for widget in self.app.text_area
                 if widget.label == "Ad copy (optional if an image is uploaded)").input(
                     "Test Serum. Guaranteed to cure acne. Shop now."
                 ).run(timeout=20)
            next(widget for widget in self.app.button
                 if widget.label == "Continue to context").click().run(timeout=20)
            next(widget for widget in self.app.button
                 if widget.label == "Continue to review").click().run(timeout=20)
            next(widget for widget in self.app.button
                 if widget.label == "Run pre-flight review").click().run(timeout=20)
            next(widget for widget in self.app.button
                 if widget.label == "Edit copy manually").click().run(timeout=20)
            next(widget for widget in self.app.text_area
                 if widget.label == "Current ad copy").input(".").run(timeout=20)
            next(widget for widget in self.app.button
                 if widget.label == "Apply & re-review").click().run(timeout=20)
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state.external_current_copy, ".")
        self.assertEqual(self.app.session_state.external_review["overall"], "REVIEW")
        self.assertFalse(self.app.session_state.external_review["export_allowed"])
        self.assertFalse(any(widget.label == "Export creative"
                             for widget in self.app.download_button))
        self.assertIn("There is no creative left", str(self.app.session_state.external_review))

    def test_another_product_url_requires_exact_variant_and_image_confirmation(self) -> None:
        self.app.secrets["OPENAI_API_KEY"] = "unit-test-server-key"
        url = "https://beminimalist.co/products/another-serum"
        self.app.run(timeout=20)
        next(widget for widget in self.app.text_area
             if widget.label == "Ad copy (optional if an image is uploaded)").input(
                 "Another Serum. Shop now.").run(timeout=20)
        next(widget for widget in self.app.button
             if widget.label == "Continue to context").click().run(timeout=20)
        next(widget for widget in self.app.radio
             if widget.label == "Choose product context").set_value(
                 "Use another Minimalist product URL").run(timeout=20)
        next(widget for widget in self.app.text_input
             if widget.label == "Minimalist product URL for this ad").input(url).run(timeout=20)
        # Supply a page response at the existing product reader's requests boundary.
        class Page:
            headers = {"content-type": "text/html"}
            encoding = "utf-8"

            def raise_for_status(self):
                return None

            def iter_content(self, chunk_size):
                yield PRODUCT_HTML.encode("utf-8")

        page = Page()
        page.url = url
        with patch("minimalist_mvp.product.requests.get", return_value=page):
            next(widget for widget in self.app.button
                 if widget.label == "Read product URL").click().run(timeout=20)
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.session_state.external_product_extraction.product_name,
                         "Test Serum 2%")
        next(widget for widget in self.app.selectbox
             if widget.label == "Select exact variant / size").select_index(0).run(timeout=20)
        next(widget for widget in self.app.selectbox
             if widget.label == "Select the matching product image").select_index(0).run(timeout=20)
        next(widget for widget in self.app.checkbox
             if widget.label == "I confirm this product, variant and image match the ad").check().run(timeout=20)
        self.assertTrue(any("Using product context: Test Serum 2% — 30ml" in item.value
                            for item in self.app.info))
        next(widget for widget in self.app.button
             if widget.label == "Continue to review").click().run(timeout=20)
        self.assertEqual(self.app.session_state.external_review_context["product_name"], "Test Serum 2%")
        self.assertEqual(self.app.session_state.external_review_context["variant"], "30ml")


if __name__ == "__main__":
    unittest.main()
