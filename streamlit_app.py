import hashlib
import logging
import os
import re

import streamlit as st
from openai import APIConnectionError, APITimeoutError, OpenAI, OpenAIError

from minimalist_mvp.demo_scenarios import SCENARIOS
from minimalist_mvp.eligibility import (
    EligibilityAssessment,
    EligibilityDecision,
    EligibilityStatus,
    assess_generation_eligibility,
)
from minimalist_mvp.generation import (
    AdDraft,
    GeneratedCreative,
    GenerationContext,
    GenerationUnavailable,
    generate_ad_content,
    load_product_image,
    render_creative_preview,
)
from minimalist_mvp.fix import (
    FixUnavailable, creative_copy, propose_external_revision,
    propose_generated_revision, revise_external, revise_generated,
    unresolved_findings,
)
from minimalist_mvp.product import (
    ExtractedItem,
    ProductExtraction,
    ProductImage,
    ProductReadError,
    ProductVariant,
    manual_product_extraction,
    read_product_url,
    variant_commercial_items,
)
from minimalist_mvp.review import FindingOutcome, OverallStatus, decide_review
from minimalist_mvp.scorer import (
    ReviewContext,
    ReviewEvidence,
    ScorerReport,
    dimension_status,
    extracted_context,
    generated_context,
    has_meaningful_creative,
    score_creative,
)


st.set_page_config(
    page_title="Minimalist Ad Pre-flight",
    page_icon="M",
    layout="wide",
)

st.markdown(
    """
    <style>
    .block-container { max-width: 1050px; padding-top: 2.25rem; }
    .status-card {
        border: 1px solid #d9dde3;
        border-left-width: 8px;
        border-radius: 10px;
        padding: 1rem 1.2rem;
        margin: 0.75rem 0 1.25rem;
        background: #ffffff;
        color: #1f2937;
    }
    .status-pass { border-left-color: #157f3b; background: #effaf2; color: #16472b; }
    .status-warn { border-left-color: #986000; background: #fff7e6; color: #613e00; }
    .status-review { border-left-color: #a45605; background: #fff2e5; color: #633407; }
    .status-block { border-left-color: #b42318; background: #fff0ee; color: #681e18; }
    .status-label { color: inherit; font-size: 1.45rem; font-weight: 750; margin-bottom: 0.2rem; }
    .status-card .eyebrow { color: inherit; opacity: 0.82; font-size: 0.82rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
    </style>
    """,
    unsafe_allow_html=True,
)


STATUS_PRESENTATION = {
    OverallStatus.PASS: ("PASS", "status-pass", "Ready to export after this pre-flight review."),
    OverallStatus.PASS_WITH_WARNINGS: (
        "PASS with warnings",
        "status-warn",
        "Ready to export. Check the non-blocking notes if helpful.",
    ),
    OverallStatus.REVIEW: (
        "REVIEW",
        "status-review",
        "Fix the issue or add the missing support, then review again.",
    ),
    OverallStatus.BLOCK: (
        "BLOCK",
        "status-block",
        "Fix the issue and review again before exporting.",
    ),
}

# Bump when rendered pixels change so a saved preview and its review cannot outlive the renderer.
CREATIVE_RENDER_VERSION = 2


def finding_label(outcome: FindingOutcome) -> str:
    return {
        FindingOutcome.PASS: "Pass",
        FindingOutcome.WARN: "Warning",
        FindingOutcome.REVIEW: "Review",
        FindingOutcome.BLOCK: "Block",
        FindingOutcome.NOT_ASSESSABLE: "Couldn't assess",
    }[outcome]


def render_source(item: ExtractedItem) -> None:
    st.caption(f"Source · {item.source.section} · {item.source.source_url}")
    st.caption(f"Wording: {item.source.wording}")
    st.caption(f"Captured {item.source.captured_at.isoformat()} · {item.source.method}")


def render_items(items: list[ExtractedItem], empty_message: str) -> None:
    if not items:
        st.caption(empty_message)
        return
    for item in items:
        with st.container(border=True):
            st.markdown(f"**{item.label}**")
            st.write(item.value)
            render_source(item)


def variant_label(variant: ProductVariant) -> str:
    availability = "" if variant.available is not False else " · unavailable"
    return f"{variant.title}{availability}"


def image_label(image: ProductImage) -> str:
    return image.label


ELIGIBILITY_PRESENTATION = {
    EligibilityStatus.ELIGIBLE: ("Ready to use", "Supported product information the AI can use."),
    EligibilityStatus.ELIGIBLE_REVIEW_REQUIRED: (
        "Use with conditions",
        "The AI may use this only with its supporting conditions or qualifiers preserved.",
    ),
    EligibilityStatus.INELIGIBLE: ("Excluded", "The AI cannot use this to create the ad."),
    EligibilityStatus.NOT_ASSESSABLE: (
        "Couldn't assess",
        "The AI cannot use this unless the missing context or evidence is resolved.",
    ),
}


def render_steps(steps: tuple[str, ...], current: str) -> None:
    st.caption("  →  ".join(f"**{step}**" if step == current else step for step in steps))


def render_summary_items(items: list[ExtractedItem], empty: str, limit: int = 3) -> None:
    if not items:
        st.caption(empty)
        return
    for item in items[:limit]:
        st.markdown(f"- **{item.label}:** {item.value}")
    if len(items) > limit:
        st.caption(f"+ {len(items) - limit} more in full details")


def render_eligibility_source(decision: EligibilityDecision) -> None:
    if not decision.sources:
        st.caption("No traceable source was available.")
    for source in decision.sources:
        st.caption(f"Source · {source.section} · {source.source_url}")
        st.caption(f"Wording: {source.wording}")
        st.caption(f"Captured {source.captured_at.isoformat()} · {source.method}")


def render_eligibility_decision(decision: EligibilityDecision) -> None:
    with st.container(border=True):
        st.caption(decision.category)
        st.markdown(f"**{decision.label}**")
        st.write(decision.value)
        st.markdown(f"**Why:** {decision.reason}")
        if decision.required_conditions:
            st.markdown("**Required evidence / qualifiers:**")
            for condition in decision.required_conditions:
                st.markdown(f"- {condition}")
        render_eligibility_source(decision)


