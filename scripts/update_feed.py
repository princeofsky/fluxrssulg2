
#!/usr/bin/env python3
"""Collecte les actualités ULiège et génère un flux RSS 2.0."""

import atexit
import hashlib
import json
import os
import re
import sys
import time

from pathlib import Path
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urlencode, urljoin

import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright


BASE = "https://www.news.uliege.be/cms/c_9435330/fr/prog-cours"
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state/articles.json"
FEED = ROOT / "docs/feed.xml"

MAX_PAGES = int(os.getenv("MAX_PAGES", "10"))

S = requests.Session()
S.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; ULiegeRSSBot/1.0)",
    "Accept-Language": "fr,en;q=0.8"
})


_pw = None
_browser = None


def close_browser():
    global _pw, _browser

    if _browser:
        _browser.close()

    if _pw:
        _pw.stop()


atexit.register(close_browser)


def get(url):
    global _pw, _browser

    if _browser is None:
        _pw = sync_playwright().start()
        _browser = _pw.chromium.launch(headless=True)

    page = _browser.new_page(
        locale="fr-BE",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        )
    )

    try:
        response = page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        # Attente conservée volontairement.
        page.wait_for_timeout(4000)

        print(
            f"[BROWSER] Statut : "
            f"{response.status if response else 'inconnu'}"
        )
        print(f"[BROWSER] URL : {page.url}")
        print(f"[BROWSER] Titre : {page.title()}")
        print(f"[BROWSER] Liens : {page.locator('a[href]').count()}")

        html = page.content()
        return BeautifulSoup(html, "html.parser")

    finally:
        page.close()


def clean(s):
    return re.sub(r"\s+", " ", s or "").strip()


def stable_id(title, link):
    """Construit un identifiant stable à partir du titre et du lien."""

    key = f"{clean(title).casefold()}|{link.strip()}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()

    return f"uliege-{digest}"


def listing(start):
    if start == 0:
        return BASE

    return BASE + "?" + urlencode({
        "pageSize": 12,
        "pagerAll": "false",
        "reverse": "false",
        "start": start
    })


def absurl(url, base=None):
    if not url:
        return ""

    return urljoin(
        base or "https://www.news.uliege.be/",
        url.strip()
    )


def extract_image(card, link):
    """Récupère une image, y compris en chargement différé."""

    if not card:
        return ""

    # Images classiques et variantes de chargement différé.
    for node in card.select("img, source"):

        src = (
            node.get("data-src")
            or node.get("data-lazy-src")
            or node.get("data-original")
            or node.get("data-original-src")
            or node.get("data-image")
            or node.get("src")
            or ""
        )

        # Images définies dans srcset ou data-srcset.
        if not src:
            srcset = (
                node.get("data-srcset")
                or node.get("srcset")
                or ""
            )

            if srcset:
                candidates = [
                    part.strip().split()[0]
                    for part in srcset.split(",")
                    if part.strip()
                ]

                if candidates:
                    src = candidates[-1]

        if src and not src.startswith("data:"):
            return absurl(src, link)

    # Images définies en arrière-plan CSS.
    for node in card.select("[style]"):

        style = node.get("style", "")

        match = re.search(
            r"""background(?:-image)?\s*:\s*url\(['"]?([^'")]+)""",
            style
        )

        if match:
            src = match.group(1).strip()

            if src and not src.startswith("data:"):
                return absurl(src, link)

    return ""


def parse_listing(soup):
    out = []
    seen = set()

    for a in soup.select("h3 a[href]"):

        title = clean(a.get_text(" ", strip=True))
        link = absurl(a.get("href"), BASE)

        if not title or not link:
            continue

        # On identifie une actualité par son titre ET son lien.
        article_id = stable_id(title, link)

        if article_id in seen:
            continue

        seen.add(article_id)

        # Remonte dans le HTML pour trouver la carte
        # contenant le titre et l'image.
        card = a
        image = ""

        for _ in range(8):

            if card.parent:
                card = card.parent

            image = extract_image(card, BASE)

            if image:
                break

        # Récupération de la description.
        desc = ""

        for selector in (
            "p",
            ".description",
            ".summary",
            ".teaser"
        ):

            n = card.select_one(selector)

            if n:
                candidate = clean(
                    n.get_text(" ", strip=True)
                )

                if candidate:
                    desc = candidate
                    break

        out.append({
            "id": article_id,
            "title": title,
            "link": link,
            "description": desc,
            "image": image,
            "content": "",
            "published": ""
        })

    return out


