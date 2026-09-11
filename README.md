# Minimalist Ad Pre-flight MVP

This prototype currently contains product-page extraction, generation eligibility, and the deterministic review-gating foundation.

## Run locally

1. Create and activate a Python virtual environment.
2. Install `requirements.txt`.
3. Run `streamlit run streamlit_app.py`.

## Test

Run `python -m unittest discover -s tests -v`.

## Included

- PASS, PASS with warnings, REVIEW, BLOCK, and Not Assessable findings
- Deterministic overall-status calculation
- Export eligibility derived from the overall status
- Five sample situations for user-facing verification
- Minimalist India product-URL validation and extraction
- Product identity, facts, claims, evidence, commercial, and social-proof groups
- Variant and product-image selection when the page is ambiguous
- Source wording and capture time for extracted information
- Grouped manual fallback for unreadable product pages
- Deterministic pre-generation classification into Eligible, Eligible but review required,
  Ineligible, and Not Assessable
- Reasons, required qualifiers, and source/evidence provenance for every eligibility decision
- Commercial information and social proof kept outside the generation-eligible set

Ad generation, creative rendering, AI review, uploaded-creative review, and real export are intentionally deferred.