def render_eligibility(assessment: EligibilityAssessment) -> None:
    st.divider()
    st.subheader("Evidence available for generation")
    st.write(
        "These are the product facts and claims the AI can use when creating the ad. "
        "Some claims can only be used with their supporting conditions or qualifiers preserved."
    )
    st.markdown(" · ".join(
        f"**{len(assessment.for_status(status))} {ELIGIBILITY_PRESENTATION[status][0]}**"
        for status in EligibilityStatus
    ))

    for status in EligibilityStatus:
        label, explanation = ELIGIBILITY_PRESENTATION[status]
        with st.expander(
            f"{label} ({len(assessment.for_status(status))})",
            expanded=False,
        ):
            st.caption(explanation)
            decisions = assessment.for_status(status)
            if not decisions:
                st.caption("No items in this group.")
            for decision in decisions:
                render_eligibility_decision(decision)

    st.caption("Price, offers, ratings and review counts are reference only—not automatically used in the ad.")


def configured_openai_api_key() -> str:
    try:
        return str(st.secrets.get("OPENAI_API_KEY", "")).strip()
    except (FileNotFoundError, KeyError):
        return ""


def configured_openai_model() -> str:
    environment_model = os.getenv("OPENAI_MODEL", "").strip()
    if environment_model:
        return environment_model
    try:
        secret_model = str(st.secrets.get("OPENAI_MODEL", "")).strip()
    except (FileNotFoundError, KeyError):
        secret_model = ""
    return secret_model or "gpt-5-mini"


def log_generation_api_error(exc: OpenAIError, api_key: str, stage: str) -> None:
    """Keep useful local diagnostics without logging credentials or request payloads."""
    message = str(exc).replace(api_key, "[REDACTED]") if api_key else str(exc)
    message = re.sub(r"(?i)\bsk-[a-z0-9_-]+\b", "[REDACTED]", message)
    message = re.sub(r"(?i)\bbearer\s+\S+", "Bearer [REDACTED]", message)
    logging.getLogger("minimalist_mvp.generation").error(
        "OpenAI %s failed: %s: %s", stage, type(exc).__name__, message[:1000]
    )


def generation_signature(
    assessment: EligibilityAssessment,
    image_reference: str,
    objective: str,
    audience: str,
) -> str:
    value = "|".join(
        (
            assessment.model_dump_json(),
            image_reference,
            objective.strip(),
            audience.strip(),
        )
    )
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def render_generated_creative(creative: GeneratedCreative, preview: bytes) -> None:
    st.subheader("Your creative")
    preview_column, content_column = st.columns([1.1, 1])
    with preview_column:
        st.image(preview, caption="Final 1080×1080 creative", width="stretch")
    with content_column:
        st.markdown("**Headline**")
        st.write(creative.draft.headline.text)
        st.markdown("**Supporting copy**")
        st.write(creative.draft.supporting_copy.text)
        if creative.draft.ingredient_callout is not None:
            st.markdown("**Ingredient / concentration**")
            st.write(creative.draft.ingredient_callout.text)
        st.markdown("**Call to action**")
        st.write(creative.draft.cta.text)

    used_ids = {
        evidence_id
        for element in (
            creative.draft.headline,
            creative.draft.supporting_copy,
            creative.draft.ingredient_callout,
        )
        if element is not None
        for evidence_id in element.evidence_ids
    }
    with st.expander("View creative evidence and image source", expanded=False):
        st.caption(f"Product image: {creative.product_image_reference} · {creative.product_image_evidence_id}")
        for entry in creative.evidence:
            if entry.evidence_id not in used_ids:
                continue
            with st.container(border=True):
                st.markdown(f"**{entry.evidence_id} · {entry.label}**")
                st.write(entry.exact_text)
                st.caption(entry.eligibility.value.replace("_", " ").title())
                if entry.required_conditions:
                    st.markdown("Required conditions:")
                    for condition in entry.required_conditions:
                        st.markdown(f"- {condition}")
                for source in entry.sources:
                    st.caption(
                        f"{source.section} · {source.source_url} · "
                        f"captured {source.captured_at.isoformat()}"
                    )


def render_scorer_report(report: ScorerReport, export_data: bytes | None = None,
                         export_name: str = "creative.png", copy_only: bool = False) -> None:
    st.subheader("Pre-flight review")
    label, css_class, gate_message = STATUS_PRESENTATION[report.overall]
    result_type = "Copy-only review" if copy_only else "Pre-flight result"
    if copy_only and report.export_allowed:
        gate_message = "Copy checks are clear within the available evidence."
    st.markdown(
        f'<div class="status-card {css_class}"><div class="eyebrow">{result_type}</div>'
        f'<div class="status-label">{label}</div><div>{gate_message}</div></div>',
        unsafe_allow_html=True,
    )
    if copy_only:
        st.caption("No image was supplied, so visual checks were not performed.")
    st.caption("Pre-flight within available evidence—not legal approval or guaranteed Meta approval.")
    columns = st.columns(3)
    for column, area in zip(columns, ("Policy & Claims", "Brand Tone", "Brand Language")):
        with column:
            st.subheader(f"{area} — {dimension_status(report.findings, area)}")
    if report.findings:
        st.markdown("**What needs attention**" if not report.export_allowed else "**Review notes**")
        for finding in report.findings:
            with st.container(border=True):
                st.markdown(f"**{finding_label(finding.status)} · {finding.rule_id} · {finding.area}**")
                st.write(f"Flagged: {finding.flagged_element}")
                st.write(f"Why: {finding.reason}")
                st.write(f"Suggested fix: {finding.suggested_fix}")
                if finding.evidence:
                    with st.expander("View supporting source / evidence"):
                        for source in finding.evidence:
                            st.caption(source)
    if report.export_allowed and copy_only:
        st.success("Copy review passed with warnings."
                   if report.overall == OverallStatus.PASS_WITH_WARNINGS else "Copy review passed.")
    elif report.export_allowed:
        st.success("Export allowed for this pre-flight result.")
        if export_data is not None:
            mime = ("text/plain" if export_name.endswith(".txt") else
                    "image/webp" if export_name.lower().endswith(".webp") else
                    "image/jpeg" if export_name.lower().endswith((".jpg", ".jpeg")) else
                    "image/png")
            st.download_button("Export creative", export_data, file_name=export_name,
                               mime=mime, type="primary")
    else:
        st.error("Export locked. Resolve the REVIEW or BLOCK issue and re-review.")


