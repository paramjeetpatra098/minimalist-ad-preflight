import unittest
from datetime import UTC, datetime

from pydantic import ValidationError, create_model

from minimalist_mvp.eligibility import assess_generation_eligibility
from minimalist_mvp.product import (
    ProductReadError,
    extract_product_html,
    restore_product_extraction,
    validate_product_url,
    variant_commercial_items,
)


PRODUCT_HTML = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Test Serum 2%",
 "image":{"url":"https://beminimalist.co/product.png"},
 "aggregateRating":{"ratingValue":"4.8","reviewCount":"120"}}
</script></head><body>
<div class="product-text">
  <h1 class="product__title">Test Serum 2%</h1>
  <span class="product__subtitle"><p><strong>Reduces excess oil</strong></p></span>
  <div class="product-icons-list"><span class="pill__label">pH: 4.0 - 5.0</span></div>
</div>
<script id="nector-product-variants-data" type="application/json">
[{"id":1,"public_title":"30ml","sku":"TEST30","available":true,
  "price":49900,"compare_at_price":59900,
  "featured_image":{"src":"//beminimalist.co/test-30.png"}},
 {"id":2,"public_title":"60ml","sku":"TEST60","available":true,
  "price":79900,"compare_at_price":null,
  "featured_image":{"src":"//beminimalist.co/test-60.png"}}]
</script>
<toggle-tab><span class="toggle__title">What Makes It Potent?</span>
<div data-js-content><ul><li>2% active helps reduce excess oil</li></ul></div></toggle-tab>
<toggle-tab><span class="toggle__title">Ideal For</span>
<div data-js-content><p>Skin type: Oily</p><p>Concerns: Excess oil</p></div></toggle-tab>
<toggle-tab><span class="toggle__title">How to Use</span>
<div data-js-content><p>Apply two drops at night.</p></div></toggle-tab>
<toggle-tab><span class="toggle__title">Consumer Studies</span>
<div data-js-content><p>90% of subjects reported improvement after 4 weeks.</p>
<p>Based on a 20-subject consumer study.</p></div></toggle-tab>
<div class="offer-card"><span class="offer-card__code">TEST10</span>
<div class="offer-card__desc">10% off above ₹999</div></div>
<div class="shopify-section"><h2>Ingredients</h2><toggle-tab>
<div class="toggle__title">All Ingredients</div>
<div data-js-content><p>Water, Test Active</p></div></toggle-tab></div>
</body></html>
"""


class ProductUrlTests(unittest.TestCase):
    def test_accepts_india_product_url(self) -> None:
        url = "https://beminimalist.co/products/test-serum?variant=1"
        self.assertEqual(validate_product_url(url), url)

    def test_accepts_collection_scoped_product_urls(self) -> None:
        urls = (
            "https://beminimalist.co/collections/skin/products/salicylic-lha-2-cleanser",
            "https://beminimalist.co/collections/bath-body/products/nonapeptide-aha-06-underarm-roll-on",
            "https://beminimalist.co/collections/vitamin-c/products/vitamin-c-ethyl-ascorbic-acid-10-acetyl-glucosamine-1",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(validate_product_url(url), url)

    def test_accepts_www_product_url_without_scheme(self) -> None:
        self.assertEqual(
            validate_product_url("www.beminimalist.co/products/salicylic-acid-2"),
            "https://www.beminimalist.co/products/salicylic-acid-2",
        )
        self.assertEqual(
            validate_product_url(
                "www.beminimalist.co/products/vitamin-c-ethyl-ascorbic-acid-10-acetyl-glucosamine-1"
            ),
            "https://www.beminimalist.co/products/vitamin-c-ethyl-ascorbic-acid-10-acetyl-glucosamine-1",
        )
        self.assertEqual(
            validate_product_url("http://www.beminimalist.co/products/salicylic-acid-2"),
            "http://www.beminimalist.co/products/salicylic-acid-2",
        )

    def test_rejects_non_product_or_non_india_url(self) -> None:
        with self.assertRaises(ProductReadError):
            validate_product_url("https://global.beminimalist.co/products/test-serum")
        with self.assertRaises(ProductReadError):
            validate_product_url("https://beminimalist.co/collections/serums")
        with self.assertRaises(ProductReadError):
            validate_product_url("https://beminimalist.co/collections/skin/products/")
        with self.assertRaises(ProductReadError):
            validate_product_url("https://beminimalist.co/collections/skin/products/test/extra")
        with self.assertRaises(ProductReadError):
            validate_product_url("www.beminimalist.co.evil.example/products/test")


class ProductExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.captured = datetime(2026, 9, 11, 10, 0, tzinfo=UTC)
        self.result = extract_product_html(
            PRODUCT_HTML,
            "https://beminimalist.co/products/test-serum?variant=1",
            self.captured,
        )

    def test_extracts_identity_and_requires_variant_context(self) -> None:
        self.assertEqual(self.result.product_name, "Test Serum 2%")
        self.assertEqual(self.result.requested_variant_id, "1")
        self.assertEqual([variant.title for variant in self.result.variants], ["30ml", "60ml"])
        self.assertGreaterEqual(len(self.result.images), 2)

    def test_stale_same_named_source_model_is_rebuilt_before_eligibility(self) -> None:
        legacy_source_model = create_model(
            "SourceReference",
            source_url=(str, ...), captured_at=(datetime, ...),
            section=(str, ...), wording=(str, ...), method=(str, ...),
        )
        legacy_source = legacy_source_model.model_validate(
            self.result.product_name_source.model_dump()
        )
        stale_extraction = self.result.model_copy(
            update={"product_name_source": legacy_source}
        )
        with self.assertRaisesRegex(ValidationError, "sources.0"):
            assess_generation_eligibility(stale_extraction, stale_extraction.variants[0])

        current = restore_product_extraction(stale_extraction)
        selected_variant = current.variants[0]
        selected_image = next(image for image in current.images
                              if image.variant_id == selected_variant.id)
        assessment = assess_generation_eligibility(current, selected_variant)
        self.assertEqual(assessment.decisions[0].sources[0].wording,
                         self.result.product_name_source.wording)
        self.assertEqual(assessment.decisions[0].sources[0].captured_at, self.captured)
        self.assertEqual(selected_image.variant_id, selected_variant.id)

    def test_extracts_requested_groups_without_inventing(self) -> None:
        self.assertIn("All Ingredients", [item.label for item in self.result.facts])
        self.assertIn("Product-page claim", [item.label for item in self.result.claims])
        self.assertEqual(len(self.result.evidence), 2)
        self.assertEqual(self.result.commercial[0].value, "TEST10 — 10% off above ₹999")
        self.assertEqual(
            [(item.label, item.value) for item in self.result.social_proof],
            [("Rating", "4.8"), ("Review count", "120")],
        )

    def test_every_extracted_item_has_source_wording(self) -> None:
        groups = (
            self.result.facts,
            self.result.claims,
            self.result.evidence,
            self.result.commercial,
            self.result.social_proof,
        )
        for group in groups:
            for item in group:
                self.assertTrue(item.source.wording)
                self.assertEqual(item.source.captured_at, self.captured)

    def test_variant_commercial_information_is_variant_specific(self) -> None:
        items = variant_commercial_items(
            self.result.variants[0], self.result.source_url, self.result.captured_at
        )
        self.assertEqual(
            [(item.label, item.value) for item in items],
            [("Price", "₹499"), ("MRP", "₹599"), ("Calculated discount", "17%")],
        )


if __name__ == "__main__":
    unittest.main()
