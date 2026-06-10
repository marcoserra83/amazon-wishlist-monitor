from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime

WISHLIST_URL = "https://www.amazon.it/hz/wishlist/ls/3UN1OP09AA54H?ref_=wl_share"

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_PASS = os.environ["GMAIL_APP_PASSWORD"]
THRESHOLD = float(os.environ.get("ALERT_THRESHOLD", 10))

DATA_FILE = "prices.json"
DEBUG_DIR = "debug_output"


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


def save_debug_output(html, screenshot_path=None):
    """Salva l'HTML e lo screenshot per debug"""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Salva HTML
    html_path = os.path.join(DEBUG_DIR, f"page_{timestamp}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[DEBUG] HTML salvato: {html_path}")
    
    # Salva screenshot se fornito
    if screenshot_path:
        import shutil
        dest_path = os.path.join(DEBUG_DIR, f"screenshot_{timestamp}.png")
        shutil.copy(screenshot_path, dest_path)
        print(f"[DEBUG] Screenshot salvato: {dest_path}")


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
        
        # Salva screenshot
        screenshot_path = "/tmp/wishlist_screenshot.png"
        page.screenshot(path=screenshot_path)
        
        browser.close()
    
    # Salva debug output
    save_debug_output(html, screenshot_path)
    
    soup = BeautifulSoup(html, "lxml")
    items = []

    print("[DEBUG] Parsing degli item...")
    for idx, row in enumerate(soup.select("li.g-item-sortable")):
        title_elem = row.select_one("a.a-link-normal")
        
        if not title_elem:
            print(f"[DEBUG] Item #{idx}: Nessun titolo trovato, skip")
            continue

        name = title_elem.get("title", "").strip()
        if not name:
            name = "PRODOTTO_SENZA_NOME"

        print(f"\n[DEBUG] ===== Item #{idx}: {name} =====")

        # Prova a trovare il prezzo ufficiale di Amazon
        price_elem = None
        
        # Prima strategia: cerca il prezzo principale
        price_elem = row.select_one(".a-price .a-offscreen")
        
        if not price_elem:
            print(f"[DEBUG] Item #{idx}: Nessun prezzo trovato, skip")
            print(f"[DEBUG] HTML dell'item:\n{row}")
            continue

        try:
            price_text = price_elem.get_text(strip=True)
            print(f"[DEBUG] Item #{idx}: Prezzo grezzo trovato: '{price_text}'")
            
            # Stampa il contenitore del prezzo per debug
            price_container = row.select_one(".a-price")
            if price_container:
                print(f"[DEBUG] Item #{idx}: Prezzo container HTML:\n{price_container}")
            
            # Estrai il valore numerico
            current_price = float(
                price_text
                .replace("€", "")
                .replace(",", ".")
                .replace(" ", "")
                .strip()
            )
            
            if current_price <= 0:
                print(f"[DEBUG] Item #{idx}: Prezzo non valido ({current_price}), skip")
                continue
            
            print(f"[DEBUG] Item #{idx}: Prezzo finale estratto = €{current_price:.2f} ✓")
            items.append((name, current_price))
            
        except Exception as e:
            print(f"[DEBUG] Item #{idx}: Errore nel parsing del prezzo '{price_text}' - {e}")
            print(f"[DEBUG] Item #{idx}: HTML completo dell'item:\n{row}")
            continue

    print(f"\n[DEBUG] TOTALE ITEM TROVATI: {len(items)}")
    for name, price in items:
        print(f"[DEBUG]   - {name}: €{price:.2f}")

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
                print(f"[DEBUG]   ❌ Prezzo precedente non valido")
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
