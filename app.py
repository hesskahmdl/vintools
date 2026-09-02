import time
import threading
from flask import Flask, jsonify, request
from flask_cors import CORS
from curl_cffi import requests

app = Flask(__name__)
CORS(app)

# Configuration de la recherche par défaut
SEARCH_KEYWORD = "nike"
MAX_PRICE = 30.0
CHECK_INTERVAL = 3

seen_item_ids = set()
detected_items = []

def get_vinted_session():
    """Crée une session anonyme avec cookies Vinted."""
    session = requests.Session(impersonate="chrome120")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7"
    }
    try:
        session.get("https://www.vinted.fr", headers=headers, timeout=10)
    except Exception as e:
        print(f"⚠️ Erreur d'initialisation de session : {e}")
    return session

def scrape_vinted():
    """Scraper trié par prix croissant (les moins chers en premier)."""
    global detected_items, seen_item_ids
    session = get_vinted_session()

    while True:
        if SEARCH_KEYWORD:
            try:
                url = f"https://www.vinted.fr/api/v2/catalog/items?search_text={SEARCH_KEYWORD}&order=price_low_to_high&per_page=20"
                
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "application/json, text/plain, */*",
                    "Referer": "https://www.vinted.fr/"
                }

                response = session.get(url, headers=headers, timeout=5)

                if response.status_code in [401, 403]:
                    print("🔄 Réinitialisation de la session Vinted...")
                    session = get_vinted_session()
                    continue

                if response.status_code == 200:
                    data = response.json()
                    items = data.get("items", [])

                    temp_items = []
                    for item in items:
                        item_id = str(item.get("id"))
                        
                        # Extraction sécurisée du prix (gestion dict, float, int ou str)
                        price_raw = item.get("price", 0)
                        if isinstance(price_raw, dict):
                            price = float(price_raw.get("amount", 0))
                        else:
                            price = float(price_raw or 0)

                        if price <= MAX_PRICE:
                            # Extraction sécurisée de l'image
                            photo_data = item.get("photo")
                            image_url = photo_data.get("url") if isinstance(photo_data, dict) else ""

                            # Extraction sécurisée du vendeur
                            user_data = item.get("user")
                            seller_name = user_data.get("login", "Vendeur") if isinstance(user_data, dict) else "Vendeur"

                            formatted_item = {
                                "id": item_id,
                                "title": item.get("title", "Sans titre"),
                                "brand": item.get("brand_title", "Marque inconnue"),
                                "size": item.get("size_title", "Taille N/A"),
                                "price": f"{price:.2f}",
                                "seller": seller_name,
                                "condition": item.get("status", "Bon état"),
                                "time_ago": "Les moins chers",
                                "image": image_url,
                                "url": f"https://www.vinted.fr/items/{item_id}?referrer=catalog"
                            }
                            temp_items.append(formatted_item)

                    detected_items = temp_items
                    print(f"⚡ [Mise à jour] {len(detected_items)} articles trouvés pour '{SEARCH_KEYWORD}' (< {MAX_PRICE}€)")

            except Exception as e:
                print(f"Erreur de lecture : {e}")

        time.sleep(CHECK_INTERVAL)

# Lancement du scraper en tâche de fond
scraper_thread = threading.Thread(target=scrape_vinted, daemon=True)
scraper_thread.start()

# --- ROUTES API FRONTEND ---

@app.route('/api/feed', methods=['GET'])
def get_feed():
    return jsonify({
        "status": "online",
        "keyword": SEARCH_KEYWORD,
        "max_price": MAX_PRICE,
        "count": len(detected_items),
        "items": detected_items
    })

@app.route('/api/config', methods=['POST'])
def update_config():
    global SEARCH_KEYWORD, MAX_PRICE, detected_items
    data = request.json or {}
    
    if "keyword" in data:
        raw_keyword = str(data["keyword"]).strip()
        # Nettoyage des crochets ou guillemets indésirables venant du frontend
        raw_keyword = raw_keyword.replace("[", "").replace("]", "").replace("'", "").replace('"', '')
        if raw_keyword:
            SEARCH_KEYWORD = raw_keyword.lower()
            
    if "max_price" in data and data["max_price"]:
        try:
            MAX_PRICE = float(data["max_price"])
        except ValueError:
            pass
        
    detected_items = [] # Vide la liste pour recharger avec les nouveaux filtres
    print(f"⚙️ Configuration appliquée : Mot-clé='{SEARCH_KEYWORD}' | Prix Max={MAX_PRICE}€")
    return jsonify({"status": "updated"})

if __name__ == '__main__':
    print("🚀 Serveur de détection par prix croissant démarré !")
    app.run(host='0.0.0.0', port=5000, debug=True)
