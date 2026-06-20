import os
import json
import time
import smtplib
from email.mime.text import MIMEText
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------
# CONFIG
# ---------------------------------------------------------
ALERT_THRESHOLD = float(os.getenv("ALERT_THRESHOLD", "0"))
GMAIL_USER = os.getenv("GMAIL_USER")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

# 👉 WISHLIST FISSA, SCRITTA NEL CODICE
WISHLIST_URL = "https://www.amazon.it/hz/wishlist/ls/3UN1OP09AA54H?ref_=wl_share"

# ---------------------------------------------------------
# EMAIL
# ---------------------------------------------------------
def send_email_alert(product_name, old_price, new_price, discount):
    body = (
        f"Prodotto: {product_name}\n"
        f"Prezzo precedente: €{old_price}\n"
        f"Prezzo attuale: €{new_price}\n"
        f"Sconto: {discount}%\n"
    )
    msg = MIMEText(body)
    msg["Subject"] = f"🔥 Sconto rilevato: {product_name}"
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_USER, GMAIL_USER, msg.as_string())

# ---------------------------------------------------------
# STORAGE
# ---------------------------------------------------------
def load_previous_prices():
    if not os.path.exists("prices.json"):
        return {}
    with open("prices.json", "r", encoding="utf-8") as f:
        return json.load(f)

def save_prices(prices):
    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(prices, f, indent=2, ensure_ascii=False)

# ---------------------------------------------------------
# PARSING
# ---------------------------------------------------------
def parse_price(text):
    if not text:
        return None
    text = text.replace("€", "").replace(",", ".").strip()
    try:
        return float(text)
    except:
        return None

def extract_product_info(item):
    try:
        title_el = item.query_selector("a.a-link-normal")
        title = title_el.inner_text().strip() if title_el else "Senza titolo"

        price_el = item.query_selector("span.a-offscreen")
        price = parse_price(price_el.inner_text()) if price_el else None

        link_el = item.query_selector("a.a-link-normal")
        link = link_el.get_attribute("href") if link_el else None
        if link and link.startswith