def get_date(soup):

    selectors = [
        ('meta[property="article:published_time"]', "content"),
        ('meta[name="date"]', "content"),
        ('meta[name="datePublished"]', "content"),
        ('meta[itemprop="datePublished"]', "content"),
        ("time[datetime]", "datetime")
    ]

    for sel, attr in selectors:

        n = soup.select_one(sel)
        val = n.get(attr) if n else None

        if val:
            try:
                d = datetime.fromisoformat(
                    val.replace("Z", "+00:00")
                )

                return (
                    d if d.tzinfo
                    else d.replace(tzinfo=timezone.utc)
                ).astimezone(timezone.utc)

            except ValueError:
                pass

    m = re.search(
        r"(?:Publié le|Mis en ligne le)\s*"
        r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})",
        clean(soup.get_text(" ", strip=True)),
        re.I
    )

    if m:
        try:
            day, month, year = map(int, m.groups())

            return datetime(
                year,
                month,
                day,
                tzinfo=timezone.utc
            )

        except ValueError:
            pass

    return None


def enrich(item):

    try:
        soup = get(item["link"])
        d = get_date(soup)

        item["published"] = d.isoformat() if d else ""

        if not item["image"]:
            og = soup.select_one(
                'meta[property="og:image"]'
            )

            if og and og.get("content"):
                item["image"] = absurl(
                    og["content"],
                    item["link"]
                )

        for sel in (
            "[itemprop='articleBody']",
            "article",
            "main"
        ):

            body = soup.select_one(sel)

            if body:
                for x in body.select(
                    "script,style,nav,form,header,footer"
                ):
                    x.decompose()

                item["content"] = str(body)

                if not item["description"]:
                    item["description"] = clean(
                        body.get_text(" ", strip=True)
                    )[:500]

                break

    except Exception as e:
        print(
            f"Enrichissement impossible {item['link']}: {e}",
            file=sys.stderr
        )

    return item


def find_existing(item, known):
    """Retrouve un article déjà enregistré."""

    # Recherche prioritaire par identifiant.
    if item["id"] in known:
        return item["id"]

    # Compatibilité avec les anciens identifiants,
    # qui étaient simplement les URL des articles.
    for old_id, old_item in known.items():

        if (
            clean(old_item.get("title")) == clean(item["title"])
            and old_item.get("link") == item["link"]
        ):
            return old_id

    return None


