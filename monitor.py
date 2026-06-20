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
        try:
            return float(core.get_text(strip=True).replace("€", "").replace(",", "."))
        except:
            pass

    # 2) JSON interno
    for script in soup.find_all("script"):
        if script.string and "price" in script.string.lower():
            match = re.search(r'"price"\s*:\s*"(\
