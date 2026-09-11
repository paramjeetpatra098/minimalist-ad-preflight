import unittest
from datetime import UTC, datetime

from minimalist_mvp.eligibility import EligibilityStatus, assess_generation_eligibility
from minimalist_mvp.product import (
    ExtractedItem,
    ProductExtraction,
    SourceReference,
)


CAPTURED = datetime(2026, 9, 11, 10, 0, tzinfo=UTC)


def source(wording: str, method: str = "visible page text", section: str = "Benefits") -> SourceReference:
    return SourceReference(
        source_url="https://beminimalist.co/products/test",
        captured_at=CAPTURED,
        section=section,
        wording=wording,
        method=method,
    )


def item(value: str, *, wording: str | None = None, method: str = "visible page text", section: str = "Benefits") -> ExtractedItem:
    return ExtractedItem(
        label="Test item",
        value=value,
        source=source(wording or value, method=method, section=section),
    )


def extraction(*, facts=None, claims=None, evidence=None) -> ProductExtraction:
    return ProductExtraction(
        source_url="https://beminimalist.co/products/test",
        captured_at=CAPTURED,
        product_name="Test Serum",
        product_name_source=source("Test Serum", section="Product identity"),
        facts=facts or [],
        claims=claims or [],
        evidence=evidence or [],
    )


class EligibilityTests(unittest.TestCase):
    def test_exact_sourced_fact_is_eligible(self) -> None:
        result = assess_generation_eligibility(extraction(facts=[item("2% active")]))
        decision = next(decision for decision in result.decisions if decision.category == "Product fact")
        self.assertEqual(decision.status, EligibilityStatus.ELIGIBLE)
        self.assertEqual(decision.sources[0].wording, "2% active")

    def test_efficacy_claim_with_evidence_requires_review_and_preserves_qualifier(self) -> None:
        claim = item("90% of subjects reported improvement after 4 weeks.", section="Consumer Studies")
        qualifier = item("Based on a 20-subject consumer study.", section="Consumer Studies")
        result = assess_generation_eligibility(
            extraction(claims=[claim], evidence=[claim, qualifier])
        )
        decision = next(decision for decision in result.decisions if decision.category == "Claim")
        self.assertEqual(decision.status, EligibilityStatus.ELIGIBLE_REVIEW_REQUIRED)
        self.assertIn(qualifier.value, decision.required_conditions)
        self.assertGreaterEqual(len(decision.sources), 2)

    def test_efficacy_claim_without_evidence_is_not_assessable(self) -> None:
        result = assess_generation_eligibility(
            extraction(claims=[item("90% of subjects saw improvement")])
        )
        decision = next(decision for decision in result.decisions if decision.category == "Claim")
        self.assertEqual(decision.status, EligibilityStatus.NOT_ASSESSABLE)

    def test_risky_claim_is_ineligible(self) -> None:
        result = assess_generation_eligibility(
            extraction(claims=[item("Guaranteed to cure acne")])
        )
        decision = next(decision for decision in result.decisions if decision.category == "Claim")
        self.assertEqual(decision.status, EligibilityStatus.INELIGIBLE)

    def test_risky_wording_is_not_misclassified_as_safe_fact(self) -> None:
        result = assess_generation_eligibility(
            extraction(facts=[item("Guaranteed permanent results")])
        )
        decision = next(decision for decision in result.decisions if decision.category == "Product fact")
        self.assertEqual(decision.status, EligibilityStatus.INELIGIBLE)

    def test_removed_material_qualifier_is_ineligible(self) -> None:
        result = assess_generation_eligibility(
            extraction(claims=[item("Reduces dark spots", wording="May help reduce the appearance of dark spots")])
        )
        decision = next(decision for decision in result.decisions if decision.category == "Claim")
        self.assertEqual(decision.status, EligibilityStatus.INELIGIBLE)

    def test_statement_that_contradicts_source_is_ineligible(self) -> None:
        result = assess_generation_eligibility(
            extraction(facts=[item("Suitable for sensitive skin", wording="Not suitable for sensitive skin")])
        )
        decision = next(decision for decision in result.decisions if decision.category == "Product fact")
        self.assertEqual(decision.status, EligibilityStatus.INELIGIBLE)

    def test_manual_claim_without_support_is_not_assessable(self) -> None:
        result = assess_generation_eligibility(
            extraction(claims=[item("Reduces excess oil", method="user-provided")])
        )
        decision = next(decision for decision in result.decisions if decision.category == "Claim")
        self.assertEqual(decision.status, EligibilityStatus.NOT_ASSESSABLE)

    def test_qualifier_is_not_a_standalone_candidate(self) -> None:
        qualifier = item("Based on a 20-subject consumer study.", section="Consumer Studies")
        result = assess_generation_eligibility(extraction(evidence=[qualifier]))
        decision = next(decision for decision in result.decisions if decision.category == "Evidence / qualifier")
        self.assertEqual(decision.status, EligibilityStatus.NOT_ASSESSABLE)

    def test_evidence_duplicate_of_claim_is_not_shown_twice(self) -> None:
        efficacy = item("90% of subjects reported improvement.", section="Consumer Studies")
        result = assess_generation_eligibility(
            extraction(claims=[efficacy], evidence=[efficacy])
        )
        matching = [decision for decision in result.decisions if decision.value == efficacy.value]
        self.assertEqual(len(matching), 1)


if __name__ == "__main__":
    unittest.main()
