import requests
from bs4 import BeautifulSoup
import json
import os
import smtplib
from email.mime.text import MIMEText

WISHLIST_URL = "https://www.amazon.it/hz/wishlist/ls/3UN1OP09AA54H?ref_=wl_share"

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_PASS = os.environ["GMAIL_APP_PASSWORD"]
THRESHOLD = float(os.environ.get("ALERT_THRESHOLD", 10))

DATA_FILE = "prices.json"


def send_email(body):
    msg = MIMEText(body)
    msg["Subject"] = "📉 Amazon Wishlist Price Drop"
    msg["From"] = GMAIL_USER
    msg["To"] = GMAIL_USER

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_PASS)
        server.send_message(msg)


def load_old_prices():
    if not os.path.exists(DATA_FILE):
        return {}

    with open(DATA_FILE, "r") as f:
        return json.load(f)


def save_prices(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)


def get_items():
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    r = requests.get(WISHLIST_URL, headers=headers)
    soup = BeautifulSoup(r.text, "lxml")

    items = []

    for row in soup.select("li.g-item-sortable"):
        price = row.select_one(".a-price .a-offscreen")
        title = row.select_one("a.a-link-normal")

        if not price:
            continue

        name = ""

        if title:
            name = title.get("title", "").strip()

        if not name:
            name = "PRODOTTO_SENZA_NOME"

        try:
            current_price = float(
                price.get_text(strip=True)
                .replace("€", "")
                .replace(",", ".")
            )
        except:
            continue

        items.append((name, current_price))

    print("ITEM TROVATI:", items)

    return items


def main():
    old        json.dump(data, f)


def get_items():
    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    r = requests.get(WISHLIST_URL, headers=headers)

    print("STATUS:", r.status_code)
    print("===== INIZIO HTML =====")
    print(r.text[:2000])
    print("===== FINE HTML =====")

    soup = BeautifulSoup(r.text, "lxml")

    items = []

    for row in soup.select("li.g-item-sortable"):
        price = row.select_one(".a-price .a-offscreen")

        if not price:
            continue

        print("ROW HTML:")
        print(str(row)[:1500])
        print("-----")

        name = "PRODOTTO_NON_TROVATO"

        try:
            current_price = float(
                price.get_text(strip=True)
                .replace("€", "")
                .replace(",", ".")
            )
        except:
            continue

        items.append((name, current_price))

    print("ITEM TROVATI:", items)

    return items

def main():
    old = load_old_prices()
    new = {}
    alerts = []

    items = get_items()

    for name, price in items:
        new[name] = price

        if name in old:
            old_price = old[name]
            drop = ((old_price - price) / old_price) * 100

            if drop >= THRESHOLD:
                alerts.append(
                    f"{name}\nVecchio: €{old_price:.2f}\nNuovo: €{price:.2f}\n↓ {drop:.1f}%\n"
                )

    save_prices(new)

    if alerts:
        send_email("\n\n".join(alerts))


if __name__ == "__main__":
    main()
