"""
Deep Screener Module - Selenium-based Trendlyne + StockEdge screener
Runs as background thread, updates shared state for dashboard polling.
"""
import json, os, time, requests, re, threading
from datetime import datetime

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service as ChromeService
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from webdriver_manager.chrome import ChromeDriverManager
    HAS_SELENIUM = True
except ImportError:
    HAS_SELENIUM = False

try:
    import pandas as pd
    HAS_PANDAS = True
except ImportError:
    HAS_PANDAS = False

# ── State ─────────────────────────────────────────────────
STATE = {"status":"idle","progress":0,"total":0,"current_stock":"",
         "results":[],"skipped":[],"failed":[],
         "started_at":None,"finished_at":None}
LOCK = threading.Lock()

ID_CACHE = "stock_ids.json"
SE_CACHE = "stockedge_ids.json"

def _load(path):
    if os.path.exists(path):
        with open(path,'r') as f: return json.load(f)
    return {}

def _save(path, data):
    with open(path,'w') as f: json.dump(data, f, indent=4)

def _update(**kw):
    with LOCK: STATE.update(kw)

# ── Public API ────────────────────────────────────────────
def start():
    with LOCK:
        if STATE["status"] == "running": return False
        STATE.update({"status":"running","progress":0,"total":0,
                      "current_stock":"Initializing...","results":[],"skipped":[],"failed":[],
                      "started_at":datetime.now().isoformat(),"finished_at":None})
    threading.Thread(target=_run, daemon=True).start()
    return True

def reset():
    with LOCK:
        STATE.update({"status":"idle","progress":0,"total":0,"current_stock":"",
                      "results":[],"skipped":[],"failed":[],
                      "started_at":None,"finished_at":None})

def get_state():
    with LOCK:
        s = dict(STATE)
        if s["status"]=="running":
            s["results"]=[]; s["skipped"]=[]; s["failed"]=[]
        return s

# ── Stock ID Finders ──────────────────────────────────────
def _find_id_google(symbol):
    try:
        url = f"https://www.google.com/search?q=site:trendlyne.com/equity+{requests.utils.quote(symbol)}"
        r = requests.get(url, headers={"User-Agent":"Mozilla/5.0"}, timeout=10)
        m = re.findall(r'https://trendlyne\.com/equity/(\d+)/([A-Z0-9]+)/([a-z0-9-]+)', r.text)
        if m:
            return {"id":m[0][0],"path":f"{m[0][0]}/{m[0][1]}/{m[0][2]}"}
    except: pass
    return None

def _find_id_selenium(driver, wait, symbol):
    for attempt in range(3):
        try:
            if attempt>0: time.sleep(2**attempt)
            driver.get("https://trendlyne.com/")
            time.sleep(3)
            sb = None
            for by,sel in [(By.CSS_SELECTOR,'input[placeholder*="Search Stock"]'),
                           (By.XPATH,'//input[contains(@placeholder,"Search")]')]:
                try:
                    sb = WebDriverWait(driver,10).until(EC.visibility_of_element_located((by,sel)))
                    if sb: break
                except: continue
            if not sb: continue
            sb.clear(); time.sleep(0.5); sb.send_keys(symbol); time.sleep(4)
            links = []
            for by,sel in [(By.CSS_SELECTOR,"ul.ui-menu a[href*='/equity/']"),
                           (By.XPATH,'//ul[contains(@class,"ui-menu")]//a[contains(@href,"/equity/")]')]:
                try:
                    found = driver.find_elements(by, sel)
                    if found: links=found; break
                except: continue
            if not links: continue
            href = None
            for lnk in links:
                h = lnk.get_attribute("href")
                if h and "/equity/" in h and f"/{symbol}/" in h.upper():
                    href=h; break
            if not href and links: href=links[0].get_attribute("href")
            if not href or "/equity/" not in href: continue
            parts = href.split("/")
            idx = parts.index("equity")
            sid,sym_p = parts[idx+1],parts[idx+2]
            cname = parts[idx+3] if len(parts)>idx+3 else ""
            fp = f"{sid}/{sym_p}/{cname}" if cname else f"{sid}/{sym_p}"
            return {"id":sid,"path":fp}
        except: continue
    return _find_id_google(symbol)

# ── StockEdge ─────────────────────────────────────────────
def _se_find_id(symbol, company_slug=None):
    se_ids = _load(SE_CACHE)
    key = f"{symbol}_{company_slug}" if company_slug else symbol
    if key in se_ids: return se_ids[key]
    try:
        term = company_slug.replace('-',' ') if company_slug else symbol
        url = f"https://api.stockedge.com/Api/UniversalSearchApi/GetQuickSearchResult?searchTerm={requests.utils.quote(term)}&lang=en"
        data = requests.get(url, timeout=10).json()
        doc_id = None
        for item in data.get('Data',[]):
            if item.get('EntityCode')=='se_security':
                doc_id=item.get('DocId'); break
        if doc_id:
            info = requests.get(f"https://api.stockedge.com/Api/SecurityDashboardApi/GetLatestSecurityInfo/{doc_id}?lang=en", timeout=10).json()
            lid = info.get('DefaultListingID')
            if lid:
                se_ids[key]=str(lid); _save(SE_CACHE,se_ids)
                return str(lid)
    except: pass
    return None

