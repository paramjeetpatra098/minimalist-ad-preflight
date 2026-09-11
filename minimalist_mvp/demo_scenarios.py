from dataclasses import dataclass

from minimalist_mvp.review import Finding, FindingOutcome


@dataclass(frozen=True)
class DemoScenario:
    label: str
    description: str
    findings: tuple[Finding, ...]


SCENARIOS = {
    "Clean creative": DemoScenario(
        label="Clean creative",
        description="All applicable checks pass and no warning is present.",
        findings=(
            Finding(
                rule_id="CLAIM-001",
                title="Product facts match",
                outcome=FindingOutcome.PASS,
                message="The displayed product facts match the available source.",
            ),
        ),
    ),
    "Tone warning": DemoScenario(
        label="Tone warning",
        description="The ad remains exportable, but the reviewer sees a brand-quality warning.",
        findings=(
            Finding(
                rule_id="TONE-001",
                title="Copy is overly technical",
                outcome=FindingOutcome.WARN,
                message="The wording is accurate but may not feel consumer-friendly.",
                material=False,
            ),
        ),
    ),
    "Evidence incomplete": DemoScenario(
        label="Evidence incomplete",
        description="A potentially valid claim needs evidence-backed resolution.",
        findings=(
            Finding(
                rule_id="CLAIM-006",
                title="Quantified claim needs substantiation",
                outcome=FindingOutcome.REVIEW,
                message="The result may be valid, but the study details and qualifiers are incomplete.",
            ),
        ),
    ),
    "Invented benefit": DemoScenario(
        label="Invented benefit",
        description="A definite rule violation blocks export until the claim is removed or rewritten.",
        findings=(
            Finding(
                rule_id="CLAIM-002",
                title="Invented product benefit",
                outcome=FindingOutcome.BLOCK,
                message="The advertised benefit is not present in the supplied product evidence.",
            ),
        ),
    ),
    "Material check unavailable": DemoScenario(
        label="Material check unavailable",
        description="A missing product or evidence source prevents an external creative from passing.",
        findings=(
            Finding(
                rule_id="SYSTEM-001",
                title="Claim cannot be assessed",
                outcome=FindingOutcome.NOT_ASSESSABLE,
                message="The creative contains a material claim, but product evidence is unavailable.",
            ),
        ),
    ),
}
