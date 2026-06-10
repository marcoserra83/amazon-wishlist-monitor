from playwright.sync_api import sync_playwright
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
        print(f"[DEBUG] {DATA_FILE} non esiste, ritorno dict vuoto")
        return {}

    with open(DATA_FILE, "r") as f:
        old_prices = json.load(f)
        print(f"[DEBUG] Prezzi precedenti caricati: {json.dumps(old_prices, indent=2)}")
        return old_prices


def save_prices(data):
    print(f"[DEBUG] Salvataggio prezzi: {json.dumps(data, indent=2)}")
    with open(DATA_FILE, "w") as f:
        json.dump(data, f)
    print("[DEBUG] Prezzi salvati con successo")


def get_items():
    print(f"[DEBUG] Apertura wishlist con Playwright: {WISHLIST_URL}")
    
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.set_viewport_size({"width": 1920, "height": 1080})
        
        print("[DEBUG] Navigazione alla pagina...")
        page.goto(WISHLIST_URL, wait_until="networkidle")
        
        print("[DEBUG] Attesa caricamento prezzi...")
        # Attendi che almeno un prezzo sia visibile
        page.wait_for_selector(".a-price .a-offscreen", timeout=10000)
        
        # Attendi un po' extra per essere sicuri che tutto sia caricato
        page.wait_for_timeout(2000)
        
        print("[DEBUG] Estrazione contenuto HTML...")
        html = page.content()
        browser.close()
    
    soup = BeautifulSoup(html, "lxml")
    items = []

    print("[DEBUG] Parsing degli item...")
    for idx, row in enumerate(soup.select("li.g-item-sortable")):
        price = row.select_one(".a-price .a-offscreen")
        title = row.select_one("a.a-link-normal")

        if not price:
            print(f"[DEBUG] Item #{idx}: Nessun prezzo trovato, skip")
            continue

        name = ""

        if title:
            name = title.get("title", "").strip()

        if not name:
            name = "PRODOTTO_SENZA_NOME"

        try:
            price_text = price.get_text(strip=True)
            print(f"[DEBUG] Item #{idx}: {name} - Prezzo grezzo: '{price_text}'")
            
            current_price = float(
                price_text
                .replace("€", "")
                .replace(",", ".")
                .strip()
            )
            print(f"[DEBUG] Item #{idx}: {name} = €{current_price:.2f}")
        except Exception as e:
            print(f"[DEBUG] Item #{idx}: Errore nel parsing del prezzo '{price_text}' - {e}")
            continue

        items.append((name, current_price))

    print(f"[DEBUG] TOTALE ITEM TROVATI: {len(items)}")
    print(f"[DEBUG] ITEM TROVATI: {items}")

    return items


def main():
    print("[DEBUG] ===== INIZIO MONITOR =====")
    print(f"[DEBUG] THRESHOLD: {THRESHOLD}%")
    
    old = load_old_prices()
    new = {}
    alerts = []

    items = get_items()

    for name, price in items:
        new[name] = price
        print(f"\n[DEBUG] Elaborazione: {name}")
        print(f"[DEBUG]   Prezzo attuale: €{price:.2f}")

        if name in old:
            old_price = old[name]
            print(f"[DEBUG]   Prezzo precedente: €{old_price:.2f}")

            if old_price > 0:
                drop = ((old_price - price) / old_price) * 100
                print(f"[DEBUG]   Sconto calcolato: {drop:.1f}%")

                if drop >= THRESHOLD:
                    alert_msg = (
                        f"{name}\n"
                        f"Vecchio: €{old_price:.2f}\n"
                        f"Nuovo: €{price:.2f}\n"
                        f"↓ {drop:.1f}%"
                    )
                    print(f"[DEBUG]   ✅ ALERT GENERATO!")
                    alerts.append(alert_msg)
                else:
                    print(f"[DEBUG]   ❌ Sconto insufficiente (< {THRESHOLD}%)")
        else:
            print(f"[DEBUG]   Nuovo prodotto, nessun confronto")

    save_prices(new)

    if alerts:
        print(f"\n[DEBUG] Invio email con {len(alerts)} alert(s)")
        send_email("\n\n".join(alerts))
        print("[DEBUG] Email inviata!")
    else:
        print("[DEBUG] Nessun alert da inviare")

    print("[DEBUG] ===== FINE MONITOR =====")


if __name__ == "__main__":
    main()
