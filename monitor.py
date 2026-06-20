from playwright.sync_api import sync_playwright, Page
from bs4 import BeautifulSoup
import json
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import re
import time
import traceback

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------
WISHLIST_URL = "https://www.amazon.it/hz/wishlist/ls/3UN1OP09AA54H?ref_=wl_share"

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_PASS = os.environ["GMAIL_APP_PASSWORD"]
THRESHOLD = float(os.environ.get("ALERT_THRESHOLD", 10))

DATA_FILE = "prices.json"
LOG_DIR = "logs"
DEBUG_DIR = "debug_output"

TIMEOUT_PAGE = 60000
TIMEOUT_SELECTOR = 30000
RETRY_COUNT = 3
DELAY_BETWEEN_PRODUCTS = 1.2


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


def save_debug_file(name: str, content: str):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(DEBUG_DIR, f"{name}_{ts}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


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
# PRICE PARSER (VERBOSO)
# ---------------------------------------------------------
def parse_price_from_html(html: str) -> float | None:
    soup = BeautifulSoup(html, "lxml")

    # 1️⃣ Rimuovo caroselli e prodotti simili che contengono prezzi fuorvianti
    log("Pulizia caroselli e prodotti simili")
    for bad in soup.select("#sims-consolidated-2, #sims-consolidated-3, #sp_detail, .a-carousel"):
        bad.decompose()

    # 2️⃣ Cerco il blocco del prezzo principale
    log("Parsing prezzo: tentativo 1 → blocco prezzo principale")
    price_block = soup.select_one(
        "#corePrice_feature_div, "
        "#apex_desktop, "
        "#corePriceDisplay_desktop_feature_div"
    )

    if price_block:
        offscreen = price_block.select_one("span.a-price > span.a-offscreen")
        if offscreen:
            try:
                val = float(offscreen.get_text(strip=True).replace("€", "").replace(",", "."))
                log(f"Prezzo trovato (blocco principale): {val}")
                return val
            except:
                log("Errore parsing blocco principale")

    # 3️⃣ Fallback controllato: cerca solo prezzi validi, non in tutta la pagina
    log("Parsing prezzo: tentativo 2 → fallback controllato")
    fallback = soup.select_one("span.a-price > span.a-offscreen")
    if fallback:
        try:
            val = float(fallback.get_text(strip=True).replace("€", "").replace(",", "."))
            log(f"Prezzo trovato (fallback controllato): {val}")
            return val
        except:
            log("Errore parsing fallback controllato")

    # 4️⃣ Ricostruzione whole + fraction
    log("Parsing prezzo: tentativo 3 → whole + fraction")
    whole = soup.select_one("span.a-price-whole")
    frac = soup.select_one("span.a-price-fraction")
    if whole and frac:
        try:
            val = float(whole.get_text(strip=True).replace(".", "") + "." + frac.get_text(strip=True))
            log(f"Prezzo trovato (whole+fraction): {val}")
            return val
        except:
            log("Errore parsing whole+fraction")

    log("❌ Nessun prezzo trovato (regex disattivato per evitare prezzi fantasma)")
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
    log(f"Avvio scraping prezzo per: {name}")
    for attempt in range(1, RETRY_COUNT + 1):
        log(f"[{name}] Tentativo {attempt}/{RETRY_COUNT}")

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_PAGE)
            html = page.content()

            if "captcha" in html.lower():
                log(f"[{name}] CAPTCHA rilevato")
                save_debug_file(f"captcha_{name}", html)
                return None

            try:
                page.wait_for_selector(".a-price, .a-offscreen", timeout=TIMEOUT_SELECTOR)
            except:
                log(f"[{name}] Nessun prezzo nel DOM, retry…")
                continue

            html = page.content()
            price = parse_price_from_html(html)

            if price and price > 0:
                log(f"[{name}] Prezzo estratto correttamente: €{price:.2f}")
                return price

            log(f"[{name}] Prezzo non valido, retry…")

        except Exception as e:
            log(f"[{name}] Errore durante scraping: {e}")
            log(traceback.format_exc())

        time.sleep(1)

    log(f"[{name}] ❌ Fallimento estrazione prezzo")
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

        context.add_init_script(STEALTH_JS)

        context.route("**/*", lambda route: route.abort()
                      if route.request.resource_type in ["image", "font", "stylesheet"]
                      else route.continue_())

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
            page.wait_for_timeout(1200)

            html = page.content()
            soup = BeautifulSoup(html, "lxml")

            rows = soup.select("div.g-item-sortable, [data-itemid]")
            current_count = len(rows)

            log(f"→ Elementi visibili: {current_count}")

            if current_count == last_count:
                stable_rounds += 1
                log(f"→ Nessun nuovo elemento (round {stable_rounds}/3)")
            else:
                stable_rounds = 0
                last_count = current_count

            if stable_rounds >= 3:
                log("Scroll completato.")
                break

        save_debug_file("final_wishlist", html)

        if last_count == 0:
            raise Exception("Nessun item trovato nella wishlist")

        log(f"Trovati {last_count} prodotti totali")

        items = []

        for idx, row in enumerate(rows):
            title_el = row.select_one("a.a-link-normal, a.a-text-normal")
            if not title_el:
                log("Elemento senza titolo, ignorato.")
                continue

            name = title_el.get("title", "").strip() or title_el.get_text(strip=True)
            url = title_el.get("href", "")
            if not url.startswith("http"):
                url = "https://www.amazon.it" + url

            log(f"→ Analisi prodotto: {name}")

            price = get_product_price(page, url, name)
            if price:
                items.append((name, price))

            if idx < len(rows) - 1:
                time.sleep(DELAY_BETWEEN_PRODUCTS)

        browser.close()
        return items


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------
def main():
    log("===== INIZIO MONITOR =====")
    log(f"THRESHOLD: {THRESHOLD}%")

    old = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            old = json.load(f)
            log(f"Caricati {len(old)} prezzi precedenti.")

    try:
        items = get_items()
    except Exception as e:
        log("ERRORE CRITICO durante get_items()")
        log(str(e))
        send_email("❌ Errore monitor Amazon:\n" + str(e))
        return

    new = {}
    alerts = []

    for name, price in items:
        new[name] = price
        log(f"Elaborazione {name}: prezzo attuale €{price:.2f}")

        if name in old:
            old_price = old[name]
            drop = ((old_price - price) / old_price) * 100 if old_price > 0 else 0
            log(f"→ Sconto rilevato: {drop:.1f}%")

            if drop >= THRESHOLD:
                alerts.append(
                    f"{name}\n"
                    f"Vecchio: €{old_price:.2f}\n"
                    f"Nuovo: €{price:.2f}\n"
                    f"↓ {drop:.1f}%"
                )

    with open(DATA_FILE, "w") as f:
        json.dump(new, f, indent=2)
        log("Prezzi aggiornati salvati.")

    if alerts:
        log(f"Invio di {len(alerts)} alert…")
        send_email("\n\n".join(alerts))
    else:
        log("Nessun alert generato.")

    log("===== FINE MONITOR =====")


if __name__ == "__main__":
    main()
