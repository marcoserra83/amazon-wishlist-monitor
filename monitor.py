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
WISHLIST_URL = os.getenv("WISHLIST_URL")

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
        if link and link.startswith("/"):
            link = "https://www.amazon.it" + link

        return {
            "title": title,
            "price": price,
            "link": link
        }
    except Exception as e:
        print(f"Errore estrazione prodotto: {e}")
        return None

# ---------------------------------------------------------
# SCRAPER
# ---------------------------------------------------------
def run_scraper():
    previous_prices = load_previous_prices()
    updated_prices = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
            locale="it-IT",
            timezone_id="Europe/Rome"
        )

        # ---------------------------------------------------------
        # STEALTH MODE (completo)
        # ---------------------------------------------------------
        context.add_init_script("""
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
};

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
        """)

        page = context.new_page()

        print(f"Caricamento wishlist: {WISHLIST_URL}")
        page.goto(WISHLIST_URL, timeout=60000)

        page.wait_for_timeout(5000)

        items = page.query_selector_all("div.g-item-sortable")
        print(f"Trovati {len(items)} elementi nella wishlist")

        for item in items:
            info = extract_product_info(item)
            if not info:
                continue

            title = info["title"]
            price = info["price"]
            link = info["link"]

            updated_prices[title] = {
                "price": price,
                "link": link
