from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
import json
import os
import smtplib
from email.mime.text import MIMEText
from datetime import datetime
import re
import time

WISHLIST_URL = "https://www.amazon.it/hz/wishlist/ls/3UN1OP09AA54H?ref_=wl_share"

GMAIL_USER = os.environ["GMAIL_USER"]
GMAIL_PASS = os.environ["GMAIL_APP_PASSWORD"]
THRESHOLD = float(os.environ.get("ALERT_THRESHOLD", 10))

DATA_FILE = "prices.json"
DEBUG_DIR = "debug_output"

# Parametri di timeout e retry
TIMEOUT_PAGE_LOAD = 60000  # 60 secondi per caricare la pagina
TIMEOUT_SELECTOR = 30000   # 30 secondi per trovare il selettore
RETRY_COUNT = 2            # Numero di tentativi
DELAY_BETWEEN_PRODUCTS = 2 # Secondi di pausa tra i prodotti


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


def save_debug_output(html, screenshot_path=None, prefix="page"):
    """Salva l'HTML e lo screenshot per debug"""
    os.makedirs(DEBUG_DIR, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    # Salva HTML
    html_path = os.path.join(DEBUG_DIR, f"{prefix}_{timestamp}.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[DEBUG] HTML salvato: {html_path}")
    
    # Salva screenshot se fornito
    if screenshot_path:
        import shutil
        dest_path = os.path.join(DEBUG_DIR, f"screenshot_{prefix}_{timestamp}.png")
        shutil.copy(screenshot_path, dest_path)
        print(f"[DEBUG] Screenshot salvato: {dest_path}")


def get_product_price(page, product_url, product_name):
    """Estrae il prezzo dalla pagina prodotto con retry"""
    print(f"[DEBUG]   Navigazione a pagina prodotto: {product_url}")
    
    for attempt in range(1, RETRY_COUNT + 1):
        print(f"[DEBUG]   Tentativo {attempt}/{RETRY_COUNT}")
        
        try:
            page.goto(product_url, wait_until="domcontentloaded", timeout=TIMEOUT_PAGE_LOAD)
            print(f"[DEBUG]   Pagina caricata, attesa contenuto...")
            
        except Exception as e:
            print(f"[DEBUG]   Errore navigazione (tentativo {attempt}): {e}")
            if attempt == RETRY_COUNT:
                return None
            time.sleep(2)
            continue
        
        # Attendi caricamento prezzo con timeout generoso
        try:
            page.wait_for_function(
                """() => {
                    const elements = document.querySelectorAll('.a-price-whole, .a-price span[data-a-color="price"]');
                    return elements.length > 0;
                }""",
                timeout=TIMEOUT_SELECTOR
            )
            print(f"[DEBUG]   Prezzo trovato nel DOM")
        except:
            print(f"[DEBUG]   Timeout attesa prezzo (tentativo {attempt})")
            if attempt == RETRY_COUNT:
                return None
            time.sleep(2)
            continue
        
        page.wait_for_timeout(500)
        
        html = page.content()
        soup = BeautifulSoup(html, "lxml")
        
        # Salva debug
        screenshot_path = f"/tmp/product_{product_name.replace(' ', '_')[:30]}.png"
        try:
            page.screenshot(path=screenshot_path)
        except:
            screenshot_path = None
        save_debug_output(html, screenshot_path, prefix=f"product_{product_name[:20]}")
        
        # DEBUG: Stampa la struttura completa dei prezzi trovati
        print(f"\n[DEBUG] ===== ANALISI STRUTTURA PREZZO PER: {product_name} =====")
        
        # Cerca TUTTI gli elementi con "a-price"
        price_containers = soup.find_all("span", class_="a-price")
        print(f"[DEBUG] Trovati {len(price_containers)} contenitori .a-price")
        
        for i, container in enumerate(price_containers[:3]):  # Primi 3
            print(f"\n[DEBUG] Contenitore #{i}:")
            print(f"[DEBUG] HTML: {container.prettify()[:500]}")  # Primi 500 caratteri
            
            # Estrai il testo direttamente
            full_text = container.get_text(strip=True)
            print(f"[DEBUG] Testo completo: '{full_text}'")
        
        print("[DEBUG] ===== FINE ANALISI STRUTTURA =====\n")
        
        # Estrattore prezzo - prova molteplici strategie
        price_text = None
        
        # Strategia 1: estrai TUTTO il testo dal contenitore .a-price
        price_container = soup.select_one(".a-price")
        if price_container:
            full_price_text = price_container.get_text(strip=True)
            print(f"[DEBUG]   Strategia 1: Testo completo da .a-price: '{full_price_text}'")
            
            # Estrai il numero con decimali usando regex
            price_match = re.search(r'(\d+[.,]\d{2})\s*€', full_price_text)
            if price_match:
                price_text = price_match.group(1)
                print(f"[DEBUG]   Strategia 1: Prezzo estratto con regex: {price_text}")
        
        # Strategia 2: .a-offscreen (prezzo nascosto ma leggibile)
        if not price_text:
            price_elem = soup.select_one(".a-price .a-offscreen")
            if price_elem:
                price_text = price_elem.get_text(strip=True)
                print(f"[DEBUG]   Strategia 2: Prezzo da .a-offscreen: {price_text}")
        
        # Strategia 3: Estrai dalla parte visibile (.a-price-whole + .a-price-fraction)
        if not price_text:
            whole = soup.select_one(".a-price-whole")
            fraction = soup.select_one(".a-price-fraction")
            if whole and fraction:
                whole_text = whole.get_text(strip=True).replace(".", "").replace(",", "")
                fraction_text = fraction.get_text(strip=True)
                # Ricostruisci: "23" + "50" -> "23,50"
                price_text = f"{whole_text},{fraction_text}"
                print(f"[DEBUG]   Strategia 3: Prezzo ricostruito: {price_text}")
        
        # Strategia 4: Primo elemento con € (fallback)
        if not price_text:
            for elem in soup.find_all(string=re.compile(r'€')):
                if elem and len(elem.strip()) < 20:
                    price_text = elem.strip()
                    print(f"[DEBUG]   Strategia 4: Fallback €: {price_text}")
                    break
        
        if not price_text:
            print(f"[DEBUG]   Nessun prezzo trovato (tentativo {attempt})")
            if attempt == RETRY_COUNT:
                return None
            time.sleep(2)
            continue
        
        try:
            current_price = float(
                price_text
                .replace("€", "")
                .replace(",", ".")
                .replace(" ", "")
                .strip()
            )
            
            if current_price <= 0:
                print(f"[DEBUG]   Prezzo non valido: {current_price} (tentativo {attempt})")
                if attempt == RETRY_COUNT:
                    return None
                time.sleep(2)
                continue
            
            print(f"[DEBUG]   Prezzo finale estratto = €{current_price:.2f} ✓")
            return current_price
            
        except Exception as e:
            print(f"[DEBUG]   Errore parsing prezzo: {e} (tentativo {attempt})")
            if attempt == RETRY_COUNT:
                return None
            time.sleep(2)
            continue
    
    return None


def get_items():
    print(f"[DEBUG] Apertura wishlist con Playwright: {WISHLIST_URL}")
    
    with sync_playwright() as p:
        # Crea un browser context con User-Agent da chrome vero
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-dev-shm-usage',  # Riduce problemi di memoria
            ]
        )
        
        context = browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            viewport={"width": 1920, "height": 1080},
            locale='it-IT',
        )
        
        page = context.new_page()
        
        # Imposta timeout di default più alto
        page.set_default_timeout(TIMEOUT_PAGE_LOAD)
        page.set_default_navigation_timeout(TIMEOUT_PAGE_LOAD)
        
        # Disabilita JavaScript detection
        page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {
                get: () => false,
            });
        """)
        
        print("[DEBUG] Navigazione alla pagina wishlist...")
        try:
            page.goto(WISHLIST_URL, wait_until="domcontentloaded", timeout=TIMEOUT_PAGE_LOAD)
        except Exception as e:
            print(f"[DEBUG] Errore durante navigazione wishlist: {e}")
            browser.close()
            raise
        
        print("[DEBUG] Attesa caricamento wishlist...")
        try:
            page.wait_for_selector("li.g-item-sortable", timeout=TIMEOUT_SELECTOR)
        except:
            print("[DEBUG] Nessun item trovato sulla wishlist!")
            browser.close()
            raise Exception("Impossibile trovare item sulla wishlist")
        
        page.wait_for_timeout(1000)
        
        print("[DEBUG] Estrazione contenuto HTML wishlist...")
        html = page.content()
        
        # Salva screenshot wishlist
        screenshot_path = "/tmp/wishlist_screenshot.png"
        try:
            page.screenshot(path=screenshot_path)
        except Exception as e:
            print(f"[DEBUG] Errore nel salvataggio screenshot: {e}")
            screenshot_path = None
        
        save_debug_output(html, screenshot_path, prefix="wishlist")
        
        soup = BeautifulSoup(html, "lxml")
        items = []

        print("[DEBUG] Parsing degli item della wishlist...")
        product_rows = soup.select("li.g-item-sortable")
        print(f"[DEBUG] Trovati {len(product_rows)} item sulla wishlist")
        
        for idx, row in enumerate(product_rows):
            title_elem = row.select_one("a.a-link-normal")
            
            if not title_elem:
                print(f"[DEBUG] Item #{idx}: Nessun titolo trovato, skip")
                continue

            name = title_elem.get("title", "").strip()
            product_url = title_elem.get("href", "")
            
            if not name:
                name = "PRODOTTO_SENZA_NOME"
            
            if not product_url:
                print(f"[DEBUG] Item #{idx}: {name} - Nessun URL trovato, skip")
                continue
            
            # Assicurati che l'URL sia completo
            if not product_url.startswith("http"):
                product_url = "https://www.amazon.it" + product_url

            print(f"\n[DEBUG] ===== Item #{idx}: {name} =====")
            
            # Estrai il prezzo dalla pagina prodotto
            current_price = get_product_price(page, product_url, name)
            
            if current_price is None:
                print(f"[DEBUG] Item #{idx}: Impossibile estrarre prezzo, skip")
                continue
            
            items.append((name, current_price))
            
            # Pausa prima del prossimo prodotto per non sovraccaricare
            if idx < len(product_rows) - 1:
                print(f"[DEBUG] Pausa di {DELAY_BETWEEN_PRODUCTS}s prima del prossimo prodotto...")
                time.sleep(DELAY_BETWEEN_PRODUCTS)

        browser.close()

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

    try:
        items = get_items()
    except Exception as e:
        print(f"[ERROR] Impossibile scaricare i prezzi: {e}")
        print("[DEBUG] ===== FINE MONITOR (ERRORE) =====")
        return

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
