import unittest

from minimalist_mvp.review import (
    Finding,
    FindingOutcome,
    OverallStatus,
    decide_review,
)


def finding(outcome: FindingOutcome, *, material: bool = True) -> Finding:
    return Finding(
        rule_id="TEST-001",
        title="Test finding",
        outcome=outcome,
        message="Used to verify deterministic review behavior.",
        material=material,
    )


class ReviewDecisionTests(unittest.TestCase):
    def test_no_findings_passes(self) -> None:
        decision = decide_review([])
        self.assertEqual(decision.status, OverallStatus.PASS)
        self.assertTrue(decision.export_allowed)

    def test_warning_creates_pass_with_warnings(self) -> None:
        decision = decide_review([finding(FindingOutcome.WARN, material=False)])
        self.assertEqual(decision.status, OverallStatus.PASS_WITH_WARNINGS)
        self.assertTrue(decision.export_allowed)

    def test_review_locks_export(self) -> None:
        decision = decide_review([finding(FindingOutcome.REVIEW)])
        self.assertEqual(decision.status, OverallStatus.REVIEW)
        self.assertFalse(decision.export_allowed)

    def test_block_wins_over_other_findings(self) -> None:
        decision = decide_review(
            [finding(FindingOutcome.REVIEW), finding(FindingOutcome.BLOCK)]
        )
        self.assertEqual(decision.status, OverallStatus.BLOCK)
        self.assertFalse(decision.export_allowed)

    def test_material_not_assessable_becomes_review(self) -> None:
        decision = decide_review([finding(FindingOutcome.NOT_ASSESSABLE)])
        self.assertEqual(decision.status, OverallStatus.REVIEW)
        self.assertFalse(decision.export_allowed)

    def test_non_material_not_assessable_becomes_warning(self) -> None:
        decision = decide_review(
            [finding(FindingOutcome.NOT_ASSESSABLE, material=False)]
        )
        self.assertEqual(decision.status, OverallStatus.PASS_WITH_WARNINGS)
        self.assertTrue(decision.export_allowed)


if __name__ == "__main__":
    unittest.main()
