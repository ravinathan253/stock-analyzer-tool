"""
Swing Trading & Investment Advisor - Live Data Backend
Enhanced with Financials + Delivery + RSI Analysis
"""
import json, os, time, requests, math, re, concurrent.futures, threading, csv
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime
from urllib.parse import parse_qs, urlparse

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Origin": "https://www.nseindia.com"
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)
CACHE = {}
CACHE_TTL = 120

def _get(url, cache_key=None, timeout=12):
    if cache_key and cache_key in CACHE:
        ts, data = CACHE[cache_key]
        if time.time() - ts < CACHE_TTL:
            return data
    try:
        r = SESSION.get(url, timeout=timeout)
        if r.status_code == 200:
            data = r.json() if r.headers.get('content-type', '').find('json') >= 0 else r.text
            if cache_key:
                CACHE[cache_key] = (time.time(), data)
            return data
    except Exception as e:
        print(f"[GET] {url}: {e}")
    return None

def _post(url, data=None, cache_key=None, timeout=12):
    if cache_key and cache_key in CACHE:
        ts, d = CACHE[cache_key]
        if time.time() - ts < CACHE_TTL:
            return d
    try:
        r = SESSION.post(url, json=data, timeout=timeout)
        if r.status_code in (200, 201):
            d = r.json() if r.headers.get('content-type', '').find('json') >= 0 else r.text
            if cache_key:
                CACHE[cache_key] = (time.time(), d)
            return d
    except Exception as e:
        print(f"[POST] {url}: {e}")
    return None

def _warm():
    try:
        SESSION.get("https://www.nseindia.com", timeout=10)
        time.sleep(1)
    except:
        pass

_warm()

ID_CACHE_FILE = "stock_ids.json"
STOCKEDGE_ID_CACHE_FILE = "stockedge_ids.json"

if os.path.exists(ID_CACHE_FILE):
    with open(ID_CACHE_FILE, 'r') as f:
        STOCK_ID_MAP = json.load(f)
else:
    STOCK_ID_MAP = {}

if os.path.exists(STOCKEDGE_ID_CACHE_FILE):
    with open(STOCKEDGE_ID_CACHE_FILE, 'r') as f:
        STOCKEDGE_ID_MAP = json.load(f)
else:
    STOCKEDGE_ID_MAP = {}

def save_stock_ids():
    with open(ID_CACHE_FILE, 'w') as f:
        json.dump(STOCK_ID_MAP, f, indent=4)

def save_stockedge_ids():
    with open(STOCKEDGE_ID_CACHE_FILE, 'w') as f:
        json.dump(STOCKEDGE_ID_MAP, f, indent=4)

def find_stock_id(symbol):
    """Find Trendlyne stock ID"""
    symbol = symbol.strip().upper()
    if symbol in STOCK_ID_MAP:
        return STOCK_ID_MAP[symbol]
    return None

def search_stockedge_for_id(symbol, company_name=None):
    """Search StockEdge API for stock ID"""
    cache_key = f"{symbol}_{company_name}" if company_name else symbol
    if cache_key in STOCKEDGE_ID_MAP:
        return STOCKEDGE_ID_MAP[cache_key]

    try:
        search_term = company_name.replace('-', ' ') if company_name else symbol
        search_url = f"https://api.stockedge.com/Api/UniversalSearchApi/GetQuickSearchResult?searchTerm={requests.utils.quote(search_term)}&lang=en"
        r = requests.get(search_url, timeout=10)
        data = r.json()
        search_results = data.get('Data', [])
        doc_id = None
        for item in search_results:
            if item.get('EntityCode') == 'se_security':
                doc_id = item.get('DocId')
                break

        if doc_id:
            info_url = f"https://api.stockedge.com/Api/SecurityDashboardApi/GetLatestSecurityInfo/{doc_id}?lang=en"
            r_info = requests.get(info_url, timeout=10)
            info_data = r_info.json()
            listing_id = info_data.get('DefaultListingID')
            if listing_id:
                found_id = str(listing_id)
                STOCKEDGE_ID_MAP[cache_key] = found_id
                save_stockedge_ids()
                return found_id
    except Exception as e:
        print(f"[StockEdge] Search failed for {symbol}: {e}")
    return None

