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
DELAY_BETWEEN_PRODUCTS = 1.5


# ---------------------------------------------------------
#  EMAIL
# ---------------------------------------------------------
def send_email(body: str):
    msg = MIMEText(body)
    msg["Subject"] = "📉 Amazon Wishlist Price Drop"
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.send_message(msg)


# ---------------------------------------------------------
#  LOGGING
# ---------------------------------------------------------
def log(msg: str):
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, f"{datetime.now().strftime('%Y-%m-%d')}.log")
    with open(path, "a", encoding="utf-8") as f:
        f.write("[" + datetime.now().strftime("%H:%M:%S") + "] " + msg + "\n")
    print(msg)


def save_debug(html: str, prefix: str, screenshot_path: str | None = None):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    html_path = os.path.join(DEBUG_DIR, prefix + "_" + ts + ".html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    if screenshot_path:
        import shutil
        dest = os.path.join(DEBUG_DIR, prefix + "_" + ts + ".png")
        shutil.copy(screenshot_path, dest)


# ---------------------------------------------------------
#  PRICE PARSING
# ---------------------------------------------------------
def parse_price_from_html(html: str) -> float | None:
    soup = BeautifulSoup(html, "lxml")

    # JSON interno Amazon
    for script in soup.find_all("script"):
        if script.string and "price" in script.string.lower():
            match = re.search(r'"price"\s*:\s*"(\d+[.,]\d+)"', script.string)
            if match:
                return float(match.group(1).replace(",", "."))

    # .a-offscreen
    elem = soup.select_one(".a-price .a-offscreen")
    if elem:
        txt = elem.get_text(strip=True)
        txt = txt.replace("€", "").replace(",", ".").strip()
        return float(txt)

    # whole + fraction
    whole = soup.select_one(".a-price-whole")
    frac = soup.select_one(".a-price-fraction")
    if whole and frac:
        w = whole.get_text(strip=True).replace(".", "")
        f = frac.get_text(strip=True)
        return float(w + "." + f)

    # fallback
    match = re.search(r'(\d+[.,]\d{2})\s*€', html)
    if match:
        return float(match.group(1).replace(",", "."))

    return None


# ---------------------------------------------------------
#  SCRAPING PRODOTTO
# ---------------------------------------------------------
def get_product_price(page: Page, url: str, name: str) -> float | None:
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            log("[" + name + "] Tentativo " + str(attempt) + "/" + str(RETRY_COUNT))
            page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_PAGE)

            html = page.content()
            if "captcha" in html.lower():
                log("[" + name + "] CAPTCHA rilevato")
                save_debug(html, "captcha_" + name)
                return None

            try:
                page.wait_for_selector(".a-price, .a-offscreen", timeout=TIMEOUT_SELECTOR)
            except:
                log("[" + name + "] Nessun prezzo nel DOM")
                continue

            html = page.content()
            price = parse_price_from_html(html)

            if price and price > 0:
                log("[" + name + "] Prezzo estratto: €" + format(price, ".2f"))
                return price

            log("[" + name + "] Prezzo non valido, retry…")

        except Exception as e:
            log("[" + name + "] Errore: " + str(e))
            log(traceback.format_exc())

        time.sleep(1)

    log("[" + name + "] Fallimento estrazione prezzo")
    return None


# ---------------------------------------------------------
#  SCRAPING WISHLIST (scroll infinito)
# ---------------------------------------------------------
def get_items():
    log("Apertura Playwright…")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"]
        )

        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            viewport={"width": 1920, "height": 1080},
            locale="it-IT",
            bypass_csp=True
        )

        context.route("**/*", lambda route: route.abort()
                      if route.request.resource_type in ["image", "font", "stylesheet"]
                      else route.continue_())

        page = context.new_page()
        page.set_default_timeout(TIMEOUT_PAGE)

        page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => false});")

        log("Caricamento wishlist…")
        page.goto(WISHLIST_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        last_count = 0
        stable_rounds = 0

        while True:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            page.wait_for_timeout(1500)

            html = page.content()
            soup = BeautifulSoup(html, "lxml")
            rows = soup.select("li.g-item-sortable")
            current_count = len(rows)

            log("Wishlist: " + str(current_count) + " item visibili")

            if current_count == last_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
                last_count = current_count

            if stable_rounds >= 3:
                break

        if last_count == 0:
            save_debug(html, "wishlist_empty")
            raise Exception("Nessun item trovato nella wishlist")

        log("Trovati " + str(last_count) + " prodotti totali")

        items = []

        for idx, row in enumerate(rows):
            title = row.select_one("a.a-link-normal")
            if not title:
                continue

            name = title.get("title", "").strip()
            if not name:
                name = "PRODOTTO_SENZA_NOME"

            url = title.get("href", "")
            if not url.startswith("http"):
                url = "https://www.amazon.it" + url

            log("→ " + name)

            price = get_product_price(page, url, name)
            if price:
                items.append((name, price))

            if idx < len(rows) - 1:
                time.sleep(DELAY_BETWEEN_PRODUCTS)

        browser.close()
        return items


# ---------------------------------------------------------
#  MAIN
# ---------------------------------------------------------
def main():
    log("===== INIZIO MONITOR =====")
    log("THRESHOLD: " + str(THRESHOLD))

    old = {}
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r") as f:
            old = json.load(f)

    try:
        items = get_items()
    except Exception as e:
        log("ERRORE CRITICO: " + str(e))
        send_email("❌ Errore monitor Amazon:\n" + str(e))
        return

    new = {}
    alerts = []

    for name, price in items:
        new[name] = price
        log("Elaborazione " + name + ": €" + format(price, ".2f"))

        if name in old:
            old_price = old[name]
            if old_price > 0:
                drop = ((old_price - price) / old_price) * 100
                log("Sconto: " + format(drop, ".1f") + "%")

                if drop >= THRESHOLD:
                    alert_msg = (
                        name + "\n"
                        "Vecchio: €" + format(old_price, ".2f") + "\n"
                        "Nuovo: €" + format(price, ".2f") + "\n"
                        "↓ " + format(drop, ".1f") + "%"
                    )
                    alerts.append(alert_msg)

    with open(DATA_FILE, "w") as f:
        json.dump(new, f, indent=2)

    if alerts:
        send_email("\n\n".join(alerts))
        log("Inviati " + str(len(alerts)) + " alert")
    else:
        log("Nessun alert")

    log("===== FINE MONITOR =====")


if __name__ == "__main__":
    main()
