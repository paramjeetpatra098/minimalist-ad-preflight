import streamlit as st

from minimalist_mvp.demo_scenarios import SCENARIOS
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


st.title("Minimalist Ad Pre-flight")
st.markdown(
    '<div class="workflow">Product &nbsp;→&nbsp; Evidence &nbsp;→&nbsp; Creative '
    '&nbsp;→&nbsp; Review &nbsp;→&nbsp; Export</div>',
    unsafe_allow_html=True,
)

st.info(
    "Foundation preview: choose a sample situation to verify review status and export gating. "
    "Product extraction, generation and image review are intentionally not included yet.",
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
        st.write("A later build step will add the creative and provenance bundle.")
    else:
        st.error("Locked", icon="🔒")
        st.write("Acknowledging an issue does not unlock export. It must be resolved and re-reviewed.")

st.divider()
st.caption(
    "Scope: one 1080×1080 Meta Feed creative · Minimalist India · no publishing, accounts, or history"
)