def render_generated_fix_loop(creative: GeneratedCreative, report: ScorerReport) -> None:
    if report.export_allowed:
        return
    st.subheader("Resolve this review")
    st.caption("Create a supported revision or edit the copy, then run the full review again.")
    editable = creative_copy(creative.draft)
    findings = unresolved_findings(report)
    if any(finding.flagged_element not in editable for finding in findings):
        st.caption("An issue in the image itself needs a revised image; changing copy here will not change its pixels.")
    revision_key = hashlib.sha256(
        (creative.draft.model_dump_json() + report.model_dump_json()).encode("utf-8")
    ).hexdigest()[:12]
    has_proposal = (st.session_state.get("generated_revision_source") == revision_key
                    and bool(st.session_state.get("generated_revision_proposal")))
    if any(finding.flagged_element in editable for finding in findings):
        if st.button("Create supported revision", key=f"generated_propose_{revision_key}",
                     type="secondary" if has_proposal else "primary"):
            try:
                client = OpenAI(api_key=configured_openai_api_key(), timeout=60, max_retries=0)
                proposal = propose_generated_revision(client, creative, findings,
                                                      model=configured_openai_model())
            except FixUnavailable:
                st.warning("We can’t safely create a supported revision with the available evidence.")
            except OpenAIError as exc:
                log_generation_api_error(exc, configured_openai_api_key(), "generated revision")
                st.error("A revision could not be proposed. Edit the copy manually.")
            else:
                st.session_state.generated_revision_proposal = proposal.model_dump(mode="json")
                st.session_state.generated_revision_source = revision_key
                st.rerun()
    proposed = (AdDraft.model_validate(st.session_state.generated_revision_proposal)
                if has_proposal else creative.draft)
    proposal_key = hashlib.sha256(proposed.model_dump_json().encode("utf-8")).hexdigest()[:12]
    with st.form(f"generated_manual_fix_{revision_key}_{proposal_key}"):
        st.markdown("**Proposed copy — review or edit before applying**" if proposed is not creative.draft
                    else "**Edit copy manually**")
        headline = st.text_input("Headline", value=proposed.headline.text)
        supporting = st.text_area("Supporting copy", value=proposed.supporting_copy.text)
        callout = st.text_input("Ingredient / concentration callout",
                                value=proposed.ingredient_callout.text if proposed.ingredient_callout else "")
        cta = st.text_input("CTA", value=proposed.cta.text)
        submitted = st.form_submit_button("Apply & re-review",
                                          type="primary" if has_proposal else "secondary")
    if submitted:
        try:
            payload = proposed.model_dump()
            payload["headline"]["text"] = headline
            payload["supporting_copy"]["text"] = supporting
            if callout and payload["ingredient_callout"] is None:
                raise FixUnavailable("A new callout needs cited eligible evidence; edit an existing callout instead.")
            payload["ingredient_callout"] = ({**payload["ingredient_callout"], "text": callout}
                                             if callout and payload["ingredient_callout"] else None)
            payload["cta"]["text"] = cta
            draft = AdDraft.model_validate(payload)
            product_image = load_product_image(creative.product_image_reference,
                                               st.session_state.get("manual_product_image"))
            client = OpenAI(api_key=configured_openai_api_key(), timeout=60, max_retries=0)
            updated, preview, new_report = revise_generated(
                client, creative, draft, product_image, model=configured_openai_model(),
            )
        except (FixUnavailable, GenerationUnavailable, ValueError) as exc:
            st.error(f"The edit could not be used safely: {exc}")
        except OpenAIError as exc:
            log_generation_api_error(exc, configured_openai_api_key(), "manual re-review")
            st.error("The correction could not be completed. Please try again.")
        else:
            st.session_state.generated_creative = updated.model_dump(mode="json")
            st.session_state.generated_preview = preview
            st.session_state.generated_render_version = CREATIVE_RENDER_VERSION
            st.session_state.generated_review = new_report.model_dump(mode="json")
            st.session_state.generated_revision_proposal = None
            st.session_state.generated_revision_source = None
            st.rerun()


