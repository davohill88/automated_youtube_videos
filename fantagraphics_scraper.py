from __future__ import annotations
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from dateutil.relativedelta import relativedelta

FANTAGRAPHICS_BASE = "https://www.fantagraphics.com"
PRODUCTS_ENDPOINT = "/collections/coming-soon/products.json"
COMICRELEASES_BASE = "https://comicreleases.com"
DATA_DIR = Path("data")
OUTPUT_FILE = DATA_DIR / "fantagraphics_upcoming.json"


def fetch_fantagraphics_products() -> list[dict]:
    products = []
    page = 1
    while True:
        url = f"{FANTAGRAPHICS_BASE}{PRODUCTS_ENDPOINT}?page={page}&limit=250"
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"Error fetching page {page}: {e}", file=sys.stderr)
            break
        page_products = data.get("products", [])
        if not page_products:
            break
        for p in page_products:
            image_url = None
            if p.get("images"):
                image_url = p["images"][0]["src"]
            price = None
            if p.get("variants"):
                price = p["variants"][0].get("price")
            description = ""
            if p.get("body_html"):
                soup = BeautifulSoup(p["body_html"], "html.parser")
                description = soup.get_text(separator=" ").strip()
            handle = p.get("handle", "")
            products.append({
                "title": p.get("title", ""),
                "description": description,
                "cover_image_url": image_url,
                "price": price,
                "product_url": f"{FANTAGRAPHICS_BASE}/products/{handle}",
                "handle": handle,
            })
        page += 1
    return products


def build_comicreleases_url(year: int, month: int) -> str:
    month_name = datetime(year, month, 1).strftime("%B").lower()
    return f"{COMICRELEASES_BASE}/{year}/{month:02d}/fantagraphics-{month_name}-{year}-solicitations/"


def check_url_exists(url: str) -> bool:
    try:
        resp = requests.head(url, timeout=10, allow_redirects=True)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def fetch_comicreleases_page(url: str):
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except requests.RequestException as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return None


def normalize_title(title: str) -> str:
    return re.sub(r"[^a-z0-9\s]", "", title.lower()).strip()


def extract_book_metadata(soup) -> dict:
    book_data = {}

    def parse_text_block(title_text: str, full_text: str) -> None:
        isbn_match = re.search(r"ISBN[:\s\-]*([0-9X\-]{10,17})", full_text, re.I)
        isbn = isbn_match.group(1).replace("-", "").strip() if isbn_match else None
        date_match = re.search(
            r"\b(\w+ \d{1,2},?\s*\d{4}|\d{1,2}/\d{1,2}/\d{4})\b", full_text
        )
        release_date = date_match.group(0) if date_match else None
        creators = []
        for pattern in [
            r"(?:by|written by|art by|illustrated by|creator)[:\s]+([^\n,;.]{2,60})",
            r"(?:writer|artist)[:\s]+([^\n,;.]{2,60})",
        ]:
            for m in re.finditer(pattern, full_text, re.I):
                name = m.group(1).strip()
                if name and len(name) < 80:
                    creators.append(name)
        norm = normalize_title(title_text)
        if norm:
            book_data[norm] = {
                "creators": list(dict.fromkeys(creators)),
                "isbn": isbn,
                "release_date": release_date,
            }

    entries = soup.find_all(["article", "div"], class_=re.compile(r"entry|post|product", re.I))
    for entry in entries:
        title_el = entry.find(["h2", "h3", "h4"])
        if title_el:
            parse_text_block(title_el.get_text(strip=True), entry.get_text(" ", strip=True))
    if not book_data:
        for heading in soup.find_all(["h2", "h3"]):
            title_text = heading.get_text(strip=True)
            sibling_texts = []
            sib = heading.next_sibling
            while sib and getattr(sib, "name", None) not in ("h2", "h3"):
                if hasattr(sib, "get_text"):
                    sibling_texts.append(sib.get_text(" ", strip=True))
                sib = sib.next_sibling
            parse_text_block(title_text, " ".join(sibling_texts))
    return book_data


def enrich_with_comicreleases(products: list[dict]) -> list[dict]:
    now = datetime.now()
    months_to_check = [
        (now.year, now.month),
        ((now - relativedelta(months=1)).year, (now - relativedelta(months=1)).month),
    ]
    combined = {}
    for year, month in months_to_check:
        url = build_comicreleases_url(year, month)
        print(f"Checking {url}")
        if check_url_exists(url):
            soup = fetch_comicreleases_page(url)
            if soup:
                data = extract_book_metadata(soup)
                combined.update(data)
                print(f"  Found {len(data)} entries")
        else:
            print("  Not found (skipping)")
    for product in products:
        norm = normalize_title(product["title"])
        match = combined.get(norm)
        if not match:
            for cr_norm, cr_data in combined.items():
                if cr_norm and (cr_norm in norm or norm in cr_norm):
                    match = cr_data
                    break
        if match:
            product["creators"] = match.get("creators", [])
            product["isbn"] = match.get("isbn")
            product["release_date"] = match.get("release_date")
        else:
            product.setdefault("creators", [])
            product.setdefault("isbn", None)
            product.setdefault("release_date", None)
    return products


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape Fantagraphics upcoming releases.")
    parser.add_argument(
        "--enrich",
        action="store_true",
        help="Cross-reference comicreleases.com for creators, ISBNs, and release dates",
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUT_FILE),
        help=f"Output JSON path (default: {OUTPUT_FILE})",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print("Fetching Fantagraphics coming-soon products...")
    products = fetch_fantagraphics_products()
    print(f"Found {len(products)} products")

    if args.enrich:
        print("Enriching with comicreleases.com data...")
        products = enrich_with_comicreleases(products)

    payload = {"scraped_at": datetime.utcnow().isoformat() + "Z", "products": products}
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(products)} products to {output_path}")


if __name__ == "__main__":
    main()