def fetch_stockedge_indicators(stock_id):
    """Fetch RSI and Relative Strength from StockEdge"""
    if not stock_id:
        return None, "No ID"
    url = f"https://api.stockedge.com/Api/SecurityDashboardApi/GetTechnicalIndicators/{stock_id}?lang=en"
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://web.stockedge.com/"
        }
        r = requests.get(url, headers=headers, timeout=10)
        if r.status_code == 200:
            return r.json(), "OK"
    except Exception as e:
        return None, str(e)[:50]
    return None, "Error"

def parse_stockedge_indicators(json_data):
    """Parse StockEdge indicators"""
    if not json_data:
        return {}, 0

    indicators_needed = {
        "Relative Strength Index (Daily)": "RSI Daily",
        "Relative Strength Index (Weekly)": "RSI Weekly",
        "Relative Strength Benchmark Index (21 Days)": "RSI 21 Days",
        "Relative Strength Benchmark Index (55 Days)": "RSI 55 Days",
        "Relative Strength Benchmark Index (21 Weeks)": "RSI 21 Weeks",
        "Static Relative Strength": "Static RS",
        "Adaptive Relative Strength": "Adaptive RS",
        "Relative Strength Sector Index (55 Days)": "Sector RS"
    }

    se_data = {}
    rs_positive_count = 0

    if isinstance(json_data, list):
        for item in json_data:
            name = item.get("Name")
            if name in indicators_needed:
                val = item.get("Value", "N/A")
                desc = item.get("Desc") or ""
                se_data[indicators_needed[name]] = {"value": val, "status": desc}

                if name in [
                    "Relative Strength Benchmark Index (21 Days)",
                    "Relative Strength Benchmark Index (55 Days)",
                    "Relative Strength Benchmark Index (21 Weeks)",
                    "Static Relative Strength",
                    "Adaptive Relative Strength",
                    "Relative Strength Sector Index (55 Days)"
                ]:
                    if "Positive" in desc:
                        rs_positive_count += 1

    return se_data, rs_positive_count

def fetch_financials_data(symbol):
    """Fetch revenue/quarter results from Trendlyne"""
    stock_data = find_stock_id(symbol)
    if not stock_data:
        return None

    stock_path = stock_data['path'] if isinstance(stock_data, dict) else stock_data
    url = f"https://trendlyne.com/fundamentals/financials/{stock_path}/"

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml"
        }
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            return None

        html = r.text

        revenue_pattern = r'<tr[^>]*>\s*<td[^>]*>\s*Total Rev\.\s*</td>\s*<td[^>]*>\s*([\d,.]+)\s*</td>\s*<td[^>]*>\s*([\d,.]+)\s*</td>\s*<td[^>]*>\s*([\d,.]+)\s*</td>'
        matches = re.findall(revenue_pattern, html, re.IGNORECASE)

        if matches:
            latest = matches[0]
            prev = matches[1] if len(matches) > 1 else matches[0]

            def parse_val(v):
                return float(v.replace(',', ''))

            latest_q = parse_val(latest[0])
            prev_q = parse_val(prev[0])

            if prev_q > 0:
                change_pct = ((latest_q - prev_q) / prev_q) * 100
                return {
                    "latest_revenue": latest_q,
                    "previous_revenue": prev_q,
                    "change_pct": round(change_pct, 1),
                    "status": "Good" if change_pct > 0 else "Bad"
                }
    except Exception as e:
        print(f"[Financials] {symbol}: {e}")
    return None

