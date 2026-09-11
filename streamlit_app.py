import streamlit as st

from minimalist_mvp.demo_scenarios import SCENARIOS
from minimalist_mvp.eligibility import (
    EligibilityAssessment,
    EligibilityDecision,
    EligibilityStatus,
    assess_generation_eligibility,
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
    }
    .status-pass { border-left-color: #157f3b; }
    .status-warn { border-left-color: #bf7300; }
    .status-review { border-left-color: #b45f06; }
    .status-block { border-left-color: #b42318; }
    .status-label { font-size: 1.45rem; font-weight: 750; margin-bottom: 0.2rem; }
    .eyebrow { color: #5d6673; font-size: 0.82rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }
    .workflow { color: #4d5662; font-size: 0.95rem; margin-bottom: 1.4rem; }
    .reference-note { color: #5d6673; font-size: 0.9rem; margin-top: -0.5rem; margin-bottom: 1rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


STATUS_PRESENTATION = {
    OverallStatus.PASS: ("PASS", "status-pass", "Export gate is open."),
    OverallStatus.PASS_WITH_WARNINGS: (
        "PASS with warnings",
        "status-warn",
        "Export gate is open. Review the non-blocking warnings first.",
    ),
    OverallStatus.REVIEW: (
        "REVIEW",
        "status-review",
        "Export remains locked until every material review item is resolved.",
    ),
    OverallStatus.BLOCK: (
        "BLOCK",
        "status-block",
        "Export remains locked until the violation is fixed and the ad is re-reviewed.",
    ),
}


def finding_label(outcome: FindingOutcome) -> str:
    return {
        FindingOutcome.PASS: "Pass",
        FindingOutcome.WARN: "Warning",
        FindingOutcome.REVIEW: "Review",
        FindingOutcome.BLOCK: "Block",
        FindingOutcome.NOT_ASSESSABLE: "Not Assessable",
    }[outcome]


def render_source(item: ExtractedItem) -> None:
    with st.expander("View source"):
        st.markdown(f"**Section:** {item.source.section}")
        st.write(item.source.wording)
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
    EligibilityStatus.ELIGIBLE: ("Eligible", "Exact sourced wording available to generation."),
    EligibilityStatus.ELIGIBLE_REVIEW_REQUIRED: (
        "Eligible but review required",
        "Available only with the listed evidence and conditions preserved.",
    ),
    EligibilityStatus.INELIGIBLE: ("Ineligible", "Not available to generation."),
    EligibilityStatus.NOT_ASSESSABLE: (
        "Not Assessable",
        "Not available unless the missing context or evidence is resolved.",
    ),
}


def render_eligibility_source(decision: EligibilityDecision) -> None:
    with st.expander("View source / evidence"):
        if not decision.sources:
            st.caption("No traceable source was available.")
        for index, source in enumerate(decision.sources, 1):
            if len(decision.sources) > 1:
                st.markdown(f"**Source {index} · {source.section}**")
            else:
                st.markdown(f"**{source.section}**")
            st.write(source.wording)
            st.caption(
                f"{source.source_url} · Captured {source.captured_at.isoformat()} · {source.method}"
            )


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
    st.subheader("Generation eligibility")
    st.write(
        "This is the controlled set that a later generator may use. "
        "It does not generate an ad or provide final policy approval."
    )
    columns = st.columns(4)
    for column, status in zip(columns, EligibilityStatus):
        label, _ = ELIGIBILITY_PRESENTATION[status]
        column.metric(label, len(assessment.for_status(status)))

    for status in EligibilityStatus:
        label, explanation = ELIGIBILITY_PRESENTATION[status]
        with st.expander(
            f"{label} ({len(assessment.for_status(status))})",
            expanded=status in {EligibilityStatus.ELIGIBLE_REVIEW_REQUIRED, EligibilityStatus.INELIGIBLE},
        ):
            st.caption(explanation)
            decisions = assessment.for_status(status)
            if not decisions:
                st.caption("No items in this group.")
            for decision in decisions:
                render_eligibility_decision(decision)

    st.info(
        "Price, MRP, offers, ratings, and review counts remain reference-only and are excluded from generation eligibility.",
        icon="ℹ️",
    )


def render_extraction(
    extraction: ProductExtraction,
    manual_image: bytes | None = None,
) -> None:
    if extraction.manual_image_name is not None or extraction.product_name_source.method == "user-provided":
        st.success("Manual product information loaded. Check it before continuing.")
    else:
        st.success("Product page read successfully. Check the extracted information before continuing.")
    st.caption(f"Source: {extraction.source_url} · Captured: {extraction.captured_at.isoformat()}")

    for warning in extraction.warnings:
        st.warning(warning)

    st.subheader("Product identity")
    identity_left, identity_right = st.columns([1.35, 1])

    selected_variant: ProductVariant | None = None
    selected_image: ProductImage | None = None
    with identity_left:
        st.markdown(f"### {extraction.product_name}")
        with st.expander("View product-name source"):
            st.write(extraction.product_name_source.wording)
            st.caption(
                f"{extraction.product_name_source.section} · "
                f"{extraction.product_name_source.method}"
            )

        if len(extraction.variants) > 1:
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
            st.markdown(f"**Variant / size:** {variant_label(selected_variant)}")
        else:
            st.caption("Variant / size was not available on the page.")

        if selected_variant and selected_variant.sku:
            st.caption(f"SKU: {selected_variant.sku}")

    with identity_right:
        if manual_image:
            st.image(manual_image, caption=extraction.manual_image_name, width="stretch")
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
            if selected_image:
                st.image(selected_image.url, caption=selected_image.label, width="stretch")
                st.caption(f"Image source: {selected_image.url}")
            else:
                st.info("Choose the exact product image rather than letting the system guess.")
        else:
            st.warning("No product image is available for verification.")

    st.subheader("Product facts")
    render_items(extraction.facts, "No product facts were found in the supplied source.")

    claims_column, evidence_column = st.columns(2)
    with claims_column:
        st.subheader("Claims")
        render_items(extraction.claims, "No benefit or efficacy statements were found.")
    with evidence_column:
        st.subheader("Evidence")
        render_items(extraction.evidence, "No study, testing, or qualifier statements were found.")

    commercial_items = list(extraction.commercial)
    if selected_variant:
        commercial_items = variant_commercial_items(
            selected_variant,
            extraction.source_url,
            extraction.captured_at,
        ) + commercial_items

    commercial_column, social_column = st.columns(2)
    with commercial_column:
        st.subheader("Commercial information")
        st.markdown(
            '<div class="reference-note">Reference only — not automatically available to ad copy.</div>',
            unsafe_allow_html=True,
        )
        render_items(commercial_items, "No price, MRP, discount, or offer was found.")
    with social_column:
        st.subheader("Social proof")
        st.markdown(
            '<div class="reference-note">Reference only — not automatically available to ad copy.</div>',
            unsafe_allow_html=True,
        )
        render_items(extraction.social_proof, "No rating or review count was found.")

    identity_ready = bool(extraction.product_name) and bool(manual_image or selected_image)
    variant_ready = len(extraction.variants) <= 1 or selected_variant is not None
    verified = st.checkbox(
        "I have checked the product identity, selected the correct variant and image, and reviewed the extracted information.",
        disabled=not (identity_ready and variant_ready),
        key=f"verified-{extraction.captured_at.isoformat()}",
    )
    if verified:
        st.success("Extraction verified. Eligibility is shown below; no ad is generated.", icon="✅")
        render_eligibility(assess_generation_eligibility(extraction, selected_variant))
    elif not (identity_ready and variant_ready):
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
            st.rerun()


def render_product_flow() -> None:
    st.markdown("### Add the product source")
    st.write("Enter a Minimalist India product page. Nothing extracted will be used until you verify it.")
    source_url = st.text_input(
        "Minimalist product URL",
        placeholder="https://beminimalist.co/products/...",
    )
    if st.button("Extract product information", type="primary"):
        st.session_state.product_extraction = None
        st.session_state.manual_product_image = None
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
        "Foundation behavior: choose a sample situation to verify status and export gating.",
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


st.title("Minimalist Ad Pre-flight")
st.markdown(
    '<div class="workflow">Product &nbsp;→&nbsp; Evidence &nbsp;→&nbsp; Creative '
    '&nbsp;→&nbsp; Review &nbsp;→&nbsp; Export</div>',
    unsafe_allow_html=True,
)

product_tab, review_tab = st.tabs(["Product extraction", "Review status examples"])
with product_tab:
    render_product_flow()
with review_tab:
    render_review_demo()

st.divider()
st.caption(
    "Scope: one 1080×1080 Meta Feed creative · Minimalist India · no publishing, accounts, or history"
)
