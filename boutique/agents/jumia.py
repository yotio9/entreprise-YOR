import re
import unicodedata
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from django.conf import settings


JUMIA_ROOTS = (
    "jumia.ci", "jumia.sn", "jumia.com.ng", "jumia.co.ke", "jumia.com.gh",
    "jumia.co.ug", "jumia.com.eg", "jumia.com.ma", "jumia.com.tn",
    "jumia.com.dz", "jumia.com",
)


class HumanReviewRequired(Exception):
    pass


class RetryableInspectionError(Exception):
    pass


def jumia_root(hostname):
    host = (hostname or "").lower().rstrip(".")
    for root in JUMIA_ROOTS:
        if host == root or host.endswith("." + root):
            return root
    return None


def validate_jumia_url(url):
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme == "https"
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
        and jumia_root(hostname) is not None
    )


def normalize_words(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.findall(r"[a-z0-9]+", value.casefold())


def parse_price(value):
    if not value:
        return None
    text = str(value)
    start = next((index for index, char in enumerate(text) if char.isdigit()), None)
    if start is None:
        return None
    raw_chars = []
    for char in text[start:]:
        if char.isdigit() or char in " .,'" or ord(char) in (160, 8217):
            raw_chars.append(char)
        else:
            break
    raw = "".join(raw_chars).replace(" ", "").replace(chr(160), "").replace("'", "").replace(chr(8217), "")
    separators = [index for index, char in enumerate(raw) if char in ".,"]

    def digits(value):
        return "".join(char for char in value if char.isdigit())

    if separators:
        last = separators[-1]
        fraction = digits(raw[last + 1:])
        if len(fraction) == 2:
            whole = digits(raw[:last])
            number = whole + "." + fraction
        else:
            number = digits(raw)
    else:
        number = digits(raw)
    try:
        return Decimal(number) if number else None
    except InvalidOperation:
        return None


def listing_currency(value):
    text = (value or "").upper().replace(" ", "")
    if "FCFA" in text or "CFA" in text or "XOF" in text:
        return "XOF"
    if "NGN" in text:
        return "NGN"
    if "KES" in text or "KSH" in text:
        return "KES"
    return ""


def assess_listing(task, listing):
    issues = []
    expected = set(normalize_words(task.expected_product_name))
    observed = set(normalize_words(listing.get("title", "")))
    overlap = len(expected & observed) / max(1, len(expected))
    if not expected or overlap < 0.8:
        issues.append("product_mismatch")

    if listing.get("challenge"):
        issues.append("supplier_challenge")
    if listing.get("availability") != "available":
        issues.append("availability_unverified")

    if task.expected_size:
        labels = " ".join(listing.get("variant_labels", []))
        size = " ".join(normalize_words(task.expected_size))
        if not size or size not in " ".join(normalize_words(labels)):
            issues.append("size_unverified")
    if task.expected_color:
        labels = set(normalize_words(" ".join(listing.get("variant_labels", []))))
        color = set(normalize_words(task.expected_color))
        if not color or not color.issubset(labels):
            issues.append("color_unverified")

    price = parse_price(listing.get("price"))
    if price is None or not listing.get("currency"):
        issues.append("price_or_currency_unverified")
    if listing.get("currency") != settings.JUMIA_EXPECTED_CURRENCY:
        issues.append("currency_mismatch")
    if task.price_limit is None:
        issues.append("price_limit_missing")
    elif price is not None and price > task.price_limit:
        issues.append("price_limit_exceeded")

    return {
        "prepared": not issues,
        "issues": issues,
        "title_match_ratio": round(overlap, 3),
        "price_within_limit": bool(task.price_limit is not None and price is not None and price <= task.price_limit),
    }


def inspect_jumia_product(task):
    if not validate_jumia_url(task.source_url):
        raise HumanReviewRequired("Le lien fournisseur n'est pas un lien HTTPS Jumia autorise.")

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RetryableInspectionError("Playwright n'est pas installe.") from exc

    timeout_ms = settings.JUMIA_AGENT_TIMEOUT_SECONDS * 1000
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=settings.JUMIA_BROWSER_HEADLESS)
            context = browser.new_context(locale="fr-CI")
            page = context.new_page()
            page.set_default_timeout(timeout_ms)

            def restrict_navigation(route):
                target = urlsplit(route.request.url)
                if target.scheme == "https" and jumia_root(target.hostname):
                    route.continue_()
                else:
                    route.abort()

            page.route("**/*", restrict_navigation)
            response = page.goto(task.source_url, wait_until="domcontentloaded", timeout=timeout_ms)
            if response is None or response.status >= 500:
                raise RetryableInspectionError("La page fournisseur ne repond pas.")
            final_host = urlsplit(page.url).hostname
            if not jumia_root(final_host):
                raise HumanReviewRequired("La page a redirige hors du domaine Jumia autorise.")

            title = ""
            try:
                title = page.locator("h1").first.inner_text(timeout=5000).strip()[:300]
            except Exception:
                title = (page.title() or "")[:300]
            body = page.locator("body").inner_text(timeout=timeout_ms)[:30000]
            lower = body.casefold()
            challenge = any(term in lower for term in (
                "captcha", "verify you are human", "verify that you are human",
                "verifiez que vous etes humain", "confirmez que vous etes humain",
                "unusual traffic", "robot check",
            ))
            if any(term in lower for term in ("se connecter pour continuer", "sign in to continue", "connexion requise")):
                challenge = True

            price_text = ""
            for selector in (
                '[data-test="product-price"]', '[data-testid="product-price"]',
                '[itemprop="price"]', 'meta[property="product:price:amount"]',
                '.-fs24', '[class*="price"]',
            ):
                try:
                    locator = page.locator(selector).first
                    if locator.count():
                        price_text = (
                            locator.get_attribute("content")
                            or locator.get_attribute("data-price")
                            or locator.inner_text(timeout=1500)
                            or ""
                        )
                        if parse_price(price_text) is not None:
                            break
                except Exception:
                    continue
            price = parse_price(price_text)
            currency = listing_currency(price_text) or listing_currency(body)

            if any(term in lower for term in ("rupture de stock", "indisponible", "out of stock", "sold out")):
                availability = "unavailable"
            elif any(term in lower for term in ("ajouter au panier", "add to cart", "buy now", "acheter maintenant")):
                availability = "available"
            else:
                availability = "unknown"

            variant_labels = []
            for selector in ("button", "option", "label", '[role="radio"]', "[aria-label]"):
                try:
                    variant_labels.extend(page.locator(selector).all_inner_texts()[:80])
                except Exception:
                    continue
            variant_labels = [str(label).strip()[:120] for label in variant_labels if str(label).strip()]
            result = {
                "title": title,
                "price": str(price) if price is not None else None,
                "currency": currency,
                "availability": availability,
                "variant_labels": variant_labels[:120],
                "challenge": challenge,
                "observed_url_host": final_host,
            }
            context.close()
            browser.close()
            return result
    except HumanReviewRequired:
        raise
    except RetryableInspectionError:
        raise
    except PlaywrightTimeoutError as exc:
        raise RetryableInspectionError("Delai de reponse Jumia depasse.") from exc
    except Exception as exc:
        raise RetryableInspectionError("Inspection Jumia indisponible.") from exc