def fetch_delivery_data(symbol):
    """Fetch delivery data from Trendlyne with full column extraction"""
    stock_data = find_stock_id(symbol)
    if not stock_data:
        return None
    stock_path = stock_data['path'] if isinstance(stock_data, dict) else stock_data
    url = f"https://trendlyne.com/equity/delivery-analysis/{stock_path}/"
    try:
        hdrs = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Accept": "text/html,application/xhtml+xml"}
        r = requests.get(url, headers=hdrs, timeout=15)
        if r.status_code != 200:
            return None
        html = r.text
        # Try full table parse: headers + first data row
        th_cells = re.findall(r'<th[^>]*>(.*?)</th>', html, re.DOTALL | re.IGNORECASE)
        col_names = [re.sub(r'<[^>]+>', '', h).strip().lower() for h in th_cells]
        tbody = re.search(r'<tbody[^>]*>(.*?)</tbody>', html, re.DOTALL | re.IGNORECASE)
        if tbody:
            first_tr = re.search(r'<tr[^>]*>(.*?)</tr>', tbody.group(1), re.DOTALL)
            if first_tr:
                tds = re.findall(r'<td[^>]*>(.*?)</td>', first_tr.group(1), re.DOTALL)
                vals = [re.sub(r'<[^>]+>', '', c).strip() for c in tds]
                result = {}
                for i, v in enumerate(vals):
                    if i >= len(col_names): break
                    cn = col_names[i]
                    cl = v.replace(',', '').replace('%', '').strip()
                    if 'delivery' in cn and 'vol' not in cn:
                        try: result["delivery_pct"] = float(cl)
                        except: pass
                    elif 'change' in cn:
                        try: result["price_change_pct"] = float(cl)
                        except: pass
                    elif 'weekly' in cn or 'wkly' in cn or 'insight' in cn:
                        result["vs_weekly_avg"] = v.strip()
                    elif 'close' in cn:
                        try: result["close_price"] = float(cl)
                        except: pass
                    elif 'date' in cn:
                        result["date"] = v.strip()
                if "delivery_pct" in result:
                    return result
        # Fallback: simple regex
        pat = r'<tr[^>]*>\s*<td[^>]*>\s*[\d]+\s*</td>\s*<td[^>]*>\s*([\d.]+)\s*</td>\s*<td[^>]*>\s*([-\d.]+)\s*</td>'
        m = re.findall(pat, html, re.IGNORECASE)
        if m:
            return {"delivery_pct": float(m[0][0]), "price_change_pct": float(m[0][1])}
    except Exception as e:
        print(f"[Delivery] {symbol}: {e}")
    return None

def fetch_index(index="NIFTY 50"):
    cached = _get(f"idx_{index}")
    if cached: return cached
    try:
        enc = requests.utils.quote(index, safe="")
        url = f"https://www.nseindia.com/api/equity-stockIndices?index={enc}"
        data = _get(url, f"idx_{index}")
        if data:
            return data.get("data", [])
    except Exception as e:
        print(f"[Index] {index}: {e}")
        _warm()
    return []

def fetch_momentum_symbols():
    cached = _get("momentum")
    if cached: return cached
    try:
        url = "https://intradayscreener.com/api/indicatorscans/reloutperf/OUTPERFORM_SHORT_MEDIUM?filter=fno"
        r = requests.get(url, timeout=10)
        data = r.json()
        if isinstance(data, list):
            skip = ['NIFTY_MID_SELECT', 'NIFTY_NEXT_50', 'NIFTY_FIN_SERVICE', 'NIFTY BANK']
            import pandas as pd
            df = pd.DataFrame(data)
            df = df[df['scanDescription'] == 'Outperforming benchmark short and medium term']
            symbols = [s for s in df['symbol'].tolist() if s not in skip]
            _get(None, "momentum")  # trigger cache set
            CACHE["momentum"] = (time.time(), symbols)
            return symbols
    except Exception as e:
        print(f"[Momentum] Fetch failed: {e}")
    return []

def fetch_quote(symbol):
    symbol = symbol.strip().upper()
    cached = _get(f"quote_{symbol}")
    if cached: return cached
    try:
        url = f"https://www.nseindia.com/api/quote-equity?symbol={requests.utils.quote(symbol)}"
        data = _get(url, f"quote_{symbol}")
        if data:
            result = data.get("priceInfo", {})
            info = data.get("info", {})
            industry = data.get("industryInfo", {})
            quote = {
                "symbol": symbol,
                "lastPrice": result.get("lastPrice", 0),
                "open": result.get("open", 0),
                "dayHigh": result.get("intraDayHighLow", {}).get("max", 0),
                "dayLow": result.get("intraDayHighLow", {}).get("min", 0),
                "previousClose": result.get("previousClose", 0),
                "change": result.get("change", 0),
                "pChange": result.get("pChange", 0),
                "totalTradedVolume": result.get("totalTradedVolume", 0),
                "yearHigh": result.get("weekHighLow", {}).get("max", 0),
                "yearLow": result.get("weekHighLow", {}).get("min", 0),
                "industry": industry.get("industry", info.get("industry", "")),
                "companyName": info.get("companyName", symbol),
            }
            return quote
    except Exception as e:
        print(f"[Quote] {symbol}: {e}")
    return None

