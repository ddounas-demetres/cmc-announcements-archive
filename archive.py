"""
Ανιχνεύει και αρχειοθετεί τις ανακοινώσεις/νέα του cmc.panteion.gr σε ένα
τοπικό αρχείο CSV (archive.csv), προσθέτοντας μόνο ό,τι δεν έχει ήδη
καταγραφεί. Σχεδιασμένο να τρέχει περιοδικά μέσω GitHub Actions.
"""

import csv
import os
import re
import sys
from datetime import date
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

GREEK_MONTHS = {
    "ιανουαριου": 1, "ιανουαριος": 1,
    "φεβρουαριου": 2, "φεβρουαριος": 2,
    "μαρτιου": 3, "μαρτιος": 3,
    "απριλιου": 4, "απριλιος": 4,
    "μαιου": 5, "μαιος": 5,
    "ιουνιου": 6, "ιουνιος": 6,
    "ιουλιου": 7, "ιουλιος": 7,
    "αυγουστου": 8, "αυγουστος": 8,
    "σεπτεμβριου": 9, "σεπτεμβριος": 9,
    "οκτωβριου": 10, "οκτωβριος": 10,
    "νοεμβριου": 11, "νοεμβριος": 11,
    "δεκεμβριου": 12, "δεκεμβριος": 12,
}
ACCENTS = str.maketrans("άέήίόύώϊΐϋΰ", "αεηιουωιιυυ")
DATE_RE = re.compile(r"(\d{1,2})\s+(\D+?)\s+(\d{4})")

SOURCES = [
    {
        "label": "Ανακοινώσεις Προπτυχιακού",
        "url": "https://cmc.panteion.gr/news-and-announcements/proptyxiakoy",
        "prefix": "/news-and-announcements/proptyxiakoy/",
    },
    {
        "label": "Ανακοινώσεις Μεταπτυχιακού",
        "url": "https://cmc.panteion.gr/news-and-announcements/metaptyxiakoy",
        "prefix": "/news-and-announcements/metaptyxiakoy/",
    },
    {
        "label": "Νέα του Τμήματος",
        "url": "https://cmc.panteion.gr/nea",
        "prefix": "/nea/",
    },
]

ARCHIVE_PATH = "archive.csv"
FIELDNAMES = ["category", "published_date", "title", "url", "archived_on"]


def normalize_month(token):
    token = token.strip().lower().translate(ACCENTS)
    return GREEK_MONTHS.get(token)


def parse_greek_date(text):
    m = DATE_RE.search(text)
    if not m:
        return None
    day = int(m.group(1))
    month = normalize_month(m.group(2))
    year = int(m.group(3))
    if not month:
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def looks_like_content_link(a, prefix):
    href = a.get("href", "")
    if not href or href.startswith("#") or href.startswith("javascript:"):
        return False
    text = a.get_text(strip=True)
    if len(text) < 8 or text.isdigit():
        return False
    path = urlparse(href).path
    return path.startswith(prefix) and path.rstrip("/") != prefix.rstrip("/")


def fetch_source(source):
    resp = requests.get(
        source["url"],
        headers={"User-Agent": "Mozilla/5.0 (CMCArchiveBot; +github actions)"},
        timeout=30,
    )
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    container = None
    for selector in ["#sp-component", "main", "article", "body"]:
        candidate = soup.select_one(selector)
        if candidate and candidate.find("a", href=True):
            container = candidate
            break
    if container is None:
        container = soup

    results = []
    seen_urls = set()

    # Layout 1: table-based category list (has real dates)
    table = container.find("table")
    if table:
        for row in table.find_all("tr"):
            cells = row.find_all("td", recursive=False)
            if not cells:
                continue
            anchor = next((c.find("a", href=True) for c in cells if c.find("a", href=True)), None)
            if anchor is None or not looks_like_content_link(anchor, source["prefix"]):
                continue
            url = urljoin(source["url"], anchor["href"]).split("#")[0]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            title = anchor.get_text(strip=True)
            if not title:
                continue
            published = None
            for cell in cells:
                published = parse_greek_date(cell.get_text(" ", strip=True))
                if published:
                    break
            results.append({
                "category": source["label"],
                "published_date": published or "",
                "title": title,
                "url": url,
            })

    # Layout 2: blog-style cards, no table / no dates (e.g. "Νέα")
    if not results:
        for a in container.find_all("a", href=True):
            if not looks_like_content_link(a, source["prefix"]):
                continue
            url = urljoin(source["url"], a["href"]).split("#")[0]
            if url in seen_urls:
                continue
            seen_urls.add(url)
            title = a.get_text(strip=True)
            if not title:
                continue
            results.append({
                "category": source["label"],
                "published_date": "",
                "title": title,
                "url": url,
            })

    return results


def load_existing():
    if not os.path.exists(ARCHIVE_PATH):
        return {}
    existing = {}
    with open(ARCHIVE_PATH, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            existing[row["url"]] = row
    return existing


def main():
    existing = load_existing()
    today = date.today().isoformat()
    added = 0

    for source in SOURCES:
        try:
            items = fetch_source(source)
        except Exception as e:
            print(f"WARNING: failed to fetch {source['label']}: {e}", file=sys.stderr)
            continue
        for item in items:
            if item["url"] in existing:
                continue
            existing[item["url"]] = {
                "category": item["category"],
                "published_date": item["published_date"],
                "title": item["title"],
                "url": item["url"],
                "archived_on": today,
            }
            added += 1

    rows = list(existing.values())
    # Stable sort: newest date first, then group by category (ascending).
    category_order = {s["label"]: i for i, s in enumerate(SOURCES)}
    rows.sort(key=lambda r: r.get("published_date") or "0001-01-01", reverse=True)
    rows.sort(key=lambda r: category_order.get(r["category"], 999))

    with open(ARCHIVE_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Added {added} new announcement(s). Total in archive: {len(rows)}")

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a") as f:
            f.write(f"added={added}\n")


if __name__ == "__main__":
    main()
