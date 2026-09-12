import unittest
from datetime import UTC, datetime
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

from minimalist_mvp.eligibility import (
    EligibilityAssessment,
    EligibilityDecision,
    EligibilityStatus,
)
from minimalist_mvp.generation import (
    AdDraft,
    CtaElement,
    GenerationUnavailable,
    HeadlineElement,
    IngredientCalloutElement,
    SupportingCopyElement,
    build_generation_allowlist,
    generate_ad_content,
    load_product_image,
    render_creative_preview,
)
from minimalist_mvp.product import SourceReference


SOURCE = SourceReference(
    source_url="https://beminimalist.co/products/test-serum",
    captured_at=datetime(2026, 9, 11, 10, 0, tzinfo=UTC),
    section="Product page",
    wording="Test Serum 2% active helps reduce excess oil",
    method="visible page text",
)


def decision(
    value: str,
    category: str,
    status: EligibilityStatus = EligibilityStatus.ELIGIBLE,
    conditions: list[str] | None = None,
) -> EligibilityDecision:
    return EligibilityDecision(
        category=category,
        label="Test",
        value=value,
        status=status,
        reason="Test reason",
        sources=[SOURCE],
        required_conditions=conditions or [],
    )


def assessment() -> EligibilityAssessment:
    return EligibilityAssessment(
        decisions=[
            decision("Test Serum", "Product identity"),
            decision("2% active helps reduce excess oil", "Product fact"),
            decision("Guaranteed to cure acne", "Claim", EligibilityStatus.INELIGIBLE),
        ]
    )


def valid_draft() -> AdDraft:
    return AdDraft(
        headline=HeadlineElement(text="Test Serum", evidence_ids=["EV-001"]),
        supporting_copy=SupportingCopyElement(
            text="2% active helps reduce excess oil",
            evidence_ids=["EV-002"],
        ),
        ingredient_callout=IngredientCalloutElement(text="2% active", evidence_ids=["EV-002"]),
        cta=CtaElement(text="Shop now", evidence_ids=[]),
    )


class FakeResponses:
    def __init__(self, draft: AdDraft | list[AdDraft | None]) -> None:
        self.drafts = draft if isinstance(draft, list) else [draft]
        self.kwargs = None
        self.calls = 0

    def parse(self, **kwargs):
        self.kwargs = kwargs
        draft = self.drafts[min(self.calls, len(self.drafts) - 1)]
        self.calls += 1
        return SimpleNamespace(status="completed", output_parsed=draft)


class FakeClient:
    def __init__(self, draft: AdDraft | list[AdDraft | None]) -> None:
        self.responses = FakeResponses(draft)