def render_generation(
    assessment: EligibilityAssessment,
    image_reference: str,
    manual_image: bytes | None,
) -> None:
    st.divider()
    st.subheader("Create your ad")
    st.caption("Optional context can guide the message; it cannot add product claims.")
    objective = st.selectbox(
        "Campaign objective (optional)",
        ("", "Awareness", "Consideration", "Conversion"),
        format_func=lambda value: value or "Default — present the product clearly",
        help="Guides which supported details to emphasize and sets the ad's neutral CTA.",
    )
    audience = st.text_area(
        "Audience context (optional)",
        placeholder="For example: people building a simple evening skincare routine",
        max_chars=300,
        help=(
            "Helps emphasize relevant, source-backed product wording in the ad. "
            "It does not change the product photo or create new claims."
        ),
    )

    api_key = configured_openai_api_key()
    if not api_key:
        st.error("OpenAI API key is not configured on the server.")

    signature = generation_signature(assessment, image_reference, objective, audience)
    if (st.session_state.get("generated_preview")
            and st.session_state.get("generated_render_version") != CREATIVE_RENDER_VERSION):
        st.session_state.generated_creative = None
        st.session_state.generated_preview = None
        st.session_state.generated_review = None
        st.info("The creative preview was updated. Generate again to review the current version.")
    if st.button("Generate one creative", type="primary", disabled=not bool(api_key)):
        api_stage = "generation"
        try:
            with st.spinner("Creating one evidence-bounded ad…"):
                product_image = load_product_image(image_reference, manual_image)
                client = OpenAI(api_key=api_key, timeout=120, max_retries=0)
                creative = generate_ad_content(
                    client,
                    assessment,
                    image_reference,
                    GenerationContext(objective=objective, audience=audience),
                    model=configured_openai_model(),
                )
                preview = render_creative_preview(creative, product_image)
                api_stage = "review"
                review_client = OpenAI(api_key=api_key, timeout=60, max_retries=0)
                report = score_creative(
                    review_client, ad_copy="\n".join(
                        element.text for element in (
                            creative.draft.headline, creative.draft.supporting_copy,
                            creative.draft.ingredient_callout, creative.draft.cta,
                        ) if element is not None
                    ), image_bytes=preview, reference_image_bytes=product_image,
                    context=generated_context(creative),
                    model=configured_openai_model(),
                )
        except GenerationUnavailable as exc:
            st.error(str(exc))
        except (APITimeoutError, APIConnectionError) as exc:
            log_generation_api_error(exc, api_key, api_stage)
            st.error(
                "Generation took longer than expected. Please try again."
                if api_stage == "generation"
                else "The generation request could not be completed. Check the server configuration and try again."
            )
        except OpenAIError as exc:
            log_generation_api_error(exc, api_key, api_stage)
            st.error("The generation request could not be completed. Check the server configuration and try again.")
        else:
            st.session_state.generated_creative = creative.model_dump(mode="json")
            st.session_state.generated_preview = preview
            st.session_state.generated_render_version = CREATIVE_RENDER_VERSION
            st.session_state.generated_review = report.model_dump(mode="json")
            st.session_state.generated_revision_proposal = None
            st.session_state.generated_revision_source = None
            st.session_state.generation_signature = signature

    stored_creative = st.session_state.get("generated_creative")
    stored_preview = st.session_state.get("generated_preview")
    if stored_creative and stored_preview:
        if st.session_state.get("generation_signature") == signature:
            render_generated_creative(GeneratedCreative.model_validate(stored_creative), stored_preview)
            stored_report = st.session_state.get("generated_review")
            if stored_report:
                report = ScorerReport.model_validate(stored_report)
                render_scorer_report(report, stored_preview)
                render_generated_fix_loop(GeneratedCreative.model_validate(stored_creative), report)
        else:
            st.info("The inputs changed. Generate again to see a creative for the current selections.")


def render_extraction(
    extraction: ProductExtraction,
    manual_image: bytes | None = None,
) -> None:
    if extraction.manual_image_name is not None or extraction.product_name_source.method == "user-provided":
        st.success("Product information added. Confirm the details before creating an ad.")
    else:
        st.success("Product page read. Confirm the details before creating an ad.")

    for warning in extraction.warnings:
        st.warning(warning)

    st.subheader("Confirm this product")
    st.markdown(f"### {extraction.product_name}")
    confirmation_key = f"confirmed-product-{extraction.captured_at.isoformat()}"
    confirmation = st.session_state.get(confirmation_key)
    if confirmation:
        confirmed_message, edit_action = st.columns([3, 1])
        with confirmed_message:
            st.success("Product confirmed", icon="✅")
        with edit_action:
            if st.button("Edit product", key=f"edit-product-{extraction.captured_at.isoformat()}"):
                st.session_state[confirmation_key] = None
                st.session_state.verified_review_context = None
                st.rerun()

    identity_left, identity_right = st.columns(2)

    selected_variant: ProductVariant | None = None
    selected_image: ProductImage | None = None
    with identity_left:
        if confirmation:
            selected_variant = next(
                (variant for variant in extraction.variants if variant.id == confirmation["variant_id"]),
                None,
            )
            st.markdown(f"**Variant / size:** {variant_label(selected_variant) if selected_variant else 'Not available'}")
        elif len(extraction.variants) > 1:
            initial_variant = next(
                (
                    variant
                    for variant in extraction.variants
                    if variant.id == extraction.requested_variant_id
                ),
                None,
            )
            selected_variant = st.selectbox(
                "Select the exact variant / size",
                extraction.variants,
                index=(
                    extraction.variants.index(initial_variant)
                    if initial_variant in extraction.variants
                    else None
                ),
                placeholder="Choose a variant before verification",
                format_func=variant_label,
                key=f"variant-{extraction.captured_at.isoformat()}",
            )
            if selected_variant is None:
                st.warning("Multiple variants were found. Select one rather than relying on the page default.")
        elif extraction.variants:
            selected_variant = extraction.variants[0]
            st.selectbox(
                "Select the exact variant / size",
                extraction.variants,
                index=0,
                format_func=variant_label,
                disabled=True,
                key=f"variant-{extraction.captured_at.isoformat()}",
            )
        else:
            st.markdown("**Variant / size:** Not available on the page")

    with identity_right:
        if confirmation:
            selected_image = next(
                (image for image in extraction.images if image.url == confirmation["image_url"]),
                None,
            )
            st.markdown(f"**Product image:** {selected_image.label if selected_image else extraction.manual_image_name or 'Not available'}")
        elif manual_image:
            st.markdown(f"**Product image:** {extraction.manual_image_name}")
        elif extraction.images:
            default_image = None
            if selected_variant:
                default_image = next(
                    (
                        image
                        for image in extraction.images
                        if image.variant_id == selected_variant.id
                    ),
                    None,
                )
            selected_image = st.selectbox(
                "Select the product image",
                extraction.images,
                index=(
                    extraction.images.index(default_image)
                    if default_image in extraction.images
                    else None
                ),
                placeholder="Choose the image that matches the product",
                format_func=image_label,
                key=f"image-{extraction.captured_at.isoformat()}",
            )
            if not selected_image:
                st.info("Choose the exact product image rather than letting the system guess.")
        else:
            st.warning("No product image is available for verification.")

        if manual_image:
            st.image(manual_image, caption=extraction.manual_image_name, width="stretch")
        elif selected_image:
            st.image(selected_image.url, caption=selected_image.label, width="stretch")

    commercial_items = list(extraction.commercial)
    if selected_variant:
        commercial_items = variant_commercial_items(
            selected_variant,
            extraction.source_url,
            extraction.captured_at,
        ) + commercial_items

    st.markdown("**Key product facts**")
    render_summary_items(extraction.facts, "No product facts found.")
    claims_column, evidence_column = st.columns(2)
    with claims_column:
        st.markdown("**Key claims**")
        render_summary_items(extraction.claims, "No benefit claims found.")
    with evidence_column:
        st.markdown("**Supporting evidence and qualifiers**")
        render_summary_items(extraction.evidence, "No supporting statements found.")
    st.markdown("**Price, offers and ratings · reference only**")
    render_summary_items([*commercial_items, *extraction.social_proof],
                         "No price, offer or rating information found.", limit=4)

    with st.expander("View full extracted details", expanded=False):
        st.caption(f"Source: {extraction.source_url} · Captured: {extraction.captured_at.isoformat()}")
        st.markdown("**Product name source**")
        st.write(extraction.product_name_source.wording)
        st.caption(f"{extraction.product_name_source.section} · {extraction.product_name_source.method}")
        if selected_variant and selected_variant.sku:
            st.caption(f"Selected SKU: {selected_variant.sku}")
        if selected_image:
            st.caption(f"Selected image source: {selected_image.url}")
        st.markdown("**All product facts**")
        render_items(extraction.facts, "No product facts were found in the supplied source.")
        st.markdown("**All claims**")
        render_items(extraction.claims, "No benefit or efficacy statements were found.")
        st.markdown("**All evidence and qualifiers**")
        render_items(extraction.evidence, "No study, testing, or qualifier statements were found.")
        st.markdown("**All price and offer information · reference only**")
        render_items(commercial_items, "No price, MRP, discount, or offer was found.")
        st.markdown("**All ratings and reviews · reference only**")
        render_items(extraction.social_proof, "No rating or review count was found.")

    identity_ready = bool(extraction.product_name) and bool(manual_image or selected_image)
    variant_ready = len(extraction.variants) <= 1 or selected_variant is not None
    if not confirmation and st.button(
        "Confirm product",
        type="primary",
        disabled=not (identity_ready and variant_ready),
        key=f"confirm-product-{extraction.captured_at.isoformat()}",
    ):
        confirmation = {
            "variant_id": selected_variant.id if selected_variant else None,
            "image_url": selected_image.url if selected_image else None,
        }
        st.session_state[confirmation_key] = confirmation
        st.rerun()
    if confirmation:
        assessment = assess_generation_eligibility(extraction, selected_variant)
        render_eligibility(assessment)
        image_reference = (
            extraction.manual_image_name
            if manual_image
            else selected_image.url if selected_image else ""
        )
        review_context = extracted_context(
            extraction, selected_variant.title if selected_variant else "",
        )
        review_context.product_image_reference = image_reference
        st.session_state.verified_review_context = review_context.model_dump(mode="json")
        if image_reference:
            render_generation(assessment, image_reference, manual_image)
    else:
        st.session_state.verified_review_context = None
        if not (identity_ready and variant_ready):
            st.caption("Resolve the product identity choices above before verification.")


