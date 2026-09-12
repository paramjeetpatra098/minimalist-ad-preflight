# Minimalist Ad Pre-flight MVP

A small Streamlit app that creates one Minimalist India Meta Feed ad from verified product evidence, or checks an existing ad before it leaves the tool. [Open the deployed app](https://minimalist-ad-preflight.streamlit.app/).

## Two workflows

- **Create & Review:** enter a Minimalist product URL → confirm the product, variant, image, and extracted evidence → generate one 1080×1080 creative → review → fix and re-review if needed → export.
- **Review Existing Ad:** upload an image, paste copy, or both → optionally add product evidence → review → fix and re-review if needed → export an eligible image creative.

## Run locally (quick setup)

Use Python 3.12. From the repository root:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

Edit `.streamlit/secrets.toml` to contain:

```toml
OPENAI_API_KEY = "your-actual-key"
```

The real secrets file is Git-ignored; never put a key in the example file or commit it. Run:

```bash
streamlit run streamlit_app.py
```

## Tests and review standard

Run `python -m unittest discover -s tests -q`. The versioned rulebook is in `rules/`; scorer golden cases and image fixtures are in `evals/`. Human-readable snapshots of the app's active prompts are in `prompts/`. The Python code remains the runtime source of truth.

## Scope and limits

- Minimalist India, one static 1080×1080 Meta Feed creative. No direct Meta publishing, login, campaign history, or analytics.
- Product URL is the normal starting point; the app asks for ambiguous variant/image selection and offers manual text/image fallback if the page cannot be read. Price, offers, ratings, and review counts are reference-only, not automatic ad copy.
- PASS means pre-flight clear **within the encoded rules and available evidence**, not legal approval or guaranteed Meta approval. Unresolved REVIEW or BLOCK prevents creative export; WARN alone does not.
- Copy-only review checks the pasted text but performs **no visual checks** and offers **no creative export**. Add the final image to review and export a creative.
- The public deployment uses a server-side OpenAI key. Anyone with access can trigger API usage, so monitor usage and set an appropriate project budget.