class GenerationTests(unittest.TestCase):
    def test_allowlist_excludes_ineligible_information(self) -> None:
        allowlist = build_generation_allowlist(assessment())
        self.assertEqual([entry.evidence_id for entry in allowlist], ["EV-001", "EV-002"])
        self.assertNotIn("Guaranteed to cure acne", [entry.exact_text for entry in allowlist])

    def test_structured_generation_uses_only_allowlist(self) -> None:
        client = FakeClient(valid_draft())
        result = generate_ad_content(
            client,
            assessment(),
            "https://beminimalist.co/product.png",
        )
        self.assertEqual(result.draft.headline.text, "Test Serum")
        prompt = client.responses.kwargs["input"][1]["content"]
        self.assertNotIn("Guaranteed to cure acne", prompt)
        self.assertEqual(result.product_image_evidence_id, "IMG-001")

    def test_generation_stops_when_only_identity_is_eligible(self) -> None:
        weak = EligibilityAssessment(decisions=[decision("Test Serum", "Product identity")])
        with self.assertRaises(GenerationUnavailable):
            generate_ad_content(FakeClient(valid_draft()), weak, "product.png")

    def test_unknown_evidence_id_is_rejected(self) -> None:
        draft = valid_draft()
        draft.headline.evidence_ids = ["EV-999"]
        with self.assertRaises(GenerationUnavailable):
            generate_ad_content(FakeClient(draft), assessment(), "product.png")

    def test_invented_number_is_rejected(self) -> None:
        draft = valid_draft()
        draft.supporting_copy.text = "99% active helps reduce excess oil"
        with self.assertRaises(GenerationUnavailable):
            generate_ad_content(FakeClient(draft), assessment(), "product.png")

    def test_malformed_partial_tokens_trigger_one_fresh_draft(self) -> None:
        malformed = valid_draft()
        malformed.supporting_copy.text = "2% active helps reduce excess oil aplica, o."
        client = FakeClient([malformed, valid_draft()])

        result = generate_ad_content(client, assessment(), "product.png")

        self.assertEqual(client.responses.calls, 2)
        self.assertEqual(result.draft.supporting_copy.text, "2% active helps reduce excess oil")
        self.assertEqual(malformed.supporting_copy.text, "2% active helps reduce excess oil aplica, o.")
        self.assertIn("Regenerate the entire draft", client.responses.kwargs["input"][0]["content"])

    def test_stray_word_still_fails_after_single_retry(self) -> None:
        malformed = valid_draft()
        malformed.supporting_copy.text = "2% active helps reduce excess oil fun"
        client = FakeClient([malformed, malformed])

        with self.assertRaisesRegex(
            GenerationUnavailable,
            "Couldn’t generate a reliable creative. Please try again.",
        ):
            generate_ad_content(client, assessment(), "product.png")

        self.assertEqual(client.responses.calls, 2)
        self.assertEqual(malformed.supporting_copy.text, "2% active helps reduce excess oil fun")

    def test_copy_that_will_not_fit_is_rejected(self) -> None:
        schema = AdDraft.model_json_schema()
        definitions = schema["$defs"]
        self.assertEqual(definitions["HeadlineElement"]["properties"]["text"]["maxLength"], 80)
        self.assertEqual(
            definitions["SupportingCopyElement"]["properties"]["text"]["maxLength"],
            200,
        )

    def test_ingredient_callout_must_use_exact_sourced_wording(self) -> None:
        draft = valid_draft()
        draft.ingredient_callout.text = "Advanced 2% active"
        with self.assertRaises(GenerationUnavailable):
            generate_ad_content(FakeClient(draft), assessment(), "product.png")

    def test_required_qualifier_cannot_be_dropped(self) -> None:
        conditional = EligibilityAssessment(
            decisions=[
                decision("Test Serum", "Product identity"),
                decision(
                    "90% reported improvement",
                    "Claim",
                    EligibilityStatus.ELIGIBLE_REVIEW_REQUIRED,
                    ["Based on a 20-subject consumer study"],
                ),
            ]
        )
        draft = AdDraft(
            headline=HeadlineElement(text="Test Serum", evidence_ids=["EV-001"]),
            supporting_copy=SupportingCopyElement(
                text="90% reported improvement",
                evidence_ids=["EV-002"],
            ),
            ingredient_callout=None,
            cta=CtaElement(text="Learn more", evidence_ids=[]),
        )
        with self.assertRaises(GenerationUnavailable):
            generate_ad_content(FakeClient(draft), conditional, "product.png")

    def test_actual_image_is_rendered_to_square_preview(self) -> None:
        image_buffer = BytesIO()
        Image.new("RGB", (300, 600), "#ffffff").save(image_buffer, format="PNG")
        image_bytes = load_product_image("manual.png", manual_image=image_buffer.getvalue())
        creative = generate_ad_content(
            FakeClient(valid_draft()), assessment(), "manual.png"
        )
        preview = render_creative_preview(creative, image_bytes)
        with Image.open(BytesIO(preview)) as rendered:
            self.assertEqual(rendered.size, (1080, 1080))
            self.assertEqual(rendered.format, "PNG")


if __name__ == "__main__":
    unittest.main()
