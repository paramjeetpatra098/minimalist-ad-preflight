# Minimalist Ad Pre-flight MVP

A Streamlit prototype for Minimalist India static Meta Feed ads. It has two paths:

- **Create & Review:** read and confirm a product page, classify evidence for generation, create one 1080×1080 ad using the selected product image, run pre-flight review, fix issues, and export an eligible creative.
- **Review Existing Ad:** upload an image, paste copy, or both; optionally add product evidence; review and revise. Copy-only review does not perform visual checks and does not export a creative asset.

The pre-flight result is PASS, PASS with warnings, REVIEW, or BLOCK within the rulebook's scope and available evidence. It is not legal approval or guaranteed Meta approval. REVIEW and BLOCK are non-exportable.

## Run locally

Use Python 3.12. Install `requirements.txt`, then add the server-side key to `.streamlit/secrets.toml`:

```toml
OPENAI_API_KEY = "your-key-here"
```

This file is ignored by Git. Do not put a real key in `.streamlit/secrets.toml.example` or `.env.example`. Start the app with `streamlit run streamlit_app.py`.

## Deploy on Streamlit Community Cloud

Push this repository to GitHub without rewriting its history. In Streamlit Community Cloud, create an app from the GitHub repository using branch `main` and entrypoint `streamlit_app.py`. Choose Python 3.12 in Advanced settings and add `OPENAI_API_KEY` in the cloud Secrets field using the TOML format above. Never upload or commit the local `.streamlit/secrets.toml` file.

The public app uses the server-side API key for generation and review, so monitor API usage and set an appropriate project budget. Product-page access can vary from the cloud environment; the app includes a manual product-information fallback.

## Test

Run `python -m unittest discover -s tests -q` for the automated suite. The frozen rulebook is in `rules/` and the scorer's golden cases and image fixtures are in `evals/`.
