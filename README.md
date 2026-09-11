# Minimalist Ad Pre-flight MVP

This first build contains only the application shell and deterministic review-gating behavior.

## Run locally

1. Create and activate a Python virtual environment.
2. Install `requirements.txt`.
3. Run `streamlit run streamlit_app.py`.

## Test

Run `python -m unittest discover -s tests -v`.

## Included in this step

- PASS, PASS with warnings, REVIEW, BLOCK, and Not Assessable findings
- Deterministic overall-status calculation
- Export eligibility derived from the overall status
- Five sample situations for user-facing verification

Product extraction, evidence capture, generation, creative rendering, uploaded-image review, and real export are intentionally deferred.
