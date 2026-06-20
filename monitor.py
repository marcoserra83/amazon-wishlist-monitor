from playwright.sync_api import sync_playwright, Page
from bs4 import BeautifulSoup
import json
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import time
import traceback
import re
from concurrent.futures import ThreadPoolExecutor

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

TIMEOUT_PAGE = 20000
TIMEOUT_SELECTOR = 2000
RETRY_COUNT = 2
DELAY_BETWEEN_PRODUCTS = 0.3
WORKERS = 8


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

    safe_name = re.sub(r'[<>:"/\\|?*]', "_", name)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(DEBUG_DIR, f"{safe_name}_{ts}.html")

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
# PRICE PARSER
# ---------------------------------------------------------
def parse_price_from_html(html: str) -> float | None:
    soup = BeautifulSoup(html, "lxml")

    for bad in soup.select("#sims-consolidated-2, #sims-consolidated-3, #sp_detail, .a-carousel"):
        bad.decompose()

    price_block = soup.select_one(
        "#corePrice_feature_div, "
        "#apex_desktop, "
        "#corePriceDisplay_desktop_feature_div"
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
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_PAGE)
            html = page.content()

            if "captcha" in html.lower():
                save_debug_file(f"captcha_{name}", html)
                return None

            try:
                page.wait_for_selector(
                    ".a-price, .a-offscreen, #corePrice_feature_div, #twister-plus-price-data-price",
                    timeout=TIMEOUT_SELECTOR
                )
            except:
                return None

            html = page.content()
            price = parse_price_from_html(html)

            if price and price > 0:
                return price

            soup = BeautifulSoup(html, "lxml")
            if not (
                soup.select_one("#corePrice_feature_div")
                or soup.select_one(".a-price")
                or soup.select_one(".a-offscreen")
                or soup.select_one("#twister-plus-price-data-price")
            ):
                return None

        except:
            pass

    return None


# ---------------------------------------------------------
# WORKER PARALLELO
# ---------------------------------------------------------
def worker_process(context, chunk, worker_id):
    page = context.new_page()
    results = []

    for name, url in chunk:
        log(f"[Worker {worker_id}] → {name}")
        price = get_product_price(page, url, name)
        if price:
            results.append((name, price))

    page.close()
    return results


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
                      if route.request.resource_type in ["image", "font"]
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

        save_debug_file("final_wishlist", html)

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

        # --- PARALLELIZZAZIONE ---
        def chunk_list(lst, n):
            return [lst[i::n] for i in range(n)]

        chunks = chunk_list(items, WORKERS)
        log(f"Avvio parallelizzazione con {WORKERS} worker…")

        results = []
        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = [
                executor.submit(worker_process, context, chunk, i+1)
                for i, chunk in enumerate(chunks)
            ]

            for f in futures:
                results.extend(f.result())

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
