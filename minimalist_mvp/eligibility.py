from __future__ import annotations

import re
from enum import StrEnum

from pydantic import BaseModel, Field

from minimalist_mvp.product import (
    ExtractedItem,
    ProductExtraction,
    ProductVariant,
    SourceReference,
)


class EligibilityStatus(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    ELIGIBLE_REVIEW_REQUIRED = "ELIGIBLE_REVIEW_REQUIRED"
    INELIGIBLE = "INELIGIBLE"
    NOT_ASSESSABLE = "NOT_ASSESSABLE"


class EligibilityDecision(BaseModel):
    category: str
    label: str
    value: str
    status: EligibilityStatus
    reason: str
    sources: list[SourceReference] = Field(default_factory=list)
    required_conditions: list[str] = Field(default_factory=list)


class EligibilityAssessment(BaseModel):
    decisions: list[EligibilityDecision]

    def for_status(self, status: EligibilityStatus) -> list[EligibilityDecision]:
        return [decision for decision in self.decisions if decision.status == status]


_RISKY_PATTERNS = (
    r"\b(?:cure|cures|cured|treat|treats|prevents?|heals?)\b",
    r"\b(?:guaranteed|guarantees|permanent(?:ly)?|miracle)\b",
    r"\bno side effects?\b",
    r"\b(?:instant|immediate) results?\b",
    r"\b100% (?:effective|results?|success)\b",
)

_EVIDENCE_TRIGGER_PATTERNS = (
    r"\bclinically\b",
    r"\bclinical(?:ly)? (?:study|studied|tested|proven|results?)\b",
    r"\b(?:study|studies|subjects?|participants?|respondents?)\b",
    r"\b\d+(?:\.\d+)?% (?:of|reported|saw|showed|experienced|reduction|improvement)\b",
    r"\b(?:proven|tested) to\b",
    r"\b\d+(?:\.\d+)?\s*(?:x|times)\b",
)

_QUALIFIER_TERMS = (
    "may",
    "helps",
    "help",
    "appearance of",
    "up to",
    "based on",
    "reported",
    "consumer study",
    "clinical study",
)

_NEGATION_TERMS = ("not", "does not", "isn't", "without", "free from")

_STANDALONE_QUALIFIER_PATTERNS = (
    r"^based on\b",
    r"^results? (?:may|can) vary\b",
    r"^individual results?\b",
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "is", "it", "of", "on", "or", "the", "this", "to", "with",
}


def assess_generation_eligibility(
    extraction: ProductExtraction,
    selected_variant: ProductVariant | None = None,
) -> EligibilityAssessment:
    """Classify exact extracted wording before it can be offered to a generator."""
    decisions = [
        EligibilityDecision(
            category="Product identity",
            label="Product name",
            value=extraction.product_name,
            status=EligibilityStatus.ELIGIBLE,
            reason="Exact product identity is supported by the captured source.",
            sources=[extraction.product_name_source],
        )
    ]

    if selected_variant is not None:
        decisions.append(
            EligibilityDecision(
                category="Product identity",
                label="Selected variant / size",
                value=selected_variant.title,
                status=EligibilityStatus.ELIGIBLE,
                reason="The user selected this exact variant from the captured product data.",
                sources=[selected_variant.source],
            )
        )
    elif len(extraction.variants) > 1:
        decisions.append(
            EligibilityDecision(
                category="Product identity",
                label="Variant / size",
                value="Multiple variants found",
                status=EligibilityStatus.NOT_ASSESSABLE,
                reason="The exact variant has not been selected, so variant-specific wording cannot be used.",
                sources=[variant.source for variant in extraction.variants],
            )
        )
    elif extraction.variants:
        variant = extraction.variants[0]
        decisions.append(
            EligibilityDecision(
                category="Product identity",
                label="Variant / size",
                value=variant.title,
                status=EligibilityStatus.ELIGIBLE,
                reason="Only one variant was found in the captured product data.",
                sources=[variant.source],
            )
        )

    decisions.extend(_classify_fact(item) for item in extraction.facts)
    decisions.extend(
        _classify_claim(item, extraction.evidence)
        for item in extraction.claims
    )
    claim_wording = {_normalise(item.value) for item in extraction.claims}
    decisions.extend(
        _classify_evidence(item, extraction.evidence)
        for item in extraction.evidence
        if _normalise(item.value) not in claim_wording
    )
    return EligibilityAssessment(decisions=decisions)


def _classify_fact(item: ExtractedItem) -> EligibilityDecision:
    if not _has_traceable_source(item):
        return _not_assessable(item, "Product fact", "No traceable source wording is available for this fact.")
    if _is_materially_changed(item):
        return _ineligible(
            item,
            "Product fact",
            "The extracted wording contradicts its source or drops a material qualifier.",
        )
    if _matches_any(item.value, _RISKY_PATTERNS):
        return _ineligible(
            item,
            "Product fact",
            "This item contains high-risk claim wording even though it was extracted from a factual page section.",
        )
    return EligibilityDecision(
        category="Product fact",
        label=item.label,
        value=item.value,
        status=EligibilityStatus.ELIGIBLE,
        reason="This exact fact is present in the captured source wording.",
        sources=[item.source],
    )


def _classify_claim(
    item: ExtractedItem,
    evidence_items: list[ExtractedItem],
) -> EligibilityDecision:
    if not _has_traceable_source(item):
        return _not_assessable(item, "Claim", "No traceable source wording is available for this claim.")
    if _is_materially_changed(item):
        return _ineligible(
            item,
            "Claim",
            "The claim is materially stronger than, or removes a qualifier from, its captured source.",
        )
    if _matches_any(item.value, _RISKY_PATTERNS):
        return _ineligible(
            item,
            "Claim",
            "This uses high-risk treatment, guarantee, absolute, or instant-result wording and is not available to generation.",
        )

    linked_evidence = _find_linked_evidence(item, evidence_items)
    needs_evidence = _matches_any(item.value, _EVIDENCE_TRIGGER_PATTERNS)
    manual_source = item.source.method == "user-provided"

    if needs_evidence and not linked_evidence:
        return _not_assessable(
            item,
            "Claim",
            "This efficacy or testing claim needs supporting evidence or qualifiers, but none could be linked.",
        )
    if manual_source and not linked_evidence:
        return _not_assessable(
            item,
            "Claim",
            "The claim was supplied manually and no supporting evidence was provided to assess it.",
        )
    if linked_evidence:
        sources = _unique_sources([item.source, *(evidence.source for evidence in linked_evidence)])
        conditions = _unique_text(evidence.value for evidence in linked_evidence)
        return EligibilityDecision(
            category="Claim",
            label=item.label,
            value=item.value,
            status=EligibilityStatus.ELIGIBLE_REVIEW_REQUIRED,
            reason="The claim is linked to evidence and may be used only with the shown evidence and qualifiers preserved.",
            sources=sources,
            required_conditions=conditions,
        )

    return EligibilityDecision(
        category="Claim",
        label=item.label,
        value=item.value,
        status=EligibilityStatus.ELIGIBLE,
        reason="This exact claim appears in the captured product-page wording and does not trigger an evidence condition here.",
        sources=[item.source],
    )


def _classify_evidence(
    item: ExtractedItem,
    evidence_items: list[ExtractedItem],
) -> EligibilityDecision:
    if not _has_traceable_source(item):
        return _not_assessable(item, "Evidence statement", "No traceable source wording is available for this statement.")
    if _matches_any(item.value, _STANDALONE_QUALIFIER_PATTERNS):
        return EligibilityDecision(
            category="Evidence / qualifier",
            label=item.label,
            value=item.value,
            status=EligibilityStatus.NOT_ASSESSABLE,
            reason="This is supporting context, not a standalone generation claim.",
            sources=[item.source],
        )
    if _matches_any(item.value, _RISKY_PATTERNS):
        return _ineligible(
            item,
            "Evidence statement",
            "This uses high-risk treatment, guarantee, absolute, or instant-result wording and is not available to generation.",
        )

    same_section_qualifiers = [
        candidate
        for candidate in evidence_items
        if candidate is not item
        and candidate.source.section == item.source.section
        and _matches_any(candidate.value, _STANDALONE_QUALIFIER_PATTERNS)
    ]
    conditions = _unique_text([item.value, *(candidate.value for candidate in same_section_qualifiers)])
    sources = _unique_sources([item.source, *(candidate.source for candidate in same_section_qualifiers)])
    return EligibilityDecision(
        category="Evidence statement",
        label=item.label,
        value=item.value,
        status=EligibilityStatus.ELIGIBLE_REVIEW_REQUIRED,
        reason="Study, testing, or efficacy wording must retain its exact result, basis, timeframe, and other shown qualifiers.",
        sources=sources,
        required_conditions=conditions,
    )


def _find_linked_evidence(
    claim: ExtractedItem,
    evidence_items: list[ExtractedItem],
) -> list[ExtractedItem]:
    claim_tokens = _meaningful_tokens(claim.value)
    claim_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", claim.value.casefold()))
    matches: list[ExtractedItem] = []
    for evidence in evidence_items:
        evidence_tokens = _meaningful_tokens(evidence.value)
        overlap = claim_tokens & evidence_tokens
        evidence_numbers = set(re.findall(r"\d+(?:\.\d+)?%?", evidence.value.casefold()))
        same_wording = _normalise(claim.value) == _normalise(evidence.value)
        same_section = claim.source.section == evidence.source.section
        number_match = bool(claim_numbers & evidence_numbers)
        enough_overlap = len(overlap) >= 3 or (
            len(overlap) >= 2 and len(overlap) / max(1, len(claim_tokens)) >= 0.4
        )
        if same_wording or enough_overlap or (same_section and number_match and len(overlap) >= 1):
            matches.append(evidence)

    if matches:
        sections = {match.source.section for match in matches}
        matches.extend(
            evidence
            for evidence in evidence_items
            if evidence not in matches
            and evidence.source.section in sections
            and _matches_any(evidence.value, _STANDALONE_QUALIFIER_PATTERNS)
        )
    return matches


def _has_traceable_source(item: ExtractedItem) -> bool:
    return bool(item.source.source_url.strip() and item.source.wording.strip())


def _is_materially_changed(item: ExtractedItem) -> bool:
    value = _normalise(item.value)
    source = _normalise(item.source.wording)
    if not value or not source:
        return False
    missing_qualifier = any(term in source and term not in value for term in _QUALIFIER_TERMS)
    source_is_negative = any(term in source for term in _NEGATION_TERMS)
    value_is_negative = any(term in value for term in _NEGATION_TERMS)
    changed_polarity = source_is_negative != value_is_negative
    return missing_qualifier or changed_polarity


def _matches_any(value: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, value, flags=re.IGNORECASE) for pattern in patterns)


def _meaningful_tokens(value: str) -> set[str]:
    return {token for token in _TOKEN_RE.findall(value.casefold()) if token not in _STOPWORDS}


def _normalise(value: str) -> str:
    return " ".join(_TOKEN_RE.findall(value.casefold()))


def _unique_text(values) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _unique_sources(sources) -> list[SourceReference]:
    unique: list[SourceReference] = []
    seen: set[tuple[str, str, str]] = set()
    for source in sources:
        key = (source.source_url, source.section, source.wording)
        if key not in seen:
            seen.add(key)
            unique.append(source)
    return unique


def _not_assessable(item: ExtractedItem, category: str, reason: str) -> EligibilityDecision:
    return EligibilityDecision(
        category=category,
        label=item.label,
        value=item.value,
        status=EligibilityStatus.NOT_ASSESSABLE,
        reason=reason,
        sources=[item.source] if _has_traceable_source(item) else [],
    )


def _ineligible(item: ExtractedItem, category: str, reason: str) -> EligibilityDecision:
    return EligibilityDecision(
        category=category,
        label=item.label,
        value=item.value,
        status=EligibilityStatus.INELIGIBLE,
        reason=reason,
        sources=[item.source] if _has_traceable_source(item) else [],
    )
