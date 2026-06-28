from playwright.sync_api import sync_playwright, Page
from bs4 import BeautifulSoup
import json
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
from zoneinfo import ZoneInfo
import re
import sys
import time
import random

# ---------------------------------------------------------
# ANTI-BOT USER AGENTS + VIEWPORTS
# ---------------------------------------------------------
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
    "Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Chrome/125.0 Mobile Safari/537.36"
]

VIEWPORTS = [
    {"width": 1920, "height": 1080},
    {"width": 1366, "height": 768},
    {"width": 1280, "height": 800},
    {"width": 414, "height": 896},   # mobile
    {"width": 768, "height": 1024},  # tablet
]

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------
WISHLIST_URL = os.environ.get("WISHLIST_URL")
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_PASS = os.environ["GMAIL_APP_PASSWORD"]
THRESHOLD = float(os.environ.get("ALERT_THRESHOLD", 1))

DEBUG_HTML = os.environ.get("DEBUG_HTML", "0") == "1"

DATA_FILE = "prices.json"
BACKUP_FILE = "prices.json.bak"
LOG_DIR = "logs"
TIMEOUT_PAGE = 20000
TIMEOUT_SELECTOR = 2500
RETRY_COUNT = 2

LOCK_FILE = "monitor.lock"
LOCK_TIMEOUT = 60 * 30  # 30 minuti

REPORT_FILE = os.path.join("debug_output", "wishlist_report.html")

# ---------------------------------------------------------
# LOCK SYSTEM
# ---------------------------------------------------------
def acquire_lock():
    if os.path.exists(LOCK_FILE):
        age = time.time() - os.path.getmtime(LOCK_FILE)
        if age > LOCK_TIMEOUT:
            print(f"[LOCK] Lock vecchio ({age:.0f}s). Lo rimuovo.")
            os.remove(LOCK_FILE)
        else:
            print("[LOCK] Un'altra esecuzione è già in corso. Esco.")
            sys.exit(0)

    with open(LOCK_FILE, "w") as f:
        f.write(str(os.getpid()))

def release_lock():
    if os.path.exists(LOCK_FILE):
        os.remove(LOCK_FILE)

# ---------------------------------------------------------
# LOGGING
# ---------------------------------------------------------
def log(msg: str):
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, datetime.now().strftime("%Y-%m-%d") + ".log")
    line = "[" + datetime.now().strftime("%H:%M:%S") + "] " + msg
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)

# ---------------------------------------------------------
# EMAIL
# ---------------------------------------------------------
def send_email(body: str):
    log("Invio email di alert…")
    msg = MIMEText(body, "html")
    msg["Subject"] = "📉 Amazon Wishlist Price Drop"
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.send_message(msg)

    log("Email inviata.")

# ---------------------------------------------------------
# BACKUP SYSTEM
# ---------------------------------------------------------
def load_prices():
    if not os.path.exists(DATA_FILE):
        return {}

    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
            log("File prezzi caricato correttamente.")
            return data
    except Exception as e:
        log(f"ERRORE: file prezzi corrotto ({e}). Recupero dal backup…")

        if os.path.exists(BACKUP_FILE):
            with open(BACKUP_FILE, "r") as f:
                data = json.load(f)
                log("Backup ripristinato.")
                return data

        log("Backup mancante. Storico perso.")
        return {}

def save_prices(data):
    with open(BACKUP_FILE, "w") as f:
        json.dump(data, f, indent=2)

    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)

    os.replace(tmp, DATA_FILE)
    log("Prezzi salvati in modo sicuro.")

# ---------------------------------------------------------
# REPORT HTML
# ---------------------------------------------------------
def generate_html_report(data):
    os.makedirs("debug_output", exist_ok=True)

    rows = []
    for asin, entry in sorted(data.items()):
        name = entry.get("name", asin)
        current = entry.get("current")
        history = entry.get("history", [])
        missing = entry.get("missing_count", 0)
        pending = entry.get("pending_drop")

        history_str = "<br>".join(
            f"{h['date']}: €{h['price']:.2f}"
            for h in history[-3:]
        )

        status = []
        if missing > 0:
            status.append(f"missing x{missing}")
        if pending:
            status.append("pending drop")
        if not status:
            status.append("ok")

        rows.append(f"""
        <tr>
          <td>{asin}</td>
          <td>{name}</td>
          <td>{'€{:.2f}'.format(current) if current is not None else 'N/D'}</td>
          <td>{'<br>'.join(status)}</td>
          <td>{history_str}</td>
        </tr>
        """)

    html = f"""<!DOCTYPE html>
<html lang="it">
<head>
  <meta charset="utf-8">
  <title>Amazon Wishlist Report</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; background:#f5f5f5; }}
    table {{ border-collapse: collapse; width: 100%; background:#fff; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; font-size: 14px; vertical-align: top; }}
    th {{ background:#222; color:#fff; position: sticky; top:0; }}
    tr:nth-child(even) {{ background:#fafafa; }}
  </style>
</head>
<body>
  <h1>Amazon Wishlist Report</h1>
  <p>Generato il {datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d %H:%M:%S")}</p>
  <table>
    <thead>
      <tr>
        <th>ASIN</th>
        <th>Nome</th>
        <th>Prezzo attuale</th>
        <th>Stato</th>
        <th>Storico (ultimi 3)</th>
      </tr>
    </thead>
    <tbody>
      {''.join(rows)}
    </tbody>
  </table>
</body>
</html>
"""
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    log(f"Report HTML generato: {REPORT_FILE}")

