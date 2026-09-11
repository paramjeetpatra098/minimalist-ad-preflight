from enum import StrEnum

from pydantic import BaseModel, Field


class FindingOutcome(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"
    NOT_ASSESSABLE = "NOT_ASSESSABLE"


class OverallStatus(StrEnum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    REVIEW = "REVIEW"
    BLOCK = "BLOCK"


class Finding(BaseModel):
    rule_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    outcome: FindingOutcome
    message: str = Field(min_length=1)
    material: bool = True


class ReviewDecision(BaseModel):
    status: OverallStatus
    export_allowed: bool
    findings: list[Finding]


def decide_review(findings: list[Finding]) -> ReviewDecision:
    """Apply the frozen V1 review and export rules deterministically."""
    outcomes = {finding.outcome for finding in findings}
    has_material_gap = any(
        finding.outcome == FindingOutcome.NOT_ASSESSABLE and finding.material
        for finding in findings
    )

    if FindingOutcome.BLOCK in outcomes:
        status = OverallStatus.BLOCK
    elif FindingOutcome.REVIEW in outcomes or has_material_gap:
        status = OverallStatus.REVIEW
    elif FindingOutcome.WARN in outcomes or FindingOutcome.NOT_ASSESSABLE in outcomes:
        status = OverallStatus.PASS_WITH_WARNINGS
    else:
        status = OverallStatus.PASS

    return ReviewDecision(
        status=status,
        export_allowed=status in {OverallStatus.PASS, OverallStatus.PASS_WITH_WARNINGS},
        findings=findings,
    )
