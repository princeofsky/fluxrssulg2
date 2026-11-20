#!/usr/bin/env python3
"""Collecte les actualités ULiège et génère un flux RSS 2.0."""
import json, os, re, sys, time
from pathlib import Path
from datetime import datetime, timezone
from email.utils import format_datetime
from urllib.parse import urlencode
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup
import atexit
from playwright.sync_api import sync_playwright

BASE = "https://www.news.uliege.be/cms/c_9435330/fr/prog-cours"
ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state/articles.json"
FEED = ROOT / "docs/feed.xml"
MAX_PAGES = int(os.getenv("MAX_PAGES", "10"))
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (compatible; ULiegeRSSBot/1.0)", "Accept-Language": "fr,en;q=0.8"})


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
        user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    )

    try:
        response = page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(4000)

        print(f"[BROWSER] Statut : {response.status if response else 'inconnu'}")
        print(f"[BROWSER] URL : {page.url}")
        print(f"[BROWSER] Titre : {page.title()}")
        print(f"[BROWSER] Liens : {page.locator('a[href]').count()}")

        html = page.content()
        return BeautifulSoup(html, "html.parser")

    finally:
        page.close()


def clean(s):
    return re.sub(r"\\s+", " ", s or "").strip()

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
    from urllib.parse import urljoin
    if not url:
        return ""
    return urljoin("https://www.news.uliege.be/", url)

def parse_listing(soup):
    out, seen = [], set()
    for a in soup.select("h3 a[href]"):
        title, link = clean(a.get_text(" ", strip=True)), absurl(a.get("href"), BASE)
        if not title or link in seen: continue
        seen.add(link)
        card = a
        for _ in range(4):
            if card.parent: card = card.parent
        desc = ""
        for selector in ("p", ".description", ".summary", ".teaser"):
            n = card.select_one(selector)
            if n and clean(n.get_text(" ", strip=True)):
                desc = clean(n.get_text(" ", strip=True)); break
        img = card.select_one("img")
        src = (img.get("src") or img.get("data-src") or img.get("data-original") or "") if img else ""
        if not src and img and img.get("srcset"):
            src = img["srcset"].split(",")[0].strip().split(" ")[0]
        out.append({"id": link, "title": title, "link": link, "description": desc, "image": absurl(src, link) if src else "", "content": "", "published": ""})
    return out

def get_date(soup):
    selectors = [('meta[property="article:published_time"]',"content"),('meta[name="date"]',"content"),('meta[name="datePublished"]',"content"),('meta[itemprop="datePublished"]',"content"),("time[datetime]","datetime")]
    for sel, attr in selectors:
        n = soup.select_one(sel); val = n.get(attr) if n else None
        if val:
            try:
                d = datetime.fromisoformat(val.replace("Z","+00:00"))
                return (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
            except ValueError: pass
    m = re.search(r"(?:Publié le|Mis en ligne le)\\s*(\\d{1,2})[./-](\\d{1,2})[./-](\\d{4})", clean(soup.get_text(" ",strip=True)), re.I)
    if m:
        try:
            day, month, year = map(int,m.groups())
            return datetime(year,month,day,tzinfo=timezone.utc)
        except ValueError: pass
    return None

def enrich(item):
    try:
        soup = get(item["link"]); d = get_date(soup)
        item["published"] = d.isoformat() if d else ""
        if not item["image"]:
            og = soup.select_one('meta[property="og:image"]')
            if og and og.get("content"): item["image"] = absurl(og["content"],item["link"])
        for sel in ("[itemprop='articleBody']","article","main"):
            body = soup.select_one(sel)
            if body:
                for x in body.select("script,style,nav,form,header,footer"): x.decompose()
                item["content"] = str(body)
                if not item["description"]: item["description"] = clean(body.get_text(" ",strip=True))[:500]
                break
    except Exception as e: print(f"Enrichissement impossible {item['link']}: {e}",file=sys.stderr)
    return item

def main():
    STATE.parent.mkdir(parents=True,exist_ok=True); FEED.parent.mkdir(parents=True,exist_ok=True)
    try: old = json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError): old = []
    known = {x["id"]:x for x in old if x.get("id")}
    fresh = []
        
    for page in range(MAX_PAGES):
        url = listing(page * 12)
        soup = get(url)

        print(f"\n[DEBUG] Page : {url}")
        print(f"[DEBUG] Titre HTML : {soup.title.get_text(strip=True) if soup.title else 'Absent'}")
        print(f"[DEBUG] Nombre de H3 : {len(soup.select('h3'))}")
        print(f"[DEBUG] Nombre de liens : {len(soup.select('a[href]'))}")
        print(f"[DEBUG] Liens dans les H3 : {len(soup.select('h3 a[href]'))}")

        entries = parse_listing(soup)

        print(f"[DEBUG] Articles détectés : {len(entries)}")

        for entry in entries[:5]:
            print(f"[DEBUG] - {entry['title']} : {entry['link']}")

        if not entries:
            break

        fresh.extend(entries)

        if len(entries) < 12:
            break

        time.sleep(.4)



    for item in fresh:
        if item["id"] in known:
            for k in ("title","link","description","image"):
                if item.get(k): known[item["id"]][k]=item[k]
        else:
            for item in fresh:
    if item["id"] in known:
        for k in ("title","link","description","image"):
            if item.get(k):
                known[item["id"]][k]=item[k]
    else:
        known[item["id"]] = item
        time.sleep(.25)
    items = sorted(known.values(),key=lambda x:x.get("published") or "",reverse=True)[:2000]
    STATE.write_text(json.dumps(items,ensure_ascii=False,indent=2)+"\\n",encoding="utf-8")
    ET.register_namespace("content","http://purl.org/rss/1.0/modules/content/")
    ET.register_namespace("media","http://search.yahoo.com/mrss/")
    rss=ET.Element("rss",{"version":"2.0"}); ch=ET.SubElement(rss,"channel")
    def add(parent,tag,val): ET.SubElement(parent,tag).text=val
    add(ch,"title","Actualités — Université de Liège"); add(ch,"link",BASE)
    add(ch,"description","Actualités du portail news de l’Université de Liège.")
    add(ch,"language","fr-BE"); add(ch,"lastBuildDate",format_datetime(datetime.now(timezone.utc)))
    for x in items:
        it=ET.SubElement(ch,"item")
        add(it,"title",x.get("title","")); add(it,"link",x.get("link",""))
        add(it,"guid",x.get("id") or x.get("link","")); add(it,"description",x.get("description",""))
        if x.get("published"):
            try:
                d=datetime.fromisoformat(x["published"].replace("Z","+00:00"))
                add(it,"pubDate",format_datetime(d if d.tzinfo else d.replace(tzinfo=timezone.utc)))
            except ValueError: pass
        if x.get("content"): ET.SubElement(it,"{http://purl.org/rss/1.0/modules/content/}encoded").text=x["content"]
        if x.get("image"): ET.SubElement(it,"{http://search.yahoo.com/mrss/}content",{"url":x["image"],"medium":"image"})
    ET.ElementTree(rss).write(FEED,encoding="utf-8",xml_declaration=True)
    print(f"Flux généré : {len(items)} articles")

if __name__ == "__main__": main()