def render_manual_fallback(source_url: str) -> None:
    st.subheader("Enter product information manually")
    st.write(
        "Paste exact wording from the product page into the matching groups. "
        "Each non-empty line is retained as user-provided source wording."
    )
    with st.form("manual-product-form"):
        product_name = st.text_input("Product name")
        variant = st.text_input("Variant / size, if available")
        product_image = st.file_uploader(
            "Product image",
            type=["png", "jpg", "jpeg", "webp"],
        )
        facts_text = st.text_area("Product facts", help="One fact per line")
        claims_text = st.text_area("Claims", help="One exact claim per line")
        evidence_text = st.text_area("Evidence and important qualifiers", help="One statement per line")
        commercial_text = st.text_area("Commercial information", help="Price, MRP, discount, or offers")
        social_text = st.text_area("Social proof", help="Rating and review count, if available")
        submitted = st.form_submit_button("Use manual information", type="primary")

    if submitted:
        try:
            extraction = manual_product_extraction(
                source_url=source_url,
                product_name=product_name,
                variant=variant,
                facts_text=facts_text,
                claims_text=claims_text,
                evidence_text=evidence_text,
                commercial_text=commercial_text,
                social_proof_text=social_text,
                image_name=product_image.name if product_image else None,
            )
        except ProductReadError as exc:
            st.error(str(exc))
        else:
            st.session_state.product_extraction = extraction
            st.session_state.manual_product_image = product_image.getvalue() if product_image else None
            st.session_state.product_read_error = None
            st.session_state.generated_creative = None
            st.session_state.generated_preview = None
            st.session_state.generated_review = None
            st.session_state.verified_review_context = None
            st.rerun()


def render_product_flow() -> None:
    st.markdown("### Create & Review")
    current = "Product"
    if st.session_state.get("product_extraction"):
        current = "Confirm"
        extraction = st.session_state.product_extraction
        if st.session_state.get(f"confirmed-product-{extraction.captured_at.isoformat()}"):
            current = "Generate"
    stored_report = st.session_state.get("generated_review")
    if stored_report:
        current = ("Export" if stored_report.get("export_allowed") else "Fix & Re-review")
    render_steps(("Product", "Confirm", "Generate", "Review", "Fix & Re-review", "Export"), current)
    st.write("Start with a Minimalist product page. Confirm what we found before creating the ad.")
    source_url = st.text_input(
        "Minimalist product URL",
        placeholder="https://beminimalist.co/products/... or /collections/.../products/...",
        help="Direct and collection-scoped product links are accepted; www links may omit https://.",
    )
    if st.button("Extract product information",
                 type="secondary" if st.session_state.get("product_extraction") else "primary"):
        st.session_state.product_extraction = None
        st.session_state.manual_product_image = None
        st.session_state.generated_creative = None
        st.session_state.generated_preview = None
        st.session_state.generated_review = None
        st.session_state.verified_review_context = None
        try:
            with st.spinner("Reading the product page…"):
                st.session_state.product_extraction = read_product_url(source_url)
            st.session_state.product_read_error = None
        except ProductReadError as exc:
            st.session_state.product_read_error = str(exc)

    read_error = st.session_state.get("product_read_error")
    extraction = st.session_state.get("product_extraction")
    if read_error:
        st.error(read_error)
        st.info("Use the manual fallback below. The app will not infer missing information.")

    manual_requested = st.checkbox(
        "Use manual fallback",
        value=bool(read_error),
        help="Use this when the product page cannot be read or its content is incomplete.",
    )
    if manual_requested:
        render_manual_fallback(source_url)

    if extraction:
        st.divider()
        render_extraction(extraction, st.session_state.get("manual_product_image"))


