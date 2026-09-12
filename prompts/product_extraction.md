# Product extraction

**There is no LLM extraction prompt.** Product reading and field extraction are deterministic code in `minimalist_mvp/product.py`; generation eligibility is deterministic code in `minimalist_mvp/eligibility.py`.

Current behavior: validate a Minimalist India product URL, read the page, extract product identity, facts, claims, evidence, commercial information, and social proof with source wording and capture time. The user confirms ambiguous variants and images. If the page cannot be read, the UI accepts pasted source text and a product image. Missing fields are not invented.

This document records the extraction intent; it is not loaded by the app.