def get_market_overview():
    nifty = fetch_index("NIFTY 50")
    bank = fetch_index("NIFTY BANK")

    n_idx = next((s for s in nifty if s.get("symbol") == "NIFTY 50"), None)
    b_idx = next((s for s in bank if s.get("symbol") == "NIFTY BANK"), None)

    def safe(obj, key, default=0):
        if not obj: return default
        v = obj.get(key, default)
        try: return float(v)
        except: return default

    return {
        "nifty50": {
            "level": safe(n_idx, "lastPrice"),
            "change": safe(n_idx, "change"),
            "changePct": safe(n_idx, "pChange"),
            "open": safe(n_idx, "open"),
            "high": safe(n_idx, "dayHigh"),
            "low": safe(n_idx, "dayLow")
        },
        "niftyBank": {
            "level": safe(b_idx, "lastPrice"),
            "change": safe(b_idx, "change"),
            "changePct": safe(b_idx, "pChange")
        },
        "marketStatus": "Live" if nifty else "Offline",
        "lastUpdated": datetime.now().strftime("%b %d, %Y %H:%M IST"),
        "stockCount": len([s for s in nifty if s.get("symbol", "") != "NIFTY 50"])
    }

def get_swing_picks(quality_mode=True):
    """Generate swing picks with parallel financial/RSI analysis"""
    momentum_syms = fetch_momentum_symbols()
    nifty_data = fetch_index("NIFTY 50")
    nifty200 = fetch_index("NIFTY 200")
    all_stocks = {s["symbol"]: s for s in (nifty_data + nifty200) if "symbol" in s and "NIFTY" not in s.get("symbol", "")}

    # First filter stocks by basic price momentum to avoid wasting API calls
    candidates = []
    for sym in momentum_syms:
        if sym in all_stocks:
            candidates.append(all_stocks[sym])

    # Add other strong Nifty stocks
    sorted_stocks = sorted(all_stocks.values(), key=lambda x: float(x.get("pChange", 0) or 0), reverse=True)
    for s in sorted_stocks:
        if s["symbol"] in [c["symbol"] for c in candidates]:
            continue
        price = float(s.get("lastPrice", 0) or 0)
        if price < 50: continue
        pch = float(s.get("pChange", 0) or 0)
        if pch > 0.5:
            candidates.append(s)

    # Limit to top 50 candidates to keep it snappy if needed, but parallel should handle more
    candidates = candidates[:60]

    picks = []
    # Parallel processing of candidates
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        future_to_stock = {executor.submit(build_swing_pick, s, "Momentum", quality_mode): s for s in candidates}
        for future in concurrent.futures.as_completed(future_to_stock):
            pick = future.result()
            if pick:
                picks.append(pick)

    # Sort picks by signal score
    picks.sort(key=lambda x: x.get("signalScore", 0), reverse=True)
    return picks