def render_review_demo() -> None:
    st.info(
        "See how different review outcomes affect export.",
        icon="ℹ️",
    )
    scenario_name = st.selectbox(
        "Sample situation",
        options=list(SCENARIOS),
        help="These samples exercise the frozen decision logic; the status is not manually selected.",
    )
    scenario = SCENARIOS[scenario_name]
    decision = decide_review(list(scenario.findings))

    st.caption(scenario.description)
    label, css_class, gate_message = STATUS_PRESENTATION[decision.status]
    st.markdown(
        f"""
        <div class="status-card {css_class}">
          <div class="eyebrow">Overall pre-flight result</div>
          <div class="status-label">{label}</div>
          <div>{gate_message}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([2, 1])
    with left:
        st.subheader("Findings")
        for finding in decision.findings:
            with st.container(border=True):
                st.markdown(f"**{finding_label(finding.outcome)} · {finding.rule_id}**")
                st.markdown(finding.title)
                st.caption(finding.message)
    with right:
        st.subheader("Export eligibility")
        if decision.export_allowed:
            st.success("Allowed", icon="✅")
        else:
            st.error("Locked", icon="🔒")


EXTERNAL_CONTEXT_CHOICES = (
    "Use the product confirmed in Create & Review",
    "Use another Minimalist product URL",
    "Review without product context",
)
EXTERNAL_STEPS = ("Add Creative", "Add Context", "Review", "Fix & Re-review", "Export")
EMPTY_CREATIVE_MESSAGE = "There is no creative left to review. Add or revise the ad before continuing."


def external_current_stage() -> str:
    return st.session_state.get("external_stage", "creative")


def external_step_label() -> str:
    stage = external_current_stage()
    if stage == "creative":
        return "Add Creative"
    if stage == "context":
        return "Add Context"
    if stage == "review":
        return "Review"
    report = st.session_state.get("external_review") or {}
    return ("Export" if report.get("export_allowed") and
            st.session_state.get("external_image_bytes") else
            "Review" if report.get("export_allowed") else "Fix & Re-review")


def external_context_name(context: ReviewContext) -> str:
    if context.product_name:
        return f"{context.product_name} — {context.variant}" if context.variant else context.product_name
    return "No confirmed product"


def render_external_creative_summary() -> None:
    copy = st.session_state.get("external_current_copy", "")
    image_name = st.session_state.get("external_image_name")
    left, right = st.columns([4, 1])
    with left:
        st.caption(f"Creative added · {'Image: ' + image_name if image_name else 'No image'}"
                   f" · {'Pasted copy' if copy.strip() else 'No pasted copy'}")
    with right:
        if st.button("Edit creative", key="external_edit_creative"):
            st.session_state.external_stage = "creative"
            st.session_state.external_copy_revision = st.session_state.get("external_copy_revision", 0) + 1
            st.session_state.external_review = None
            st.session_state.external_revision_proposal = None
            st.rerun()


def render_external_context_summary() -> None:
    context = ReviewContext.model_validate(st.session_state.get("external_review_context", {}))
    left, right = st.columns([4, 1])
    with left:
        if context.product_name:
            st.info(f"Using product context: {external_context_name(context)}")
        elif context.evidence:
            st.caption("Context added · exact source wording supplied; no product selected")
        else:
            st.caption("Reviewing without product context · material claims may need review")
    with right:
        if st.button("Edit context", key="external_edit_context"):
            st.session_state.external_stage = "context"
            st.session_state.external_review = None
            st.session_state.external_revision_proposal = None
            st.rerun()


def render_external_copy_reference() -> None:
    current = st.session_state.get("external_current_copy", "")
    st.markdown("**Creative being reviewed**")
    image_bytes = st.session_state.get("external_image_bytes")
    if image_bytes:
        st.image(image_bytes, caption="Uploaded creative", width=240)
    else:
        st.info("Copy-only review — visual checks were not performed.")
    if current:
        st.code(current, language=None)
    elif image_bytes:
        st.caption("No separate ad copy supplied; the uploaded image is being reviewed.")
    original = st.session_state.get("external_original_copy", "")
    if original and original != current:
        with st.expander("View original copy"):
            st.code(original, language=None)


def render_external_creative_stage() -> None:
    st.caption("Upload the finished image, paste ad copy, or provide both.")
    saved_image = st.session_state.get("external_image_bytes")
    if saved_image:
        st.image(saved_image, caption=f"Current image: {st.session_state.get('external_image_name', 'creative')}", width=300)
    uploaded = st.file_uploader("Creative image", type=["png", "jpg", "jpeg", "webp"],
                                key="external_creative_image")
    if uploaded:
        st.image(uploaded.getvalue(), caption=f"New image: {uploaded.name}", width=300)
    remove_saved = st.checkbox("Remove current image", disabled=not bool(saved_image))
    copy_key = f"external_copy_input_{st.session_state.get('external_copy_revision', 0)}"
    ad_copy = st.text_area("Ad copy (optional if an image is uploaded)",
                           value=st.session_state.get("external_current_copy", ""), key=copy_key)
    image_bytes = (uploaded.getvalue() if uploaded else None) or (None if remove_saved else saved_image)
    if ad_copy and not has_meaningful_creative(ad_copy, image_bytes):
        st.warning(EMPTY_CREATIVE_MESSAGE)
    if st.button("Continue to context", type="primary"):
        if not has_meaningful_creative(ad_copy, image_bytes):
            st.error(EMPTY_CREATIVE_MESSAGE)
            return
        if "external_original_copy" not in st.session_state:
            st.session_state.external_original_copy = ad_copy
        st.session_state.external_current_copy = ad_copy
        st.session_state.external_image_bytes = image_bytes
        st.session_state.external_image_name = (uploaded.name if uploaded else
            None if remove_saved else st.session_state.get("external_image_name"))
        st.session_state.external_stage = "context"
        st.session_state.external_review = None
        st.session_state.external_revision_proposal = None
        st.rerun()


def render_external_alternate_product() -> tuple[ReviewContext | None, bool]:
    product_url = st.text_input("Minimalist product URL for this ad",
                                value=st.session_state.get("external_product_url", ""),
                                placeholder="https://beminimalist.co/products/...")
    if st.button("Read product URL"):
        try:
            with st.spinner("Reading the product page…"):
                extraction = read_product_url(product_url)
        except ProductReadError as exc:
            st.session_state.external_product_extraction = None
            st.error(str(exc))
        else:
            st.session_state.external_product_extraction = extraction
            st.session_state.external_product_url = product_url
            st.rerun()
    extraction = st.session_state.get("external_product_extraction")
    if not extraction or product_url != st.session_state.get("external_product_url"):
        return None, False
    st.markdown(f"**{extraction.product_name}**")
    selected_variant = None
    if len(extraction.variants) > 1:
        selected_variant = st.selectbox(
            "Select exact variant / size", extraction.variants, index=None,
            placeholder="Choose the pack used in this ad", format_func=variant_label,
            key=f"external_variant_{extraction.captured_at.isoformat()}",
        )
    elif extraction.variants:
        selected_variant = extraction.variants[0]
        st.caption(f"Variant: {selected_variant.title}")
    selected_image = None
    if extraction.images:
        selected_image = st.selectbox(
            "Select the matching product image", extraction.images,
            index=0 if len(extraction.images) == 1 else None,
            placeholder="Choose the matching pack", format_func=image_label,
            key=f"external_image_{extraction.captured_at.isoformat()}",
        )
        if selected_image:
            st.image(selected_image.url, caption=selected_image.label, width=220)
    with st.expander("View extracted product details and sources"):
        st.caption(f"Source: {extraction.source_url} · Captured: {extraction.captured_at.isoformat()}")
        for label, items in (("Product facts", extraction.facts), ("Claims", extraction.claims),
                             ("Evidence and qualifiers", extraction.evidence)):
            st.markdown(f"**{label}**")
            render_items(items, "None found.")
    ready = (len(extraction.variants) <= 1 or selected_variant is not None) and selected_image is not None
    confirmed = st.checkbox("I confirm this product, variant and image match the ad",
                            disabled=not ready,
                            key=f"external_confirm_{extraction.captured_at.isoformat()}")
    if not ready:
        st.caption("Select the exact variant and product image before continuing.")
    if not confirmed:
        return None, False
    context = extracted_context(extraction, selected_variant.title if selected_variant else "")
    context.product_image_reference = selected_image.url
    st.info(f"Using product context: {external_context_name(context)}")
    return context, True


def render_external_context_stage() -> None:
    st.caption("Product context improves claims checks. Without it, material claims may need REVIEW.")
    available = bool(st.session_state.get("verified_review_context"))
    default = st.session_state.get("external_context_mode") or EXTERNAL_CONTEXT_CHOICES[2]
    choice = st.radio("Choose product context", EXTERNAL_CONTEXT_CHOICES,
                      index=EXTERNAL_CONTEXT_CHOICES.index(default))
    context = ReviewContext()
    ready = True
    if choice == EXTERNAL_CONTEXT_CHOICES[0]:
        if available:
            context = ReviewContext.model_validate(st.session_state.verified_review_context)
            st.info(f"Using product context: {external_context_name(context)}")
        else:
            st.warning("No product is confirmed yet. Confirm one in Create & Review or choose another option.")
            ready = False
    elif choice == EXTERNAL_CONTEXT_CHOICES[1]:
        context, ready = render_external_alternate_product()
    else:
        st.caption("We will not treat missing product evidence as a PASS for material claims.")
    with st.expander("Advanced: add exact supporting evidence",
                     expanded=bool(st.session_state.get("external_show_evidence", False))):
        source_text = st.text_area("Additional exact product/evidence wording (optional)",
                                   value=st.session_state.get("external_extra_source_text", ""),
                                   help="Paste source wording only. An unsourced assertion is not independent substantiation.")
        source_url = st.text_input("Source URL for additional wording (optional)",
                                   value=st.session_state.get("external_extra_source_url", ""))
    if st.button("Continue to review", type="primary", disabled=not ready):
        context = context or ReviewContext()
        if source_text.strip():
            context.evidence.append(ReviewEvidence(
                evidence_id="USER-001", exact_text=source_text.strip(), source_url=source_url.strip(),
            ))
        st.session_state.external_review_context = context.model_dump(mode="json")
        st.session_state.external_context_mode = choice
        st.session_state.external_extra_source_text = source_text
        st.session_state.external_extra_source_url = source_url
        st.session_state.external_stage = "review"
        st.session_state.external_show_evidence = False
        st.session_state.external_review = None
        st.session_state.external_revision_proposal = None
        st.rerun()


def render_external_review_stage() -> None:
    render_external_copy_reference()
    image_bytes = st.session_state.get("external_image_bytes")
    if image_bytes:
        st.image(image_bytes, caption="Image that will be reviewed", width=300)
    api_key = configured_openai_api_key()
    if not api_key:
        st.error("OpenAI API key is not configured on the server.")
    if st.button("Run pre-flight review", type="primary", disabled=not bool(api_key)):
        context = ReviewContext.model_validate(st.session_state.get("external_review_context", {}))
        reference_image = None
        if context.product_image_reference and image_bytes:
            try:
                manual_image = (st.session_state.get("manual_product_image")
                                if st.session_state.get("external_context_mode") == EXTERNAL_CONTEXT_CHOICES[0]
                                else None)
                reference_image = load_product_image(context.product_image_reference, manual_image)
            except GenerationUnavailable:
                context.review_notes += " Selected pack image could not be read; pack comparison is not assessable."
        try:
            with st.spinner("Reviewing the creative…"):
                report = score_creative(
                    OpenAI(api_key=api_key, timeout=60, max_retries=0),
                    ad_copy=st.session_state.get("external_current_copy", ""),
                    image_bytes=image_bytes, reference_image_bytes=reference_image,
                    context=context, model=configured_openai_model(),
                )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.external_review = report.model_dump(mode="json")
            st.session_state.external_review_context = context.model_dump(mode="json")
            st.session_state.external_reference_image = reference_image
            st.session_state.external_stage = "result"
            st.rerun()


def render_external_result_stage() -> None:
    stored = st.session_state.get("external_review")
    if not stored:
        st.session_state.external_stage = "review"
        st.rerun()
    report = ScorerReport.model_validate(stored)
    current_copy = st.session_state.get("external_current_copy", "")
    image_bytes = st.session_state.get("external_image_bytes")
    render_external_copy_reference()
    context = ReviewContext.model_validate(st.session_state.get("external_review_context", {}))
    visual_coverage = ("reviewed" if report.image_readability in ("READABLE", "PARTIAL")
                       else "couldn’t assess") if image_bytes else "not supplied"
    st.caption("Assessment coverage · Copy: " + ("reviewed" if current_copy.strip() else "not supplied")
               + " · Product evidence: " + ("supplied" if context.evidence else "not supplied")
               + " · Visual creative: " + visual_coverage)
    render_scorer_report(report, image_bytes,
                         st.session_state.get("external_image_name") or "creative.png",
                         copy_only=image_bytes is None)
    if report.export_allowed:
        if image_bytes is None and st.button("Add final creative", type="primary"):
            st.session_state.external_stage = "creative"
            st.session_state.external_copy_revision = st.session_state.get("external_copy_revision", 0) + 1
            st.rerun()
        return
    findings = unresolved_findings(report)
    editable = [finding for finding in findings
                if finding.flagged_element.strip() and finding.flagged_element in current_copy]
    st.markdown("**Resolve this review**")
    revision_key = hashlib.sha256(
        (current_copy + report.model_dump_json()).encode("utf-8")
    ).hexdigest()[:12]
    api_key = configured_openai_api_key()
    has_proposal = (st.session_state.get("external_revision_source") == revision_key
                    and st.session_state.get("external_revision_proposal") is not None)
    if editable and has_meaningful_creative(current_copy, None):
        if st.button("Create supported revision", key=f"external_propose_{revision_key}",
                     type="secondary" if has_proposal else "primary",
                     disabled=not bool(api_key)):
            try:
                client = OpenAI(api_key=api_key, timeout=60, max_retries=0)
                proposal = propose_external_revision(client, current_copy, findings, context,
                                                     model=configured_openai_model())
            except FixUnavailable:
                st.session_state.external_revision_unavailable = True
                st.rerun()
            except OpenAIError as exc:
                log_generation_api_error(exc, api_key, "external revision")
                st.error("A revision could not be proposed. Edit the copy manually.")
            else:
                st.session_state.external_revision_unavailable = False
                st.session_state.external_revision_proposal = proposal
                st.session_state.external_revision_source = revision_key
                st.rerun()
    if not editable or st.session_state.get("external_revision_unavailable"):
        st.warning("We can’t safely create a supported revision with the available evidence.")
    if image_bytes and not editable:
        st.caption("The issue is in the image itself. Upload a revised creative to change it.")
    actions = st.columns(2)
    with actions[0]:
        if st.button("Add product evidence", key="external_add_evidence"):
            st.session_state.external_stage = "context"
            st.session_state.external_show_evidence = True
            st.session_state.external_review = None
            st.session_state.external_revision_proposal = None
            st.session_state.external_revision_unavailable = False
            st.rerun()
    with actions[1]:
        if current_copy and st.button("Edit copy manually", key="external_manual_edit_button"):
            st.session_state.external_manual_edit = True
            st.rerun()
        elif not current_copy and st.button("Upload revised creative", key="external_upload_revision"):
            st.session_state.external_stage = "creative"
            st.rerun()
    if not current_copy or (image_bytes and not editable):
        return
    if not has_proposal and not st.session_state.get("external_manual_edit"):
        return
    proposed = (st.session_state.external_revision_proposal
                if has_proposal else current_copy)
    proposal_key = hashlib.sha256(proposed.encode("utf-8")).hexdigest()[:12]
    with st.form(f"external_revision_form_{revision_key}_{proposal_key}"):
        st.markdown("**Proposed copy — review or edit before applying**" if proposed != current_copy
                    else "**Edit current copy manually**")
        revised_copy = st.text_area("Current ad copy", value=proposed, height=150)
        submitted = st.form_submit_button("Apply & re-review",
                                          type="primary" if has_proposal else "secondary",
                                          disabled=not bool(api_key))
    if submitted:
        try:
            client = OpenAI(api_key=api_key, timeout=60, max_retries=0)
            new_report = revise_external(
                client, revised_copy, image_bytes, context,
                reference_image_bytes=st.session_state.get("external_reference_image"),
                model=configured_openai_model(),
            )
        except OpenAIError as exc:
            log_generation_api_error(exc, api_key, "external re-review")
            st.error("The revision could not be reviewed. Please try again.")
        else:
            st.session_state.external_current_copy = revised_copy
            st.session_state.external_review = new_report.model_dump(mode="json")
            st.session_state.external_revision_proposal = None
            st.session_state.external_revision_source = None
            st.session_state.external_manual_edit = False
            st.session_state.external_revision_unavailable = False
            st.session_state.external_copy_revision = st.session_state.get("external_copy_revision", 0) + 1
            st.rerun()


def render_external_review() -> None:
    st.markdown("### Review Existing Ad")
    stage = external_current_stage()
    current = external_step_label()
    render_steps(EXTERNAL_STEPS, current)
    st.subheader(current)
    if stage == "creative":
        render_external_creative_stage()
        return
    render_external_creative_summary()
    if stage == "context":
        render_external_context_stage()
        return
    render_external_context_summary()
    if stage == "review":
        render_external_review_stage()
        return
    render_external_result_stage()


st.title("Minimalist Ad Pre-flight")
st.caption("Create a Minimalist India ad or check an existing one before it goes live.")

product_tab, external_tab = st.tabs(["Create & Review", "Review Existing Ad"])
with product_tab:
    render_product_flow()
with external_tab:
    render_external_review()

with st.expander("See sample review outcomes (demo)", expanded=False):
    render_review_demo()
