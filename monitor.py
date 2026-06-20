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
    path = os.path.join(LOG_DIR, datetime.now().strftime("%Y-%m-%d") + ".log")
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
#  PRICE PARSING (VERSIONE AFFIDABILE)
# ---------------------------------------------------------
def parse_price_from_html(html: str) -> float | None:
    soup = BeautifulSoup(html, "lxml")

    # 1) Prezzo principale Amazon (il più affidabile)
    core = soup.select_one("#corePriceDisplay_desktop_feature_div .a-offscreen")
    if core:
        txt = core.get_text(strip=True)
        txt = txt.replace("€", "").replace(",", ".").strip()
        try:
            return float(txt)
        except:
            pass

    # 2) JSON interno Amazon
    for script in soup.find_all("script"):
        if script.string and "price" in script.string.lower():
            match = re.search(r'"price"\s*:\s*"(\d+[.,]\d+)"', script.string)
            if match:
                try:
                    return float(match.group(1).replace(",", "."))
                except:
                    pass

    # 3) .a-offscreen generico
    elem = soup.select_one(".a-price .a-offscreen")
    if elem:
        txt = elem.get_text(strip=True)
        txt = txt.replace("€", "").replace(",", ".").strip()
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

