import time
import threading
from datetime import datetime, timezone
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, jsonify, request
from flask_cors import CORS
from curl_cffi import requests

app = Flask(__name__)
CORS(app)

# Dictionnaire global pour isoler chaque utilisateur
user_sessions = {}
user_lock = threading.Lock()

def get_default_user_data():
    return {
        "keywords": ["nike running", "nike division", "", "", ""],
        "max_price": None,
        "items": [],
        "favorites": [],
        "vinted_session": None,
        "stats": {
            "scraped_count": 0,
            "last_scrape_time": None,
            "avg_price": 0.0,
            "min_price": 0.0,
            "max_price": 0.0,
            "total_found": 0
        }
    }

def get_session_id():
    return request.headers.get("X-Session-ID") or request.args.get("session_id") or "default"

def get_user_store(session_id):
    with user_lock:
        if session_id not in user_sessions:
            user_sessions[session_id] = get_default_user_data()
        return user_sessions[session_id]

def get_vinted_session(udata):
    if udata["vinted_session"] is not None:
        return udata["vinted_session"]
    try:
        session = requests.Session(impersonate="chrome120")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7"
        }
        res = session.get("https://www.vinted.fr", headers=headers, timeout=5)
        if res.status_code == 200:
            udata["vinted_session"] = session
            return session
    except Exception as e:
        print(f"⚠️ Erreur création session Vinted : {e}")
    return udata["vinted_session"]

def format_time_ago(timestamp):
    if not timestamp:
        return "À l'instant"
    try:
        now = datetime.now(timezone.utc).timestamp()
        diff = int(now - timestamp)
        if diff < 60:
            return f"Il y a {max(1, diff)}s"
        elif diff < 3600:
            return f"Il y a {diff // 60} min"
        elif diff < 86400:
            return f"Il y a {diff // 3600}h"
        else:
            return f"Il y a {diff // 86400}j"
    except Exception:
        return "À l'instant"

def fetch_single_keyword(udata, keyword):
    if not keyword or not keyword.strip():
        return []
        
    encoded_keyword = quote(keyword.strip())
    url = f"https://www.vinted.fr/api/v2/catalog/items?search_text={encoded_keyword}&order=newest_first&per_page=96"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.vinted.fr/"
    }

    try:
        session = get_vinted_session(udata)
        if session is None:
            return []
        response = session.get(url, headers=headers, timeout=4)
        if response.status_code == 200:
            return response.json().get("items", [])
        elif response.status_code in [401, 403]:
            udata["vinted_session"] = None
            session = get_vinted_session(udata)
            if session:
                res_retry = session.get(url, headers=headers, timeout=4)
                if res_retry.status_code == 200:
                    return res_retry.json().get("items", [])
    except Exception as e:
        print(f"Erreur scraping sur '{keyword}' : {e}")
    return []

def run_user_scrape(udata):
    active_keywords = [kw for kw in udata["keywords"] if kw and kw.strip()]
    if not active_keywords:
        return

    all_raw_items = []
    
    with ThreadPoolExecutor(max_workers=min(5, len(active_keywords))) as executor:
        results = executor.map(lambda kw: fetch_single_keyword(udata, kw), active_keywords)
        for items_list in results:
            all_raw_items.extend(items_list)

    if not all_raw_items:
        return

    seen_ids = set()
    formatted_items = []
    all_prices = []

    sorted_raw = sorted(
        all_raw_items, 
        key=lambda x: x.get("photo", {}).get("high_resolution", {}).get("timestamp") or x.get("id", 0), 
        reverse=True
    )

    max_p = udata["max_price"]
    for item in sorted_raw:
        item_id = str(item.get("id"))
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)

        price_raw = item.get("price", 0)
        price = float(price_raw.get("amount", 0)) if isinstance(price_raw, dict) else float(price_raw or 0)

        if max_p is not None and price > max_p:
            continue

        all_prices.append(price)

        photo_data = item.get("photo")
        image_url = photo_data.get("url") if isinstance(photo_data, dict) else ""
        created_ts = photo_data.get("high_resolution", {}).get("timestamp") if isinstance(photo_data, dict) else None

        user_data = item.get("user", {}) if isinstance(item.get("user"), dict) else {}
        seller_name = user_data.get("login", "Vendeur")
        
        # Récupération de la note (rating) si disponible
        seller_rating = user_data.get("feedback_reputation", None) or item.get("rating", None)

        formatted_items.append({
            "id": item_id,
            "title": item.get("title") or "Sans titre",
            "brand": item.get("brand_title", "N/A"),
            "size": item.get("size_title", "N/A"),
            "price": f"{price:.2f}",
            "price_num": price,
            "seller": seller_name,
            "seller_rating": seller_rating,
            "condition": item.get("status", "Article"),
            "time_ago": format_time_ago(created_ts),
            "image": image_url,
            "url": f"https://www.vinted.fr/items/{item_id}?referrer=catalog"
        })

    with user_lock:
        udata["items"] = formatted_items
        
        if all_prices:
            udata["stats"]["avg_price"] = round(sum(all_prices) / len(all_prices), 2)
            udata["stats"]["min_price"] = round(min(all_prices), 2)
            udata["stats"]["max_price"] = round(max(all_prices), 2)
            udata["stats"]["total_found"] = len(all_prices)

        udata["stats"]["scraped_count"] += len(formatted_items)
        udata["stats"]["last_scrape_time"] = time.strftime("%H:%M:%S")

