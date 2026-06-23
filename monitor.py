from playwright.sync_api import sync_playwright, Page
from bs4 import BeautifulSoup
import json
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import re

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
WISHLIST_URL = os.environ.get("WISHLIST_URL")
GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_PASS = os.environ["GMAIL_APP_PASSWORD"]
THRESHOLD = float(os.environ.get("ALERT_THRESHOLD", 1))

DATA_FILE = "prices.json"
LOG_DIR = "logs"
TIMEOUT_PAGE = 20000
TIMEOUT_SELECTOR = 2500
RETRY_COUNT = 2

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
    msg = MIMEText(body)
    msg["Subject"] = "📉 Amazon Wishlist Price Drop"
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.send_message(msg)

    log("Email inviata.")

# ---------------------------------------------------------
# NORMALIZZA NOME
# ---------------------------------------------------------
def normalize(name: str) -> str:
    name = name.lower()
    name = re.sub(r"\(.*?\)|\[.*?\]|\{.*?\}", "", name)

    blacklist = [
        "vinile", "lp", "remaster", "remastered", "edition", "edizione",
        "anniversary", "deluxe", "expanded", "version", "2lp", "3lp",
        "limited", "limitata", "col", "color", "colored", "transparent",
        "black", "white", "yellow", "blue", "red", "180gr", "180g",
        "20th", "25th", "30th", "40th"
    ]
    for word in blacklist:
        name = name.replace(word, "")

    name = re.sub(r"[^a-z0-9]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name

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
# PRICE PARSER
# ---------------------------------------------------------
def parse_price_from_html(html: str) -> float | None:
    soup = BeautifulSoup(html, "lxml")

    for bad in soup.select("#sims-consolidated-2, #sims-consolidated-3, #sp_detail, .a-carousel"):
        bad.decompose()

    price_block = soup.select_one(
        "#corePrice_feature_div, #apex_desktop, #corePriceDisplay_desktop_feature_div"
    )
    if price_block:
        offscreen = price_block.select_one("span.a-price > span.a-offscreen")
        if offscreen:
            try:
                return float(offscreen.get_text(strip=True).replace("€", "").replace(",", "."))
            except:
                pass

    fallback = soup.select_one("span.a-price > span.a-offscreen")
    if fallback:
        try:
            return float(fallback.get_text(strip=True).replace("€", "").replace(",", "."))
        except:
            pass

    whole = soup.select_one("span.a-price-whole")
    frac = soup.select_one("span.a-price-fraction")
    if whole and frac:
        try:
            return float(whole.get_text(strip=True).replace(".", "") + "." + frac.get_text(strip=True))
        except:
            pass

    return None

# ---------------------------------------------------------
# STEALTH MODE
# ---------------------------------------------------------
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = { runtime: {} };
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3] });
Object.defineProperty(navigator, 'languages', { get: () => ['it-IT', 'it'] });
Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
"""

# ---------------------------------------------------------
# SCRAPING PREZZO
# ---------------------------------------------------------
def get_product_price(page: Page, url: str, name: str) -> float | None:
    for attempt in range(1, RETRY_COUNT + 1):
        log(f"  Tentativo {attempt} per {name}")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_PAGE)
            html = page.content()

            if "captcha" in html.lower():
                log(f"  CAPTCHA rilevato per {name}")
                return None

            try:
                page.wait_for_selector(
                    ".a-price, .a-offscreen, #corePrice_feature_div, #twister-plus-price-data-price",
                    timeout=TIMEOUT_SELECTOR
                )
            except:
                log(f"  Nessun selettore prezzo trovato per {name}")
                continue

            html = page.content()
            price = parse_price_from_html(html)

            if price and price > 0:
                log(f"  Prezzo trovato: €{price:.2f}")
                return price

            log(f"  Parsing fallito per {name}")

        except Exception as e:
            log(f"  Errore durante parsing {name}: {e}")

    return None

# ---------------------------------------------------------
# SCRAPING WISHLIST
# ---------------------------------------------------------
def get_items():
    log("Apertura Playwright…")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            locale="it-IT",
            timezone_id="Europe/Rome",
            viewport={"width": 1920, "height": 1080}
        )

        # Stealth SEMPRE attivo
        context.add_init_script(STEALTH_JS)

        # Blocca solo font (NON immagini)
        context.route("**/*", lambda route: route.abort()
                      if route.request.resource_type in ["font"]
                      else route.continue_())

        # Carica cookie Amazon
        cookies_json = os.environ.get("AMAZON_COOKIES")
        if cookies_json:
            try:
                cookies = json.loads(cookies_json)
                context.add_cookies(cookies)
                log("Cookie Amazon caricati nel browser.")
            except Exception as e:
                log(f"Errore caricamento cookie Amazon: {e}")

        page = context.new_page()
        page.set_default_timeout(TIMEOUT_PAGE)

        log("Caricamento wishlist…")
        page.goto(WISHLIST_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1500)

        last_count = 0
        stable_rounds = 0

        log("Inizio scroll infinito…")

        while True:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            page.wait_for_timeout(700)

            html = page.content()
            soup = BeautifulSoup(html, "lxml")
            rows = soup.select("div.g-item-sortable, [data-itemid]")
            current_count = len(rows)

            log(f"→ Elementi visibili: {current_count}")

            if current_count == last_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
                last_count = current_count

            if stable_rounds >= 2:
                break

        if last_count == 0:
            raise Exception("Nessun item trovato nella wishlist")

        log(f"Trovati {last_count} prodotti totali")

        items = []
        for row in rows:
            title_el = row.select_one("a.a-link-normal, a.a-text-normal")
            if not title_el:
                continue

            name = title_el.get("title", "").strip() or title_el.get_text(strip=True)
            url = title_el.get("href", "")
            if not url.startswith("http"):
                url = "https://www.amazon.it" + url

            items.append((name, url))

        results = []
        for name, url in items:
            log(f"Elaborazione {name}")
            price = get_product_price(page, url, name)
            if price:
                results.append((name, price, url))

        browser.close()
        return results

# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
def main():
    log("===== INIZIO MONITOR =====")
    log(f"THRESHOLD: {THRESHOLD}%")

    old = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            try:
                old = json.load(f)
                log(f"Caricati {len(old)} prodotti dallo storico.")
            except:
                old = {}

    try:
        items = get_items()
    except Exception as e:
        log("ERRORE CRITICO durante get_items()")
        log(str(e))
        return

    new = {}
    alerts = []
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d %H:%M:%S")

    for raw_name, price, url in items:
        name = normalize(raw_name)
        log(f"Prezzo attuale {name}: €{price:.2f}")

        asin = extract_asin(url)
        camel = f"https://it.camelcamelcamel.com/product/{asin}" if asin else "ASIN non trovato"

        if name not in old:
            new[name] = {
                "current": price,
                "history": [
                    {"date": today, "price": price}
                ]
            }
            continue

        old_history = old[name].get("history", [])
        history = list(old_history)

        last_price = history[-1]["price"] if history else None
        old_current = old[name].get("current")

        new[name] = {
            "current": price,
            "history": history
        }

        if last_price != price:
            new[name]["history"].append({
                "date": today,
                "price": price
            })
            log(f"  Prezzo cambiato per {name}: {last_price} → {price}")

        if old_current and old_current > 0:
            drop = ((old_current - price) / old_current) * 100
            if drop >= THRESHOLD:
                alerts.append(
                    f"{name}\n"
                    f"Vecchio: €{old_current:.2f}\n"
                    f"Nuovo: €{price:.2f}\n"
                    f"↓ {drop:.1f}%\n"
                    f"CamelCamelCamel: {camel}\n"
                    f"Amazon: {url}"
                )

    with open(DATA_FILE, "w") as f:
        json.dump(new, f, indent=2)
        log("Prezzi aggiornati salvati.")

    if alerts:
        send_email("\n\n".join(alerts))
    else:
        log("Nessun alert generato.")

    log("===== FINE MONITOR =====")

if __name__ == "__main__":
    main()