def build_swing_pick(s, reason_prefix, quality_mode=True):
    try:
        price = float(s.get("lastPrice", 0) or 0)
        prev = float(s.get("previousClose", 0) or 0)
        high = float(s.get("dayHigh", 0) or 0)
        low = float(s.get("dayLow", 0) or 0)
        opn = float(s.get("open", 0) or 0)
        pch = float(s.get("pChange", 0) or 0)
        vol = float(s.get("totalTradedVolume", 0) or 0)
        y_high = float(s.get("yearHigh", 0) or 0)
        y_low = float(s.get("yearLow", 0) or 0)

        if price <= 0 or prev <= 0:
            return None

        atr_est = high - low if high > low else price * 0.02
        if atr_est < price * 0.005:
            atr_est = price * 0.02

        sl = round(price - (atr_est * 1.5), 2)
        target = round(price + (atr_est * 3), 2)
        risk = price - sl
        reward = target - price
        rr_raw = round(reward / risk, 1) if risk > 0 else 0
        rr = int(rr_raw) if rr_raw == int(rr_raw) else rr_raw

        if rr < 1.5:
            sl = round(price - (atr_est * 1.0), 2)
            target = round(price + (atr_est * 2.5), 2)
            risk = price - sl
            reward = target - price
            rr_raw = round(reward / risk, 1) if risk > 0 else 0
            rr = int(rr_raw) if rr_raw == int(rr_raw) else rr_raw

        if rr < 1.5:
            return None

        entry_low = round(min(price, opn) - atr_est * 0.2, 2)
        entry_high = round(price + atr_est * 0.3, 2)

        symbol = s["symbol"]
        stock_id = find_stock_id(symbol)
        se_id = search_stockedge_for_id(symbol)

        financial_data = None
        delivery_data = None
        rsi_data = {}
        rsi_status = "N/A"

        if quality_mode:
            financial_data = fetch_financials_data(symbol)
            delivery_data = fetch_delivery_data(symbol)

            if se_id:
                rsi_json, rsi_msg = fetch_stockedge_indicators(se_id)
                if rsi_json:
                    rsi_data, rsi_status = parse_stockedge_indicators(rsi_json)

        # Build momentum signal with actual data
        momentum_parts = [reason_prefix]
        if pch > 1:
            momentum_parts.append(f"Strong +{pch:.1f}% move today")
        elif pch > 0:
            momentum_parts.append(f"Positive +{pch:.1f}% momentum")
        if opn == low and pch > 0:
            momentum_parts.append("Open=Low bullish pattern")
        if y_high > 0:
            from_high = ((y_high - price) / y_high * 100)
            if from_high < 10:
                momentum_parts.append(f"Near 52W high ({from_high:.0f}% away)")

        # Add RSI data to momentum
        if rsi_data:
            rsi_daily = rsi_data.get("RSI Daily", {})
            if rsi_daily:
                rsi_val = rsi_daily.get("value", "")
                rsi_desc = rsi_daily.get("status", "")
                if rsi_val:
                    momentum_parts.append(f"RSI Daily: {rsi_val} ({rsi_desc})")
            if isinstance(rsi_status, int) and rsi_status > 0:
                momentum_parts.append(f"RS Positive: {rsi_status}/6 indicators bullish")

        # Add revenue data to momentum
        if financial_data:
            rev_change = financial_data.get("change_pct", 0)
            momentum_parts.append(f"Revenue QoQ: {rev_change:+.1f}% ({financial_data['status']})")

        # Add delivery data to momentum
        if delivery_data:
            del_pct = delivery_data.get("delivery_pct", 0)
            if del_pct > 0:
                momentum_parts.append(f"Delivery: {del_pct:.1f}%")

        sector = s.get("meta", {}).get("industry", "") or s.get("industry", "") or "NSE Listed"

        # Build why buy with quality data
        why_parts = [f"Live momentum detected. Price ₹{price} with {pch:+.1f}% change. Day range ₹{low}–₹{high}. Volume: {vol:,.0f}. Risk-reward 1:{rr}."]
        if financial_data and financial_data["status"] == "Good":
            why_parts.append(f"📊 Revenue growing {financial_data['change_pct']:+.1f}% QoQ (₹{financial_data['previous_revenue']:,.0f} → ₹{financial_data['latest_revenue']:,.0f}).")
        if delivery_data and delivery_data.get("delivery_pct", 0) > 50:
            why_parts.append(f"📦 High delivery at {delivery_data['delivery_pct']:.1f}% — indicates genuine buying interest.")
        if isinstance(rsi_status, int) and rsi_status >= 4:
            why_parts.append(f"📈 Strong RS: {rsi_status}/6 relative strength indicators positive — stock outperforming benchmark.")
        elif isinstance(rsi_status, int) and rsi_status >= 2:
            why_parts.append(f"📈 Moderate RS: {rsi_status}/6 relative strength indicators positive.")

        pick = {
            "stock": symbol,
            "sector": sector,
            "currentPrice": price,
            "entryRange": f"{entry_low}–{entry_high}",
            "targetPrice": target,
            "stopLoss": sl,
            "riskReward": f"1:{rr}",
            "momentum": ". ".join(momentum_parts),
            "whyBuy": " ".join(why_parts),
            "whenToSell": f"Exit at ₹{target} target OR below ₹{sl} stop loss. Also exit if stock reverses below previous close of ₹{prev:.2f} on closing basis.",
            "liveData": {
                "open": opn,
                "high": high,
                "low": low,
                "close": price,
                "prevClose": prev,
                "change": pch,
                "volume": vol,
                "yearHigh": y_high,
                "yearLow": y_low
            }
        }

        # Attach raw quality data for frontend display
        if quality_mode:
            if financial_data:
                pick["financials"] = financial_data
                if financial_data["status"] == "Bad":
                    return None  # Skip stocks with declining revenue

            if delivery_data:
                pick["delivery"] = delivery_data

            if rsi_data:
                pick["rsi"] = rsi_data
                pick["rsi_positive_count"] = rsi_status if isinstance(rsi_status, int) else 0

        # ── Signal Classification ──
        score = 0
        reasons = []

        # Price momentum
        if pch > 2:
            score += 3; reasons.append("Strong bullish momentum")
        elif pch > 0.5:
            score += 2; reasons.append("Positive momentum")
        elif pch > 0:
            score += 1; reasons.append("Mild positive")
        elif pch < -2:
            score -= 3; reasons.append("Sharp decline")
        elif pch < -0.5:
            score -= 2; reasons.append("Negative momentum")

        # Open=Low bullish
        if opn == low and pch > 0:
            score += 1; reasons.append("Open=Low bullish")

        # 52-week position
        if y_high > 0:
            from_high = ((y_high - price) / y_high * 100)
            if from_high < 5:
                score += 1; reasons.append("Near 52W high")
            elif from_high > 30:
                score -= 1; reasons.append("Far from 52W high")

        # RSI score
        rs_count = rsi_status if isinstance(rsi_status, int) else 0
        if rs_count >= 5:
            score += 3; reasons.append(f"RS very strong ({rs_count}/6)")
        elif rs_count >= 3:
            score += 2; reasons.append(f"RS positive ({rs_count}/6)")
        elif rs_count >= 1:
            score += 1; reasons.append(f"RS mild ({rs_count}/6)")

        # RSI Daily value
        if rsi_data:
            rsi_daily = rsi_data.get("RSI Daily", {})
            rsi_val_str = str(rsi_daily.get("value", ""))
            try:
                rsi_val = float(rsi_val_str)
                if rsi_val > 70:
                    score -= 1; reasons.append(f"RSI overbought ({rsi_val:.0f})")
                elif rsi_val > 55:
                    score += 1; reasons.append(f"RSI bullish ({rsi_val:.0f})")
                elif rsi_val < 30:
                    score -= 1; reasons.append(f"RSI oversold ({rsi_val:.0f})")
            except:
                pass

        # Volume
        if vol > 1000000:
            score += 1; reasons.append("High volume")

        # Risk-reward
        if rr >= 2.5:
            score += 1; reasons.append(f"Excellent R:R 1:{rr}")

        # Classify signal
        if score >= 5:
            signal = "STRONG BUY"
        elif score >= 3:
            signal = "BUY"
        elif score >= 1:
            signal = "MODERATE"
        elif score <= -2:
            signal = "SELL"
        else:
            signal = "HOLD"

        pick["signal"] = signal
        pick["signalScore"] = score
        pick["signalReasons"] = reasons

        return pick
    except Exception as e:
        print(f"[Pick] Error for {s.get('symbol', '?')}: {e}")
        return None