# ---------------------------------------------------------
# NORMALIZZA NOME
# ---------------------------------------------------------
def normalize(name: str) -> str:
    name = name.lower()
    name = re.sub(r"\(.*?\)|\[.*?\]|\{.*?\}", "", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    return re.sub(r"\s+", " ", name).strip()

# ---------------------------------------------------------
# EXTRACT ASIN
# ---------------------------------------------------------
def extract_asin(url: str) -> str | None:
    m = re.search(r"/dp/([A-Z0-9]{10})", url)
    if m:
        return m.group(1)
    m = re.search(r"/product/([A-Z0-9]{10})", url)
    if m:
        return m.group(1)
    return None

# ---------------------------------------------------------
# SHIPPING + SELLER
# ---------------------------------------------------------
def extract_shipping_and_seller(html: str):
    soup = BeautifulSoup(html, "lxml")

    seller = None
    shipped_by = None
    shipping_cost = None

    odf_seller = soup.select_one("#merchantInfoFeature_feature_div .offer-display-feature-text-message")
    if odf_seller:
        seller = odf_seller.get_text(strip=True)
        shipped_by = seller

    el = soup.select_one("[data-csa-c-delivery-price]")
    if el:
        raw = el.get("data-csa-c-delivery-price", "").strip()
        raw = raw.replace("a ", "").replace("&nbsp;", " ").strip()
        if raw:
            shipping_cost = raw

    if not shipping_cost:
        el = soup.select_one("#mir-layout-DELIVERY_BLOCK-slot-PRIMARY_DELIVERY_MESSAGE_LARGE span")
        if el:
            txt = el.get_text(strip=True)
            m = re.search(r"(\d+,\d+)\s*€", txt)
            if m:
                shipping_cost = m.group(1) + " €"

    if not seller:
        el = soup.select_one("#merchant-info")
        if el:
            seller = el.get_text(strip=True)

    return seller, shipped_by, shipping_cost

# ---------------------------------------------------------
# PRICE PARSER
# ---------------------------------------------------------
def parse_price_from_html(html: str) -> float | None:
    soup = BeautifulSoup(html, "lxml")

    price_block = soup.select_one("span.a-price > span.a-offscreen")
    if price_block:
        try:
            return float(price_block.get_text(strip=True).replace("€","").replace(",","."))
        except:
            pass

    return None

# ---------------------------------------------------------
# STEALTH MODE
# ---------------------------------------------------------
STEALTH_JS = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
"""

# ---------------------------------------------------------
# SCRAPING PREZZO
# ---------------------------------------------------------
def get_product_price(page: Page, url: str, asin: str):
    for attempt in range(1, RETRY_COUNT+1):

        time.sleep(random.uniform(0.5, 1.5))  # jitter anti-bot

        log(f"  Tentativo {attempt} per ASIN {asin}")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_PAGE)

            time.sleep(random.uniform(0.8, 2.2))  # anti-bot delay

            html = page.content()

            if "captcha" in html.lower():
                log(f"  CAPTCHA rilevato per {asin}")
                return None, None, None, None

            try:
                page.wait_for_selector(".a-price, .a-offscreen", timeout=TIMEOUT_SELECTOR)
            except:
                log(f"  Nessun selettore prezzo trovato per {asin}")
                continue

            html = page.content()
            price = parse_price_from_html(html)
            seller, shipped_by, shipping_cost = extract_shipping_and_seller(html)

            if price and price > 0:
                log(f"  Prezzo trovato: €{price:.2f}")
                return price, seller, shipped_by, shipping_cost

        except Exception as e:
            log(f"  Errore durante parsing {asin}: {e}")

    return None, None, None, None

# ---------------------------------------------------------
# SCRAPING WISHLIST
# ---------------------------------------------------------
def get_items():
    log("Apertura Playwright…")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        ua = random.choice(USER_AGENTS)
        vp = random.choice(VIEWPORTS)

        context = browser.new_context(
            user_agent=ua,
            viewport=vp,
            locale="it-IT",
            timezone_id="Europe/Rome",
            device_scale_factor=1,
        )

        context.set_extra_http_headers({
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "DNT": "1"
        })

        context.add_init_script(STEALTH_JS)

        page = context.new_page()
        page.set_default_timeout(TIMEOUT_PAGE)

        log("Caricamento wishlist…")
        page.goto(WISHLIST_URL, wait_until="domcontentloaded")

        time.sleep(random.uniform(1.0, 2.5))  # anti-bot delay

        last_count = 0
        stable_rounds = 0

        log("Inizio scroll infinito…")

        while True:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            page.wait_for_timeout(700)

            html = page.content()
            soup = BeautifulSoup(html, "lxml")
            rows = soup.select("div[data-asin], li[data-asin], div.wishlist-item")

            if not rows:
                rows = soup.select("div.a-section.a-spacing-none[data-asin]")

            current_count = len(rows)

            log(f"→ Elementi visibili: {current_count}")

            if current_count == last_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
                last_count = current_count

            if stable_rounds >= 2:
                break

        items = []
        for row in rows:
            title_el = row.select_one("a.a-link-normal, a.a-text-normal")
            if not title_el:
                continue

            name = title_el.get("title", "").strip() or title_el.get_text(strip=True)
            url = title_el.get("href", "")
            if not url.startswith("http"):
                url = "https://www.amazon.it" + url

            asin = extract_asin(url)
            if not asin:
                continue

            items.append((asin, name, url))

        browser.close()
        return items

# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
def main():
    acquire_lock()
    try:
        log("===== INIZIO MONITOR =====")
        log(f"THRESHOLD: {THRESHOLD}%")

        old = load_prices()

        try:
            items = get_items()
        except Exception as e:
            log("ERRORE CRITICO durante get_items()")
            log(str(e))
            return

        new = old.copy()
        alerts = []
        today = datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d %H:%M:%S")

        found_asins = set()

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)

            ua = random.choice(USER_AGENTS)
            vp = random.choice(VIEWPORTS)

            context = browser.new_context(
                user_agent=ua,
                viewport=vp,
                locale="it-IT",
                timezone_id="Europe/Rome",
                device_scale_factor=1,
            )

            context.set_extra_http_headers({
                "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1"
            })

            context.add_init_script(STEALTH_JS)

            page = context.new_page()
            page.set_default_timeout(TIMEOUT_PAGE)

            processed = 0

            for asin, raw_name, url in items:
                found_asins.add(asin)
                name = normalize(raw_name)

                log(f"Elaborazione {asin} ({name})")

                price, seller, shipped_by, shipping_cost = get_product_price(page, url, asin)
                processed += 1

                if asin not in new:
                    new[asin] = {
                        "name": name,
                        "current": price,
                        "history": [{"date": today, "price": price}] if price else [],
                        "pending_drop": None,
                        "missing_count": 0
                    }
                    continue

                entry = new[asin]

                if "missing_count" not in entry:
                    entry["missing_count"] = 0

                entry["missing_count"] = 0
                old_price = entry["current"]

                if price is None:
                    log(f"  Prezzo non disponibile per {asin}, mantengo il precedente.")
                    continue

                if old_price != price:
                    entry["history"].append({"date": today, "price": price})
                    log(f"  Prezzo cambiato: {old_price} → {price}")

                entry["current"] = price

                if old_price and old_price > 0:
                    drop = ((old_price - price) / old_price) * 100

                    if drop >= THRESHOLD:
                        pd = entry.get("pending_drop")

                        if not pd:
                            entry["pending_drop"] = {
                                "first_seen": today,
                                "old_price": old_price,
                                "new_price": price
                            }
                            log(f"  Ribasso rilevato per {asin}, in attesa di conferma…")
                        else:
                            if pd["new_price"] == price:
                                alerts.append(
                                    f"<b>{entry['name']}</b><br>"
                                    f"Vecchio: €{old_price:.2f}<br>"
                                    f"Nuovo: €{price:.2f}<br>"
                                    f"↓ {drop:.1f}% (confermato)<br><br>"
                                    f"<a href='{url}'>amazon</a><br><br>"
                                )
                                log(f"  Ribasso confermato per {asin}!")
                                entry["pending_drop"] = None
                            else:
                                log(f"  Ribasso fantasma per {asin}, reset.")
                                entry["pending_drop"] = None

            browser.close()
            log(f"Prodotti processati: {processed}")

        for asin in list(new.keys()):
            if asin not in found_asins:

                if "missing_count" not in new[asin]:
                    new[asin]["missing_count"] = 0

                new[asin]["missing_count"] += 1
                log(f"Prodotto {asin} non trovato (missing_count={new[asin]['missing_count']})")

                if new[asin]["missing_count"] >= 3:
                    log(f"Prodotto {asin} rimosso definitivamente dalla wishlist.")
                    del new[asin]

        save_prices(new)
        generate_html_report(new)

        if alerts:
            send_email("<br>".join(alerts))
        else:
            log("Nessun alert generato.")

        log("===== FINE MONITOR =====")

    finally:
        release_lock()

if __name__ == "__main__":
    main()

