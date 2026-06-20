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
# EMAIL
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
# LOGGING
# ---------------------------------------------------------
def log(msg: str):
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, datetime.now().strftime("%Y-%m-%d") + ".log")
    with open(path, "a", encoding="utf-8") as f:
        f.write("[" + datetime.now().strftime("%H:%M:%S") + "] " + msg + "\n")
    print(msg)


def save_debug_file(name: str, content: str):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(DEBUG_DIR, name + "_" + ts + ".html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------
# PRICE PARSER
# ---------------------------------------------------------
def parse_price_from_html(html: str) -> float | None:
    soup = BeautifulSoup(html, "lxml")

    # 1) Core price
    core = soup.select_one("#corePriceDisplay_desktop_feature_div .a-offscreen")
    if core:
        txt = core.get_text(strip=True).replace("€", "").replace(",", ".")
        try:
            return float(txt)
        except:
            pass

    # 2) JSON interno Amazon
    for script in soup.find_all("script"):
        if not script.string:
            continue
        text = script.string
        if "price" not in text.lower():
            continue

        match = re.search(r'"price"\s*:\s*"(\d+[.,]\d+)"', text)
        if match:
            try:
                return float(match.group(1).replace(",", "."))
            except:
                pass

    # 3) .a-offscreen generico
    off = soup.select_one(".a-price .a-offscreen")
    if off:
        txt = off.get_text(strip=True).replace("€", "").replace(",", ".")
        try:
            return float(txt)
        except:
            pass

    # 4) whole + fraction
    whole = soup.select_one(".a-price-whole")
    frac = soup.select_one(".a-price-fraction")
    if whole and frac:
        w = whole.get_text(strip=True).replace(".", "")
        f = frac.get_text(strip=True)
        try:
            return float(w + "." + f)
        except:
            pass

    # 5) fallback regex
    match = re.search(r'(\d+[.,]\d{2})\s*€', html)
    if match:
        try:
            return float(match.group(1).replace(",", "."))
        except:
            pass

    return None


# ---------------------------------------------------------
# STEALTH MODE
# ---------------------------------------------------------
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

window.chrome = { runtime: {} };

Object.defineProperty(navigator, 'plugins', {
    get: () => [1, 2, 3],
});

Object.defineProperty(navigator, 'languages', {
    get: () => ['it-IT', 'it'],
});

Object.defineProperty(navigator, 'platform', {
    get: () => 'Win32',
});

Object.defineProperty(navigator, 'hardwareConcurrency', {
    get: () => 8,
});

Object.defineProperty(navigator, 'deviceMemory', {
    get: () => 8,
});

Object.defineProperty(navigator, 'maxTouchPoints', {
    get: () => 0,
});

const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters)
);

const getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) return 'Intel Inc.';
    if (parameter === 37446) return 'Intel Iris OpenGL Engine';
    return getParameter(parameter);
};
"""


# ---------------------------------------------------------
# SCRAPING PREZZO
# ---------------------------------------------------------
def get_product_price(page: Page, url: str, name: str) -> float | None:
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            log(f"[{name}] Tentativo {attempt}/{RETRY_COUNT}")
            page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_PAGE)

            html = page.content()

            if "captcha" in html.lower():
                log(f"[{name}] CAPTCHA rilevato")
                save_debug_file("captcha_" + name, html)
                return None

            try:
                page.wait_for_selector(".a-price, .a-offscreen", timeout=TIMEOUT_SELECTOR)
            except:
                log(f"[{name}] Nessun prezzo nel DOM")
                continue

            html = page.content()
            price = parse_price_from_html(html)

            if price and price > 0:
                log(f"[{name}] Prezzo estratto: €{price:.2f}")
                return price

            log(f"[{name}] Prezzo non valido, retry…")

        except Exception as e:
            log(f"[{name}] Errore: {e}")
            log(traceback.format_exc())

        time.sleep(1)

    log(f"[{name}] Fallimento estrazione prezzo")
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
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
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

        while True:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
            page.wait_for_timeout(1200)

            html = page.content()
            soup = BeautifulSoup(html, "lxml")

            rows = soup.select("div.g-item-sortable, [data-itemid]")
            current_count = len(rows)

            log(f"Wishlist: {current_count} item visibili")

            if current_count == last_count:
                stable_rounds += 1
            else:
                stable_rounds = 0
                last_count = current_count

            if stable_rounds >= 3:
                break

        save_debug_file("final_wishlist", html)

        if last_count == 0:
            raise Exception("Nessun item trovato nella wishlist")

        log(f"Trovati {last_count} prodotti totali")

        items = []

        for idx, row in enumerate(rows):
            title_el = row.select_one("a.a-link-normal, a.a-text-normal")
            if not title_el:
                continue

            name = title_el.get("title", "").strip() or title_el.get_text(strip=True)
            if not name:
                name = "PRODOTTO_SENZA_NOME"

            url = title_el.get("href", "")
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
# MAIN
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
        log(f"Elaborazione {name}: €{price:.2f}")

        if name in old:
            old_price = old[name]
            if old_price > 0:
                drop = ((old_price - price) / old_price) * 100
                log(f"Sconto: {drop:.1f}%")

                if drop >= THRESHOLD:
                    alerts.append(
                        f"{name}\n"
                        f"Vecchio: €{old_price:.2f}\n"
                        f"Nuovo: €{price:.2f}\n"
                        f"↓ {drop:.1f}%"
                    )

    with open(DATA_FILE, "w") as f:
        json.dump(new, f, indent=2)

    if alerts:
        send_email("\n\n".join(alerts))
        log(f"Inviati {len(alerts)} alert")
    else:
        log("Nessun alert")

    log("===== FINE MONITOR =====")


if __name__ == "__main__":
    main()
