from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel, Field


ALLOWED_HOSTS = {"beminimalist.co", "www.beminimalist.co"}
MAX_PAGE_BYTES = 4_000_000
PRODUCT_PATH = re.compile(r"^/(?:products/[^/]+|collections/[^/]+/products/[^/]+)/?$")


class ProductReadError(Exception):
    """Raised when a product page cannot be read safely or completely."""


class SourceReference(BaseModel):
    source_url: str
    captured_at: datetime
    section: str
    wording: str
    method: str


class ExtractedItem(BaseModel):
    label: str
    value: str
    source: SourceReference


class ProductImage(BaseModel):
    url: str
    label: str
    source: SourceReference
    variant_id: str | None = None


class ProductVariant(BaseModel):
    id: str
    title: str
    sku: str | None = None
    available: bool | None = None
    price_minor: int | None = None
    mrp_minor: int | None = None
    currency: str = "INR"
    image_url: str | None = None
    source: SourceReference


class ProductExtraction(BaseModel):
    source_url: str
    captured_at: datetime
    product_name: str
    product_name_source: SourceReference
    requested_variant_id: str | None = None
    manual_image_name: str | None = None
    variants: list[ProductVariant] = Field(default_factory=list)
    images: list[ProductImage] = Field(default_factory=list)
    facts: list[ExtractedItem] = Field(default_factory=list)
    claims: list[ExtractedItem] = Field(default_factory=list)
    evidence: list[ExtractedItem] = Field(default_factory=list)
    commercial: list[ExtractedItem] = Field(default_factory=list)
    social_proof: list[ExtractedItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def validate_product_url(raw_url: str) -> str:
    url = raw_url.strip()
    if not url:
        raise ProductReadError("Enter a Minimalist product URL.")

    if "://" not in url and not url.startswith("//"):
        url = f"https://{url}"

    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        port = parsed.port
    except ValueError as exc:
        raise ProductReadError("Use a valid Minimalist product URL.") from exc
    if parsed.scheme not in {"http", "https"} or host not in ALLOWED_HOSTS:
        raise ProductReadError("Use a product URL from beminimalist.co for the India market.")
    if parsed.username or parsed.password or port is not None:
        raise ProductReadError("Use a standard Minimalist product URL without credentials or a custom port.")
    if not PRODUCT_PATH.fullmatch(parsed.path):
        raise ProductReadError("The URL must point to a specific Minimalist product page.")
    return url


def read_product_url(raw_url: str, timeout_seconds: int = 20) -> ProductExtraction:
    requested_url = validate_product_url(raw_url)
    try:
        response = requests.get(
            requested_url,
            headers={"User-Agent": "Mozilla/5.0 MinimalistAdPreflight/0.1"},
            timeout=timeout_seconds,
            stream=True,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise ProductReadError("The product page could not be read.") from exc

    final_url = validate_product_url(response.url)
    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type.lower():
        raise ProductReadError("The URL did not return a readable product page.")

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_content(chunk_size=64 * 1024):
        total += len(chunk)
        if total > MAX_PAGE_BYTES:
            raise ProductReadError("The product page was too large to read safely.")
        chunks.append(chunk)

    encoding = response.encoding or "utf-8"
    html = b"".join(chunks).decode(encoding, errors="replace")
    return extract_product_html(html, final_url)


def extract_product_html(
    html: str,
    source_url: str,
    captured_at: datetime | None = None,
) -> ProductExtraction:
    captured = captured_at or datetime.now(UTC)
    soup = BeautifulSoup(html, "html.parser")
    product_json = _find_product_json(soup)

    heading = soup.select_one("h1.product__title") or soup.find("h1")
    product_name = _clean_text(product_json.get("name")) if product_json else ""
    name_method = "structured product data"
    if not product_name and heading:
        product_name = _clean_text(heading.get_text(" ", strip=True))
        name_method = "visible product heading"
    if not product_name:
        raise ProductReadError("The page did not contain a recognisable product name.")

    name_source = _source(
        source_url,
        captured,
        "Product identity",
        product_name,
        name_method,
    )

    variants = _extract_variants(soup, source_url, captured)
    images = _extract_images(soup, product_json, variants, source_url, captured)
    sections = _extract_toggle_sections(soup)

    facts: list[ExtractedItem] = []
    claims: list[ExtractedItem] = []
    evidence: list[ExtractedItem] = []
    commercial: list[ExtractedItem] = []
    social_proof: list[ExtractedItem] = []

    for pill in soup.select(".product-icons-list .pill__label"):
        value = _clean_text(pill.get_text(" ", strip=True))
        _append_item(facts, "Product specification", value, source_url, captured, "Product summary")

    for section_name in ("Ideal For", "How to Use"):
        for value in sections.get(section_name, []):
            _append_item(facts, section_name, value, source_url, captured, section_name)

    ingredients_section = _find_heading_container(soup, "Ingredients")
    if ingredients_section:
        for toggle in ingredients_section.select("toggle-tab"):
            title = _toggle_title(toggle)
            content = toggle.select_one("[data-js-content]")
            value = _clean_text(content.get_text(" ", strip=True)) if content else ""
            _append_item(facts, title or "Ingredients", value, source_url, captured, "Ingredients")

    summary_claims = []
    for subtitle in soup.select(".product-text .product__subtitle"):
        value = _clean_text(subtitle.get_text(" ", strip=True))
        if value and value not in summary_claims:
            summary_claims.append(value)
    for value in summary_claims:
        _append_item(claims, "Product-page claim", value, source_url, captured, "Product summary")

    for value in sections.get("What Makes It Potent?", []):
        _append_item(claims, "Benefit statement", value, source_url, captured, "What Makes It Potent?")
        if _looks_like_evidence(value):
            _append_item(evidence, "Evidence-linked statement", value, source_url, captured, "What Makes It Potent?")

    for section_name, values in sections.items():
        if _is_evidence_section(section_name):
            for value in values:
                _append_item(evidence, section_name, value, source_url, captured, section_name)

    for offer in soup.select(".offer-card"):
        code = offer.select_one(".offer-card__code")
        description = offer.select_one(".offer-card__desc")
        code_text = _clean_text(code.get_text(" ", strip=True)) if code else ""
        desc_text = _clean_text(description.get_text(" ", strip=True)) if description else ""
        value = " — ".join(part for part in (code_text, desc_text) if part)
        _append_item(commercial, "Offer", value, source_url, captured, "Offers")

    rating = product_json.get("aggregateRating", {}) if product_json else {}
    if isinstance(rating, dict):
        _append_item(
            social_proof,
            "Rating",
            _clean_text(rating.get("ratingValue")),
            source_url,
            captured,
            "Structured product data",
        )
        _append_item(
            social_proof,
            "Review count",
            _clean_text(rating.get("reviewCount")),
            source_url,
            captured,
            "Structured product data",
        )

    warnings: list[str] = []
    if not variants:
        warnings.append("No structured variant or size information was found.")
    if not images:
        warnings.append("No usable product image was found.")

    requested_variant = parse_qs(urlsplit(source_url).query).get("variant", [None])[0]
    return ProductExtraction(
        source_url=source_url,
        captured_at=captured,
        product_name=product_name,
        product_name_source=name_source,
        requested_variant_id=requested_variant,
        variants=variants,
        images=images,
        facts=_dedupe_items(facts),
        claims=_dedupe_items(claims),
        evidence=_dedupe_items(evidence),
        commercial=_dedupe_items(commercial),
        social_proof=_dedupe_items(social_proof),
        warnings=warnings,
    )


def manual_product_extraction(
    source_url: str,
    product_name: str,
    variant: str,
    facts_text: str,
    claims_text: str,
    evidence_text: str,
    commercial_text: str,
    social_proof_text: str,
    image_name: str | None,
) -> ProductExtraction:
    captured = datetime.now(UTC)
    clean_name = _clean_text(product_name)
    if not clean_name:
        raise ProductReadError("Enter the product name for the manual fallback.")

    source_label = source_url.strip() or "Manual product-page evidence"
    manual_source = _source(source_label, captured, "Manual entry", clean_name, "user-provided")
    variants: list[ProductVariant] = []
    clean_variant = _clean_text(variant)
    if clean_variant:
        variants.append(
            ProductVariant(
                id="manual-variant",
                title=clean_variant,
                source=_source(
                    source_label,
                    captured,
                    "Product identity",
                    clean_variant,
                    "user-provided",
                ),
            )
        )

    warnings = ["This extraction uses user-provided evidence because the product page was not read automatically."]
    if not image_name:
        warnings.append("No product image has been provided.")

    return ProductExtraction(
        source_url=source_label,
        captured_at=captured,
        product_name=clean_name,
        product_name_source=manual_source,
        manual_image_name=image_name,
        variants=variants,
        facts=_manual_items("Product facts", facts_text, source_label, captured),
        claims=_manual_items("Claim", claims_text, source_label, captured),
        evidence=_manual_items("Evidence / qualifier", evidence_text, source_label, captured),
        commercial=_manual_items("Commercial information", commercial_text, source_label, captured),
        social_proof=_manual_items("Social proof", social_proof_text, source_label, captured),
        warnings=warnings,
    )


def variant_commercial_items(
    variant: ProductVariant,
    source_url: str,
    captured_at: datetime,
) -> list[ExtractedItem]:
    items: list[ExtractedItem] = []
    if variant.price_minor is not None:
        value = _format_money(variant.price_minor, variant.currency)
        _append_item(
            items,
            "Price",
            value,
            source_url,
            captured_at,
            "Selected variant",
            variant.source.wording,
            method=variant.source.method,
        )
    if variant.mrp_minor is not None:
        value = _format_money(variant.mrp_minor, variant.currency)
        _append_item(
            items,
            "MRP",
            value,
            source_url,
            captured_at,
            "Selected variant",
            variant.source.wording,
            method=variant.source.method,
        )
    if (
        variant.price_minor is not None
        and variant.mrp_minor is not None
        and variant.mrp_minor > variant.price_minor
    ):
        discount = round((variant.mrp_minor - variant.price_minor) / variant.mrp_minor * 100)
        _append_item(
            items,
            "Calculated discount",
            f"{discount}%",
            source_url,
            captured_at,
            "Selected variant",
            f"Derived from page values: price {_format_money(variant.price_minor, variant.currency)} and MRP {_format_money(variant.mrp_minor, variant.currency)}.",
            method="derived from page values",
        )
    return items


def _find_product_json(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        for candidate in _walk_json(payload):
            kind = candidate.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if "Product" in kinds and candidate.get("name"):
                return candidate
    return {}


def _walk_json(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _extract_variants(
    soup: BeautifulSoup,
    source_url: str,
    captured: datetime,
) -> list[ProductVariant]:
    script = soup.select_one("script#nector-product-variants-data")
    if not script:
        return []
    try:
        payload = json.loads(script.string or script.get_text())
    except (json.JSONDecodeError, TypeError):
        return []

    variants: list[ProductVariant] = []
    for raw in payload if isinstance(payload, list) else []:
        variant_id = str(raw.get("id", "")).strip()
        title = _clean_text(raw.get("public_title") or raw.get("title"))
        if not variant_id or not title:
            continue
        image = raw.get("featured_image") or {}
        image_url = _absolute_url(image.get("src"), source_url) if isinstance(image, dict) else None
        wording_parts = [f"Variant {title}"]
        if raw.get("price") is not None:
            wording_parts.append(f"price {_format_money(int(raw['price']), 'INR')}")
        if raw.get("compare_at_price") is not None:
            wording_parts.append(f"MRP {_format_money(int(raw['compare_at_price']), 'INR')}")
        variants.append(
            ProductVariant(
                id=variant_id,
                title=title,
                sku=_clean_text(raw.get("sku")) or None,
                available=raw.get("available") if isinstance(raw.get("available"), bool) else None,
                price_minor=int(raw["price"]) if raw.get("price") is not None else None,
                mrp_minor=int(raw["compare_at_price"]) if raw.get("compare_at_price") is not None else None,
                image_url=image_url,
                source=_source(
                    source_url,
                    captured,
                    "Product variants",
                    "; ".join(wording_parts),
                    "embedded product data",
                ),
            )
        )
    return variants


def _extract_images(
    soup: BeautifulSoup,
    product_json: dict[str, Any],
    variants: list[ProductVariant],
    source_url: str,
    captured: datetime,
) -> list[ProductImage]:
    candidates: list[tuple[str | None, str, str | None, str]] = []
    for variant in variants:
        if variant.image_url:
            candidates.append((variant.image_url, f"{variant.title} product image", variant.id, "variant product data"))

    raw_image = product_json.get("image") if product_json else None
    if isinstance(raw_image, dict):
        raw_image = raw_image.get("url") or raw_image.get("image")
    if isinstance(raw_image, list):
        for index, image in enumerate(raw_image, 1):
            candidates.append((str(image), f"Product image {index}", None, "structured product data"))
    elif raw_image:
        candidates.append((str(raw_image), "Primary product image", None, "structured product data"))

    for index, zoom in enumerate(soup.select("product-image-zoom[data-image]"), 1):
        candidates.append((zoom.get("data-image"), f"Gallery image {index}", None, "product gallery"))

    images: list[ProductImage] = []
    seen: set[str] = set()
    for raw_url, label, variant_id, method in candidates:
        url = _absolute_url(raw_url, source_url)
        if not url or url in seen:
            continue
        seen.add(url)
        images.append(
            ProductImage(
                url=url,
                label=label,
                variant_id=variant_id,
                source=_source(source_url, captured, "Product image", url, method),
            )
        )
    return images[:12]


def _extract_toggle_sections(soup: BeautifulSoup) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {}
    for toggle in soup.select("toggle-tab"):
        title = _toggle_title(toggle)
        content = toggle.select_one("[data-js-content]")
        if not title or not content:
            continue
        values = [_clean_text(node.get_text(" ", strip=True)) for node in content.select("li, p")]
        values = [value for value in values if value]
        if not values:
            value = _clean_text(content.get_text(" ", strip=True))
            values = [value] if value else []
        sections.setdefault(title, []).extend(values)
    return sections


def _toggle_title(toggle: Tag) -> str:
    title = toggle.select_one(".toggle__title")
    if not title:
        return ""
    return _clean_text(title.get_text(" ", strip=True))


def _find_heading_container(soup: BeautifulSoup, heading_text: str) -> Tag | None:
    for heading in soup.find_all(["h2", "h3"]):
        if _clean_text(heading.get_text(" ", strip=True)).casefold() == heading_text.casefold():
            return heading.find_parent("div", class_="shopify-section") or heading.parent
    return None


def _is_evidence_section(title: str) -> bool:
    lowered = title.casefold()
    return any(term in lowered for term in ("clinical result", "consumer stud", "test result"))


def _looks_like_evidence(value: str) -> bool:
    lowered = value.casefold()
    return any(term in lowered for term in ("clinically", "tested", "study", "subjects"))


def _append_item(
    destination: list[ExtractedItem],
    label: str,
    value: str,
    source_url: str,
    captured: datetime,
    section: str,
    wording: str | None = None,
    method: str = "visible page text",
) -> None:
    clean_value = _clean_text(value)
    if not clean_value:
        return
    destination.append(
        ExtractedItem(
            label=label,
            value=clean_value,
            source=_source(source_url, captured, section, wording or clean_value, method),
        )
    )


def _manual_items(
    label: str,
    text: str,
    source_url: str,
    captured: datetime,
) -> list[ExtractedItem]:
    values = [_clean_text(line) for line in text.splitlines() if _clean_text(line)]
    return [
        ExtractedItem(
            label=label,
            value=value,
            source=_source(source_url, captured, "Manual entry", value, "user-provided"),
        )
        for value in values
    ]


def _source(
    source_url: str,
    captured: datetime,
    section: str,
    wording: str,
    method: str,
) -> SourceReference:
    return SourceReference(
        source_url=source_url,
        captured_at=captured,
        section=section,
        wording=_clean_text(wording),
        method=method,
    )


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _absolute_url(value: Any, source_url: str) -> str | None:
    clean = _clean_text(value)
    if not clean:
        return None
    if clean.startswith("//"):
        return f"https:{clean}"
    return urljoin(source_url, clean)


def _dedupe_items(items: list[ExtractedItem]) -> list[ExtractedItem]:
    result: list[ExtractedItem] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.label.casefold(), item.value.casefold())
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _format_money(value_minor: int, currency: str) -> str:
    major = value_minor / 100
    amount = f"{major:,.2f}".rstrip("0").rstrip(".")
    return f"₹{amount}" if currency == "INR" else f"{currency} {amount}"