def main():

    STATE.parent.mkdir(parents=True, exist_ok=True)
    FEED.parent.mkdir(parents=True, exist_ok=True)

    try:
        old = json.loads(
            STATE.read_text(encoding="utf-8")
        )

    except (OSError, json.JSONDecodeError):
        old = []

    known = {
        x["id"]: x
        for x in old
        if x.get("id")
    }

    fresh = []

    # Collecte des pages de résultats.
    for page in range(MAX_PAGES):

        url = listing(page * 12)
        soup = get(url)

        print(f"\n[DEBUG] Page : {url}")

        print(
            f"[DEBUG] Titre HTML : "
            f"{soup.title.get_text(strip=True) if soup.title else 'Absent'}"
        )

        print(
            f"[DEBUG] Nombre de H3 : "
            f"{len(soup.select('h3'))}"
        )

        print(
            f"[DEBUG] Nombre de liens : "
            f"{len(soup.select('a[href]'))}"
        )

        print(
            f"[DEBUG] Liens dans les H3 : "
            f"{len(soup.select('h3 a[href]'))}"
        )

        entries = parse_listing(soup)

        # Compteur d'images détectées.
        images = sum(
            1 for entry in entries
            if entry.get("image")
        )

        print(
            f"[DEBUG] Articles détectés : {len(entries)}"
        )

        print(
            f"[DEBUG] Images détectées : "
            f"{images}/{len(entries)}"
        )

        for entry in entries[:5]:
            print(
                f"[DEBUG] - {entry['title']} : "
                f"{entry['link']}"
            )

            if entry.get("image"):
                print(
                    f"[DEBUG]   Image : {entry['image']}"
                )

        if not entries:
            break

        fresh.extend(entries)

        if len(entries) < 12:
            break

        time.sleep(.4)

    # Mise à jour des articles existants et ajout des nouveaux.
    for item in fresh:

        existing_id = find_existing(item, known)

        if existing_id is not None:

            existing = known[existing_id]

            # Mise à jour des informations disponibles.
            for k in (
                "title",
                "link",
                "description",
                "image"
            ):

                if item.get(k):
                    existing[k] = item[k]

            # On conserve impérativement l'ancien GUID
            # et la date de publication déjà enregistrée.

        else:

            # Date de première détection.
            item["published"] = datetime.now(
                timezone.utc
            ).isoformat()

            known[item["id"]] = item

            print(
                f"[NOUVEAU] {item['title']}"
            )

            time.sleep(.25)

    # Tri et sauvegarde des articles.
    items = sorted(
        known.values(),
        key=lambda x: x.get("published") or "",
        reverse=True
    )[:2000]

    STATE.write_text(
        json.dumps(
            items,
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    # Génération du flux RSS.
    ET.register_namespace(
        "content",
        "http://purl.org/rss/1.0/modules/content/"
    )

    ET.register_namespace(
        "media",
        "http://search.yahoo.com/mrss/"
    )

    rss = ET.Element(
        "rss",
        {"version": "2.0"}
    )

    ch = ET.SubElement(rss, "channel")

    def add(parent, tag, val):
        ET.SubElement(parent, tag).text = str(val or "")

    add(
        ch,
        "title",
        "Actualités — Université de Liège"
    )

    add(ch, "link", BASE)

    add(
        ch,
        "description",
        "Actualités du portail news de l’Université de Liège."
    )

    add(ch, "language", "fr-BE")

    add(
        ch,
        "lastBuildDate",
        format_datetime(datetime.now(timezone.utc))
    )

    for x in items:

        it = ET.SubElement(ch, "item")

        add(it, "title", x.get("title", ""))
        add(it, "link", x.get("link", ""))

        # GUID unique et stable.
        guid = ET.SubElement(
            it,
            "guid",
            {"isPermaLink": "false"}
        )

        guid.text = (
            x.get("id")
            or stable_id(
                x.get("title", ""),
                x.get("link", "")
            )
        )

        add(
            it,
            "description",
            x.get("description", "")
        )

        # Date de publication ou de première détection.
        if x.get("published"):

            try:
                d = datetime.fromisoformat(
                    x["published"].replace("Z", "+00:00")
                )

                if not d.tzinfo:
                    d = d.replace(tzinfo=timezone.utc)

                add(
                    it,
                    "pubDate",
                    format_datetime(d)
                )

            except (ValueError, TypeError):
                pass

        if x.get("content"):

            ET.SubElement(
                it,
                "{http://purl.org/rss/1.0/modules/content/}encoded"
            ).text = x["content"]

        # Ajout de l'image au flux RSS.
        if x.get("image"):

            ET.SubElement(
                it,
                "{http://search.yahoo.com/mrss/}content",
                {
                    "url": x["image"],
                    "medium": "image"
                }
            )

    ET.ElementTree(rss).write(
        FEED,
        encoding="utf-8",
        xml_declaration=True
    )

    print(f"Flux généré : {len(items)} articles")


if __name__ == "__main__":
    main()