# get_longterm_picks removed

# get_portfolio removed

def group_by_sector(picks):
    """Group swing picks by sector with aggregate stats"""
    sector_map = {}
    for p in picks:
        sector = p.get('sector') or 'Other'
        if not sector or sector.strip() == '' or sector == 'NSE Listed':
            sector = 'Diversified / Other'
        if sector not in sector_map:
            sector_map[sector] = []
        sector_map[sector].append(p)

    result = []
    for sector, stocks in sector_map.items():
        avg_change = sum(s.get('liveData', {}).get('change', 0) or 0 for s in stocks) / len(stocks) if stocks else 0
        strong_buys = sum(1 for s in stocks if s.get('signal') == 'STRONG BUY')
        buys = sum(1 for s in stocks if s.get('signal') in ('STRONG BUY', 'BUY'))
        avg_score = sum(s.get('signalScore', 0) or 0 for s in stocks) / len(stocks) if stocks else 0
        result.append({
            'sector': sector,
            'stockCount': len(stocks),
            'avgChange': round(avg_change, 2),
            'strongBuys': strong_buys,
            'totalBuys': buys,
            'avgScore': round(avg_score, 1),
            'stocks': sorted(stocks, key=lambda x: x.get('signalScore', 0), reverse=True)
        })

    # Sort sectors: highest avg score first
    result.sort(key=lambda x: x['avgScore'], reverse=True)
    return result

