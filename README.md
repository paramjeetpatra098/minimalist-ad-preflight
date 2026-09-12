# Minimalist Ad Pre-flight MVP

This prototype currently contains product-page extraction, generation eligibility, and the deterministic review-gating foundation.

## Run locally

1. Create and activate a Python virtual environment.
2. Install `requirements.txt`.
3. Add your server-side key to `.streamlit/secrets.toml`:
   ```toml
   OPENAI_API_KEY = "your-real-api-key"
   ```
   Keep this file private; it is ignored by Git. Restart Streamlit after creating or changing it.
4. Run `streamlit run streamlit_app.py`.

## Test

Run `python -m unittest discover -s tests -v`.

## Included

- PASS, PASS with warnings, REVIEW, BLOCK, and Not Assessable findings
- Deterministic overall-status calculation
- Export eligibility derived from the overall status
- Five sample situations for user-facing verification
- Minimalist India product-URL validation and extraction
- Direct and collection-scoped Minimalist product URLs, with scheme-less www links normalized to HTTPS
- Product identity, facts, claims, evidence, commercial, and social-proof groups
- Variant and product-image selection when the page is ambiguous
- Source wording and capture time for extracted information
- Grouped manual fallback for unreadable product pages
- Deterministic pre-generation classification into Eligible, Eligible but review required,
  Ineligible, and Not Assessable
- Reasons, required qualifiers, and source/evidence provenance for every eligibility decision
- Commercial information and social proof kept outside the generation-eligible set
- One evidence-bounded ad content plan with citations for every factual element
- One in-memory 1080×1080 preview using the selected real product image
- Deterministic rejection of unknown evidence IDs, invented numbers, unsupported wording,
  or dropped required qualifiers

Final creative review, correction, uploaded-creative review, and export are intentionally deferred.