# --- ROUTES API ---

@app.route('/api/feed', methods=['GET'])
def get_feed():
    sid = get_session_id()
    udata = get_user_store(sid)
    
    run_user_scrape(udata)
    
    items = udata["items"]
    limited_items = items[:40]
    return jsonify({
        "status": "online",
        "keywords": [kw for kw in udata["keywords"] if kw],
        "total_count": len(items),
        "displayed_count": len(limited_items),
        "items": limited_items,
        "stats": {
            "avg_price": udata["stats"]["avg_price"],
            "min_price": udata["stats"]["min_price"],
            "max_price": udata["stats"]["max_price"]
        }
    })

@app.route('/api/config', methods=['GET', 'POST'])
def handle_config():
    sid = get_session_id()
    udata = get_user_store(sid)
    
    if request.method == 'POST':
        data = request.json or {}
        
        if "single_keyword" in data:
            kw = str(data["single_keyword"]).strip()
            udata["keywords"] = [kw, "", "", "", ""]
            udata["items"] = []

        if "keywords" in data and isinstance(data["keywords"], list):
            new_kw = [str(k).strip() for k in data["keywords"][:5]]
            while len(new_kw) < 5:
                new_kw.append("")
            udata["keywords"] = new_kw
            udata["items"] = []

        if "max_price" in data:
            try:
                val = float(data["max_price"])
                udata["max_price"] = val if val > 0 else None
            except (ValueError, TypeError):
                udata["max_price"] = None

        run_user_scrape(udata)

        return jsonify({"status": "updated", "keywords": udata["keywords"], "max_price": udata["max_price"]})
    
    return jsonify({"keywords": udata["keywords"], "max_price": udata["max_price"]})

@app.route('/api/favorites', methods=['GET', 'POST', 'DELETE'])
def handle_favorites():
    sid = get_session_id()
    udata = get_user_store(sid)
    
    if request.method == 'POST':
        item = request.json
        if item and not any(f['id'] == item['id'] for f in udata["favorites"]):
            udata["favorites"].append(item)
        return jsonify({"status": "added", "favorites": udata["favorites"]})
    
    elif request.method == 'DELETE':
        item_id = request.args.get('id')
        udata["favorites"] = [f for f in udata["favorites"] if f['id'] != item_id]
        return jsonify({"status": "removed", "favorites": udata["favorites"]})
        
    return jsonify({"favorites": udata["favorites"]})

@app.route('/api/recommendations', methods=['GET'])
def get_recommendations():
    sid = get_session_id()
    udata = get_user_store(sid)
    
    if not udata["favorites"]:
        return jsonify({"items": []})
        
    fav_brands = {f['brand'].lower() for f in udata["favorites"] if f.get('brand') and f['brand'] != "N/A"}
    recommended = [
        item for item in udata["items"] 
        if item.get('brand', '').lower() in fav_brands
    ][:40]
    return jsonify({"items": recommended})

@app.route('/api/stats', methods=['GET'])
def get_stats():
    sid = get_session_id()
    udata = get_user_store(sid)
    st = udata["stats"]
    return jsonify({
        "active_keywords": [k for k in udata["keywords"] if k],
        "max_price_filter": udata["max_price"],
        "current_feed_items": len(udata["items"]),
        "displayed_items": min(40, len(udata["items"])),
        "avg_price": st["avg_price"],
        "min_price": st["min_price"],
        "max_price": st["max_price"],
        "total_favorites": len(udata["favorites"]),
        "last_check": st["last_scrape_time"]
    })

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)