def _se_fetch(stock_id):
    if not stock_id: return None
    try:
        r = requests.get(f"https://api.stockedge.com/Api/SecurityDashboardApi/GetTechnicalIndicators/{stock_id}?lang=en",
                         headers={"User-Agent":"Mozilla/5.0","Referer":"https://web.stockedge.com/"}, timeout=10)
        return r.json()
    except: return None

def _se_parse(json_data):
    if not json_data: return {},0
    needed = ["Relative Strength Index (Daily)","Relative Strength Index (Weekly)",
              "Relative Strength Benchmark Index (21 Days)","Relative Strength Benchmark Index (55 Days)",
              "Relative Strength Benchmark Index (21 Weeks)","Static Relative Strength",
              "Adaptive Relative Strength","Relative Strength Sector Index (55 Days)"]
    rs_keys = needed[2:]  # Only RS (not RSI) count toward positive count
    se_data = {}; count = 0
    if isinstance(json_data, list):
        for item in json_data:
            name = item.get("Name")
            if name in needed:
                v,d = item.get("Value","N/A"), item.get("Desc","")
                se_data[name] = {"value":v,"status":d}
                if name in rs_keys and "Positive" in (d or ""): count+=1
    return se_data, count

# Short display names for the 8 indicators
INDICATOR_KEYS = [
    ("Relative Strength Index (Daily)", "RSI Daily"),
    ("Relative Strength Index (Weekly)", "RSI Weekly"),
    ("Relative Strength Benchmark Index (21 Days)", "RS 21 Days"),
    ("Relative Strength Benchmark Index (55 Days)", "RS 55 Days"),
    ("Relative Strength Benchmark Index (21 Weeks)", "RS 21 Weeks"),
    ("Static Relative Strength", "Static RS"),
    ("Adaptive Relative Strength", "Adaptive RS"),
    ("Relative Strength Sector Index (55 Days)", "Sector RS"),
]

