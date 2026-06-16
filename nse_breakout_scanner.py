from curl_cffi import requests
import pandas as pd
import time
from datetime import datetime

TOLERANCE = 0.05

print("=" * 120)
print("LIVE NSE BREAKOUT SCANNER WITH INDEX MEMBERSHIP")
print("=" * 120)

now = datetime.now().time()
market_start = datetime.strptime("09:15", "%H:%M").time()
market_end = datetime.strptime("15:30", "%H:%M").time()

print(f"\nCurrent Time : {now}")
if not (market_start <= now <= market_end):
    print("\n⚠️ Market closed or outside live hours. NSE data may be empty or static.\n")

session = requests.Session(impersonate="chrome120")

headers = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Origin": "https://www.nseindia.com",
    "Connection": "keep-alive"
}
session.headers.update(headers)

try:
    session.get("https://www.nseindia.com", timeout=10)
    time.sleep(1)
    session.get("https://www.nseindia.com/market-data/live-equity-market", timeout=10)
    time.sleep(1)
    print("✅ NSE Session Ready")
except Exception as e:
    print(f"⚠️ Warning during warmup: {e}")

keyword_to_nse = {
    "NIFTY_AUTO": "NIFTY AUTO",
    "NIFTY_CONSR_DURBL": "NIFTY CONSUMER DURABLES",
    "NIFTY_MEDIA": "NIFTY MEDIA",
    "NIFTY_REALTY": "NIFTY REALTY",
    "NIFTY_CONSUMPTION": "NIFTY CONSUMPTION",
    "NIFTY_PVT_BANK": "NIFTY PRIVATE BANK",
    "NIFTY_BANK": "NIFTY BANK",
    "NIFTY_FMCG": "NIFTY FMCG",
    "NIFTY_FIN_SERVICE": "NIFTY FINANCIAL SERVICES 25/50",
    "NIFTY_ENERGY": "NIFTY ENERGY",
    "NIFTY_PSU_BANK": "NIFTY PSU BANK",
    "NIFTY_METAL": "NIFTY METAL",
    "NIFTY_PHARMA": "NIFTY PHARMA",
    "NIFTY_OIL_AND_GAS": "NIFTY OIL & GAS",
    "NIFTY_IT": "NIFTY IT"
}

def fetch_index(index_name):
    encoded = requests.utils.quote(index_name)
    url = f"https://www.nseindia.com/api/equity-stock-indices?index={encoded}"
    try:
        r = session.get(url, timeout=15)
        if r.status_code != 200:
            return pd.DataFrame()
        data = r.json()
        return pd.DataFrame(data.get("data", []))
    except Exception:
        return pd.DataFrame()

print("📡 Fetching Sector Strength...")

sector_url = "https://intradayscreener.com/api/indices/sectorData/1"
r_sector = requests.get(
    sector_url,
    headers={"User-Agent": "Mozilla/5.0", "Referer": "https://intradayscreener.com/"},
    timeout=10
)
data = r_sector.json()

df_sector = pd.DataFrame({
    "Sector": data.get("labels", []),
    "Keyword": data.get("keywords", []),
    "SectorChange": data.get("datasets", [])
})

df_sector["SectorChange"] = pd.to_numeric(df_sector["SectorChange"], errors="coerce")

selected = df_sector[abs(df_sector["SectorChange"]) >= 0.5]

NSE_MEMBERSHIP_INDICES = {
    "NIFTY50": "NIFTY 50",
    "NIFTY100": "NIFTY 100",
    "NIFTY150": "NIFTY MIDCAP 150",
    "NIFTY200": "NIFTY 200",
    "NIFTY500": "NIFTY 500"
}

membership_map = {}

for short_name, index_name in NSE_MEMBERSHIP_INDICES.items():
    idx_df = fetch_index(index_name)
    if idx_df.empty:
        continue

    idx_df = idx_df[idx_df["symbol"] != index_name]

    for stock in idx_df["symbol"]:
        membership_map.setdefault(stock, {})
        membership_map[stock][short_name] = "Yes"

stock_sector_map = {}

for _, index_name in keyword_to_nse.items():
    sector_df = fetch_index(index_name)

    if sector_df.empty:
        continue

    sector_df = sector_df[sector_df["symbol"] != index_name]

    for stock in sector_df["symbol"]:
        stock_sector_map.setdefault(stock, [])
        stock_sector_map[stock].append(index_name)

all_results = []

for _, sector_row in selected.iterrows():

    keyword = sector_row["Keyword"]

    if keyword not in keyword_to_nse:
        continue

    index_name = keyword_to_nse[keyword]
    trend = "Bullish" if sector_row["SectorChange"] > 0 else "Bearish"

    df = fetch_index(index_name)

    if df.empty:
        continue

    df = df[df["symbol"] != index_name]

    numeric_cols = [
        "open", "dayHigh", "dayLow",
        "lastPrice", "pChange",
        "totalTradedVolume"
    ]

    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    bullish = df[abs(df["open"] - df["dayLow"]) <= TOLERANCE].copy()
    bearish = df[abs(df["open"] - df["dayHigh"]) <= TOLERANCE].copy()

    breakout_df = bullish if trend == "Bullish" else bearish
    breakout_df["Signal"] = "BUY" if trend == "Bullish" else "SELL"

    if breakout_df.empty:
        continue

    breakout_df["Sector"] = breakout_df["symbol"].apply(
        lambda x: ",".join(stock_sector_map.get(x, []))
    )

    breakout_df["SectorStrength"] = sector_row["SectorChange"]

    for idx_name in NSE_MEMBERSHIP_INDICES.keys():
        breakout_df[idx_name] = breakout_df["symbol"].apply(
            lambda x: membership_map.get(x, {}).get(idx_name, "No")
        )

    all_results.append(breakout_df)

if all_results:

    final_df = pd.concat(all_results, ignore_index=True)

    final_df = final_df.drop_duplicates(
        subset=["symbol"],
        keep="first"
    )

    final_df = final_df.sort_values(
        "pChange",
        ascending=False
    )

    output_cols = [
        "symbol",
        "Sector",
        "Signal",
        "SectorStrength",
        "NIFTY50",
        "NIFTY100",
        "NIFTY150",
        "NIFTY200",
        "NIFTY500",
        "open",
        "dayHigh",
        "dayLow",
        "lastPrice",
        "pChange",
        "totalTradedVolume"
    ]

    output_cols = [c for c in output_cols if c in final_df.columns]

    print(final_df[output_cols].to_string(index=False))

    final_df[output_cols].to_excel(
        "nse_breakout_results.xlsx",
        index=False
    )

    print("\\nExcel Saved: nse_breakout_results.xlsx")

else:
    print("No breakout stocks found")