# ── Deep Screener (via external module) ──────────────────
import deep_screener

class SwingAdvisorAPI(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self._cors()
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode('utf-8'))

    def _serve_file(self, name, ctype):
        try:
            p = os.path.join(os.path.dirname(__file__), name)
            with open(p, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', ctype)
            self._cors()
            self.end_headers()
            self.wfile.write(content)
        except:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        try:
            self._handle_get(path, query)
        except Exception as e:
            print(f"[ERROR] do_GET crashed: {e}")
            import traceback; traceback.print_exc()
            self.send_response(500)
            self.end_headers()

    def _handle_get(self, path, query):
        if path in ("/", "/index.html", "/dashboard"):
            self._serve_file("dashboard.html", "text/html")
            return
        if path == "/styles.css":
            self._serve_file("styles.css", "text/css")
            return

        if path == "/api/swing-picks":
            quality = query.get("quality", ["true"])[0].lower() == "true"
            picks = get_swing_picks(quality_mode=quality)
            self._json({"picks": picks, "timestamp": datetime.now().isoformat(), "source": "LIVE NSE + Financials + RSI"})
            return
        if path == "/api/sector-picks":
            quality = query.get("quality", ["true"])[0].lower() == "true"
            picks = get_swing_picks(quality_mode=quality)
            sectors = group_by_sector(picks)
            self._json({"sectors": sectors, "timestamp": datetime.now().isoformat(), "source": "LIVE NSE + Financials + RSI"})
            return
        if path == "/api/market-overview":
            data = get_market_overview()
            self._json(data)
            return
        if path == "/api/search-stock":
            symbol = query.get("symbol", [""])[0].strip().upper()
            if not symbol:
                self._json({"error": "Missing symbol parameter"}, 400)
                return
            quote = fetch_quote(symbol)
            if not quote:
                self._json({"error": f"Could not fetch data for {symbol}"}, 404)
                return
            pick = build_swing_pick(quote, f"Manual search", quality_mode=True)
            if pick:
                self._json({"pick": pick, "timestamp": datetime.now().isoformat()})
            else:
                self._json({"quote": quote, "message": f"{symbol} does not meet swing criteria", "timestamp": datetime.now().isoformat()})
            return
        if path == "/api/deep-screener":
            action = query.get("action",["status"])[0]
            if action == "start":
                ok = deep_screener.start()
                self._json({"started":ok,"message":"Screener started" if ok else "Already running"})
            elif action == "reset":
                deep_screener.reset()
                self._json({"message":"Reset"})
            else:
                self._json(deep_screener.get_state())
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {args[0]}")

# fetch_sectors removed

class LiveAnalysis:
    def get_market_overview(self):
        return get_market_overview()

    def get_swing_picks(self, quality_mode=True):
        return get_swing_picks(quality_mode)

analysis = LiveAnalysis()

if __name__ == '__main__':
    port = 8080
    server = HTTPServer(('localhost', port), SwingAdvisorAPI)
    print("=" * 60)
    print("  SWING ADVISOR PRO — ENHANCED WITH FINANCIALS + RSI")
    print("=" * 60)
    print(f"  Dashboard:  http://localhost:{port}")
    print(f"  API:        http://localhost:{port}/api/")
    print(f"  Quality:    Financials + Delivery + RSI Analysis")
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    server.server_close()
    print("\nServer stopped.")