# ── Main Runner ───────────────────────────────────────────
def _run():
    driver = None
    try:
        if not HAS_SELENIUM:
            _update(status="error",current_stock="Selenium not installed. pip install selenium webdriver-manager"); return
        if not HAS_PANDAS:
            _update(status="error",current_stock="Pandas not installed. pip install pandas openpyxl"); return

        # 1. Fetch momentum stocks
        _update(current_stock="Fetching momentum stocks...")
        url = "https://intradayscreener.com/api/indicatorscans/reloutperf/OUTPERFORM_SHORT_MEDIUM?filter=fno"
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        api_data = resp.json()
        if not isinstance(api_data, list): raise Exception("Invalid API response")
        stocks = [d['symbol'] for d in api_data if d.get('scanDescription')=='Outperforming benchmark short and medium term']
        skip_syms = ['NIFTY_MID_SELECT','NIFTY_NEXT_50','NIFTY_FIN_SERVICE','NIFTY BANK']
        stocks = [s for s in stocks if s not in skip_syms]
        if not stocks: raise Exception("No momentum stocks found")

        # 2. Load caches
        stock_ids = _load(ID_CACHE)

        # 3. Setup Chrome
        _update(current_stock="Starting Chrome browser...")
        opts = ChromeOptions()
        opts.add_argument("--start-maximized")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--log-level=3")
        opts.add_experimental_option('excludeSwitches', ['enable-logging'])
        driver = webdriver.Chrome(service=ChromeService(ChromeDriverManager().install()), options=opts)
        wait_drv = WebDriverWait(driver, 15)

        # 4. Find missing IDs
        need_ids = [s for s in stocks if s not in stock_ids]
        if need_ids:
            _update(current_stock=f"Finding IDs for {len(need_ids)} new stocks...")
            for sym in need_ids:
                sid = _find_id_selenium(driver, wait_drv, sym)
                if sid: stock_ids[sym] = sid
            _save(ID_CACHE, stock_ids)

        # 5. Process stocks
        have_ids = [s for s in stocks if s in stock_ids]
        total = len(have_ids)
        _update(total=total)
        results = []; skipped = []; failed = []
        fin_rows = []; del_dfs = []

        for i, sym in enumerate(have_ids):
            _update(progress=i+1, current_stock=sym)
            try:
                sd = stock_ids[sym]
                sp = sd['path'] if isinstance(sd,dict) else sd

                # ── Financials ──
                driver.get(f"https://trendlyne.com/fundamentals/financials/{sp}/")
                time.sleep(2)
                tbl = wait_drv.until(EC.presence_of_element_located((By.CSS_SELECTOR,"table")))
                hdrs = [th.text.strip() for th in tbl.find_elements(By.TAG_NAME,"th")]
                rows = []
                for tr in tbl.find_elements(By.TAG_NAME,"tr")[1:]:
                    cells = [td.text.strip() for td in tr.find_elements(By.TAG_NAME,"td")]
                    if cells: rows.append(cells)
                df_fin = pd.DataFrame(rows, columns=hdrs)
                rev_row = df_fin[df_fin['Indicator']=='Total Rev.']
                if rev_row.empty:
                    failed.append({"stock":sym,"reason":"No revenue data"}); continue
                qcols = [c for c in rev_row.columns if "'" in c]
                rv = rev_row[qcols].iloc[0].replace('','0').replace(',','',regex=True).astype(float)
                lq,lv = rv.index[0], float(rv.values[0])
                pq,pv = rv.index[1], float(rv.values[1])
                cpct = ((lv-pv)/pv*100) if pv!=0 else 0
                status = "Good" if lv>pv else "Bad"
                if status=="Bad":
                    skipped.append({"stock":sym,"reason":f"Revenue declining {cpct:+.1f}%"}); continue

                row = {"stock":sym,"latest_quarter":lq,"latest_revenue":lv,
                       "prev_quarter":pq,"prev_revenue":pv,
                       "revenue_change":round(lv-pv,2),"revenue_change_pct":round(cpct,1),
                       "revenue_status":status}

                # ── Delivery ──
                del_data = {}
                try:
                    driver.get(f"https://trendlyne.com/equity/delivery-analysis/{sp}/")
                    time.sleep(3)
                    dtbl = WebDriverWait(driver,20).until(EC.presence_of_element_located((By.CSS_SELECTOR,"table")))
                    dhdrs = [th.text.strip() for th in dtbl.find_elements(By.TAG_NAME,"th")]
                    drows = []
                    for tr in dtbl.find_elements(By.TAG_NAME,"tr")[1:]:
                        cells = [td.text.strip() for td in tr.find_elements(By.TAG_NAME,"td")]
                        if cells: drows.append(cells)
                    if drows:
                        df_del = pd.DataFrame(drows, columns=dhdrs)
                        df_del.insert(0,"Stock",sym)
                        del_dfs.append(df_del)
                        # Extract first row values for the result
                        first = drows[0]
                        col_map = {h.strip().lower():v for h,v in zip(dhdrs, first)}
                        for k,v in col_map.items():
                            if 'delivery' in k and 'vol' not in k:
                                try: del_data["delivery_pct"]=float(v.replace(',','').replace('%',''))
                                except: pass
                            elif 'change' in k:
                                try: del_data["price_change_pct"]=float(v.replace(',','').replace('%',''))
                                except: pass
                            elif 'weekly' in k or 'wkly' in k or 'insight' in k:
                                del_data["vs_weekly_avg"]=v
                            elif 'close' in k:
                                try: del_data["close_price"]=float(v.replace(',',''))
                                except: pass
                            elif 'date' in k:
                                del_data["date"]=v
                except: pass

                row.update(del_data)

                # ── StockEdge ──
                slug = None
                if sp:
                    parts = sp.split("/")
                    if len(parts)>=3: slug=parts[2]
                se_id = _se_find_id(sym, slug)
                se_data, rs_count = {}, 0
                if se_id:
                    jdata = _se_fetch(se_id)
                    if jdata: se_data, rs_count = _se_parse(jdata)
                row["rs_count"] = rs_count
                for full_name, short_name in INDICATOR_KEYS:
                    ind = se_data.get(full_name)
                    row[short_name] = f"{ind['value']} ({ind['status']})" if ind else None
                if "close_price" not in row: row["close_price"] = None
                results.append(row)
            except Exception as e:
                failed.append({"stock":sym,"reason":str(e)[:80]})

        # 6. Save Excel
        try:
            os.makedirs("swing_screener_reports", exist_ok=True)
            ts = datetime.now().strftime("%d-%m-%Y")
            if results:
                df_out = pd.DataFrame(results)
                if del_dfs:
                    try:
                        delivery_all = pd.concat(del_dfs, ignore_index=True)
                        delivery_all.columns = (delivery_all.columns.str.strip().str.upper()
                                                .str.replace(" ","_").str.replace("%","PCT")
                                                .str.replace("(","").str.replace(")",""))
                        if "DATE" in delivery_all.columns:
                            delivery_all["DATE"] = pd.to_datetime(delivery_all["DATE"], format="%d %b '%y", errors="coerce")
                            delivery_all = delivery_all.sort_values(["STOCK","DATE"], ascending=[True,False])
                    except: pass
                fpath = f"swing_screener_reports/{ts}.xlsx"
                df_out.to_excel(fpath, index=False)
        except Exception as ex:
            print(f"[Screener] Save error: {ex}")

        _update(status="done",results=results,skipped=skipped,failed=failed,
                finished_at=datetime.now().isoformat())
    except Exception as e:
        _update(status="error",current_stock=str(e)[:200],finished_at=datetime.now().isoformat())
    finally:
        if driver:
            try: driver.quit()
            except: pass
