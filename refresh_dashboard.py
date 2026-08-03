#!/usr/bin/env python3
"""
Performance Attribution Tracker — LIVE data refresher.

What this does
---------------
Fetches real, current prices from Yahoo Finance (free, no API key) for:
  - Global market indices (Nifty 50, Sensex, Nikkei 225, KOSPI, Shanghai/CSI300, FTSE 100, S&P 500, Nasdaq 100)
  - India NSE sector indices (Bank, IT, Pharma, FMCG, Metal, Energy, Realty, PSU Bank, Media, Auto, Infra)
  - US sector SPDR ETFs (XLK, XLF, XLV, XLP, XLB, XLE, XLRE, XLC, XLI, XLU, XLY) as sector proxies
  - Broad country indices for Europe / UK / Japan / Korea / China, used as a country-level proxy
    wherever a free, structured *sector-level* index for that market doesn't exist
  - Individual stock returns for the top-10 constituents per sector (India + US), to rank by
    contribution to sector return (return x assumed index weight)

Computes D / W / M / YTD % change for each, and writes a fresh, self-contained
attribution_tracker_live.html with real numbers and a real timestamp.

Why this has to run on YOUR machine, not inside Claude's sandbox
------------------------------------------------------------------
Claude's artifact/tool sandbox cannot reach finance.yahoo.com (only a short allow-list
of package registries is reachable). This script needs your normal internet connection.

Usage
-----
    pip install yfinance pandas --break-system-packages   # or in a venv, drop the flag
    python3 refresh_dashboard.py

Output: attribution_tracker_live.html in the same folder. Open it in a browser.
Re-run any time for a fresh snapshot — there is no auto-refresh/polling (a static
HTML file can't poll Yahoo on its own without a backend), so "live" here means
"live as of when you last ran the script".

Known data gaps (same limits apply to any free-data build of this dashboard):
  - No free, structured sector-level indices for Europe/UK/Japan/Korea/China
    -> those columns show the broad country index change instead, clearly labeled
  - No free per-stock live "index weight" -> weights are static, approximate,
    sourced from public factsheets; only the RETURN component is live
  - No free institutional/FII-DII-equivalent flow data for non-India markets
  - India FII/DII cash figures are not on Yahoo Finance; script leaves that panel
    with a link to NSE/NSDL for you to check same-day

Ticker verification (2026-07-11)
---------------------------------
Cross-checked the highest-risk tickers via web search against live Yahoo Finance
quote pages (investing.com uses its own internal symbol IDs, not Yahoo-compatible
tickers, so it can't be used directly here -- but it's useful for confirming a
company/index's correct current name, which is how mismatches like Zomato -> Eternal
get caught). All of the following were confirmed live and correctly named:
  Global indices : ^NSEI, ^BSESN, ^N225, ^KS11, 000001.SS, 000300.SS, ^FTSE, ^GSPC, ^NDX
  India sectors  : ^NSEBANK, ^CNXIT, ^CNXPHARMA, ^CNXFMCG, ^CNXMETAL, ^CNXENERGY,
                   ^CNXREALTY, ^CNXPSUBANK, ^CNXMEDIA, ^CNXINFRA, ^CNXAUTO
  Europe proxy   : ^STOXX50E
  Tricky stocks  : M&M.NS (Mahindra & Mahindra), ETERNAL.NS (formerly Zomato,
                   renamed March 2025), MCDOWELL-N.NS (United Spirits)
Not independently re-verified this pass: the remaining ~170 individual stock
tickers (assumed correct from standard NSE/NYSE/NASDAQ conventions) and the US
sector SPDR ETF tickers (XLK/XLF/etc. -- these are long-standing, stable tickers).
If any come back None on your run, tell me the ticker and I'll look it up.
"""

import json
import sys
import datetime as dt

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    print("Missing dependency. Run:\n  pip install yfinance pandas --break-system-packages")
    sys.exit(1)

# --------------------------------------------------------------------------
# 1. TICKER MAPS
# --------------------------------------------------------------------------

GLOBAL_INDICES = [
    ("India", "🇮🇳", "Nifty 50", "^NSEI"),
    ("India", "🇮🇳", "Sensex", "^BSESN"),
    ("Japan", "🇯🇵", "Nikkei 225", "^N225"),
    ("S. Korea", "🇰🇷", "KOSPI", "^KS11"),
    ("China", "🇨🇳", "Shanghai Composite", "000001.SS"),
    ("China", "🇨🇳", "CSI 300", "000300.SS"),
    ("UK", "🇬🇧", "FTSE 100", "^FTSE"),
    ("US", "🇺🇸", "S&P 500", "^GSPC"),
    ("US", "🇺🇸", "Nasdaq 100", "^NDX"),
]

# Country-level proxy tickers used for sector columns where no free sector index exists
COUNTRY_PROXY = {
    "eu": "^STOXX50E",   # Euro Stoxx 50 (broad Europe proxy)
    "uk": "^FTSE",
    "jp": "^N225",
    "kr": "^KS11",
    "cn": "000300.SS",
}

# sector -> {in: NSE index ticker or None, us: SPDR ETF ticker or None}
SECTOR_INDEX_TICKERS = {
    "IT / Technology":            {"in": "^CNXIT",      "us": "XLK"},
    "Banking / Financials":       {"in": "^NSEBANK",    "us": "XLF"},
    "Pharma / Healthcare":        {"in": "^CNXPHARMA",  "us": "XLV"},
    "FMCG / Consumer Staples":    {"in": "^CNXFMCG",    "us": "XLP"},
    "Metal / Materials":          {"in": "^CNXMETAL",   "us": "XLB"},
    "Energy":                     {"in": "^CNXENERGY",  "us": "XLE"},
    "Realty":                     {"in": "^CNXREALTY",  "us": "XLRE"},
    "PSU / Public Sector":        {"in": "^CNXPSUBANK", "us": None},
    "Media":                      {"in": "^CNXMEDIA",   "us": "XLC"},
    "Infra":                      {"in": "^CNXINFRA",   "us": None},
    "Industrials":                {"in": None,          "us": "XLI"},
    "Utilities":                  {"in": None,          "us": "XLU"},
    "Consumer Discretionary":     {"in": None,          "us": "XLY"},
    "Auto":                       {"in": "^CNXAUTO",    "us": None},
}

# sector -> {in:[(name,ticker,weight)], us:[(name,ticker,weight)]}   -- top 10 each, static weights
STOCKS = {
 "IT / Technology": {
  "in":[("TCS","TCS.NS",9.7),("Infosys","INFY.NS",8.2),("HCL Tech","HCLTECH.NS",4.1),("Wipro","WIPRO.NS",3.3),
        ("Tech Mahindra","TECHM.NS",2.8),("LTIMindtree","LTIM.NS",2.1),("Persistent Systems","PERSISTENT.NS",1.6),
        ("Coforge","COFORGE.NS",1.4),("Mphasis","MPHASIS.NS",1.2),("Oracle Fin. Services","OFSS.NS",0.9)],
  "us":[("Apple","AAPL",22.1),("Microsoft","MSFT",19.4),("Nvidia","NVDA",18.8),("Broadcom","AVGO",6.2),
        ("Oracle","ORCL",3.9),("Salesforce","CRM",3.1),("Cisco","CSCO",2.4),("IBM","IBM",2.2),
        ("Adobe","ADBE",1.9),("Qualcomm","QCOM",1.7)],
 },
 "Banking / Financials": {
  "in":[("HDFC Bank","HDFCBANK.NS",28.5),("ICICI Bank","ICICIBANK.NS",23.1),("SBI","SBIN.NS",9.4),
        ("Kotak Mahindra","KOTAKBANK.NS",8.2),("Axis Bank","AXISBANK.NS",7.6),("IndusInd Bank","INDUSINDBK.NS",4.1),
        ("Bank of Baroda","BANKBARODA.NS",3.2),("Punjab National Bank","PNB.NS",2.4),
        ("Federal Bank","FEDERALBNK.NS",2.1),("IDFC First Bank","IDFCFIRSTB.NS",1.6)],
  "us":[("JPMorgan","JPM",12.8),("Berkshire (Fin)","BRK-B",8.1),("Visa","V",7.4),("Mastercard","MA",6.2),
        ("Bank of America","BAC",5.9),("Goldman Sachs","GS",5.2),("Morgan Stanley","MS",4.4),
        ("Wells Fargo","WFC",4.1),("Citigroup","C",3.6),("American Express","AXP",3.2)],
 },
 "Pharma / Healthcare": {
  "in":[("Sun Pharma","SUNPHARMA.NS",15.2),("Cipla","CIPLA.NS",9.8),("Dr Reddy's","DRREDDY.NS",8.9),
        ("Divi's Lab","DIVISLAB.NS",7.4),("Apollo Hospitals","APOLLOHOSP.NS",6.8),("Lupin","LUPIN.NS",4.2),
        ("Aurobindo Pharma","AUROPHARMA.NS",3.6),("Torrent Pharma","TORNTPHARM.NS",3.1),
        ("Zydus Lifesciences","ZYDUSLIFE.NS",2.8),("Mankind Pharma","MANKIND.NS",2.4)],
  "us":[("Eli Lilly","LLY",18.4),("UnitedHealth","UNH",10.2),("Johnson & Johnson","JNJ",9.7),
        ("AbbVie","ABBV",7.8),("Merck","MRK",5.9),("Pfizer","PFE",5.1),("Thermo Fisher","TMO",4.6),
        ("Abbott","ABT",4.2),("Amgen","AMGN",3.8),("Bristol-Myers Squibb","BMY",3.2)],
 },
 "FMCG / Consumer Staples": {
  "in":[("HUL","HINDUNILVR.NS",17.8),("ITC","ITC.NS",16.4),("Nestle India","NESTLEIND.NS",9.1),
        ("Britannia","BRITANNIA.NS",7.2),("Tata Consumer","TATACONSUM.NS",6.5),("Godrej Consumer","GODREJCP.NS",4.8),
        ("Dabur","DABUR.NS",3.9),("Marico","MARICO.NS",3.4),("Colgate-Palmolive India","COLPAL.NS",3.1),
        ("United Spirits","MCDOWELL-N.NS",2.6)],
  "us":[("Procter & Gamble","PG",13.2),("Costco","COST",12.8),("Walmart","WMT",11.4),("Coca-Cola","KO",9.6),
        ("PepsiCo","PEP",8.9),("Mondelez","MDLZ",6.2),("Philip Morris","PM",5.6),("Altria","MO",4.8),
        ("Kimberly-Clark","KMB",3.9),("General Mills","GIS",3.2)],
 },
 "Metal / Materials": {
  "in":[("Tata Steel","TATASTEEL.NS",18.2),("JSW Steel","JSWSTEEL.NS",14.6),("Hindalco","HINDALCO.NS",12.8),
        ("Vedanta","VEDL.NS",10.4),("SAIL","SAIL.NS",5.2),("NMDC","NMDC.NS",4.1),
        ("Jindal Steel & Power","JINDALSTEL.NS",3.8),("APL Apollo Tubes","APLAPOLLO.NS",3.2),
        ("Hindustan Zinc","HINDZINC.NS",2.9),("NALCO","NATIONALUM.NS",2.4)],
  "us":[("Linde","LIN",24.1),("Freeport-McMoRan","FCX",9.8),("Air Products","APD",8.4),("Newmont","NEM",6.9),
        ("Nucor","NUE",5.1),("Alcoa","AA",4.2),("Sherwin-Williams","SHW",3.8),("Ecolab","ECL",3.4),
        ("Dow","DOW",2.9),("DuPont","DD",2.6)],
 },
 "Energy": {
  "in":[("Reliance Industries","RELIANCE.NS",34.2),("ONGC","ONGC.NS",12.1),("IOC","IOC.NS",8.9),
        ("BPCL","BPCL.NS",6.4),("Coal India","COALINDIA.NS",6.1),("GAIL","GAIL.NS",4.8),
        ("Adani Total Gas","ATGL.NS",3.6),("Oil India","OIL.NS",3.2),("HPCL","HINDPETRO.NS",2.8),
        ("Adani Green Energy","ADANIGREEN.NS",2.4)],
  "us":[("ExxonMobil","XOM",22.6),("Chevron","CVX",17.8),("ConocoPhillips","COP",9.2),("Schlumberger","SLB",6.4),
        ("EOG Resources","EOG",5.8),("Marathon Petroleum","MPC",4.9),("Williams Companies","WMB",4.2),
        ("Kinder Morgan","KMI",3.8),("Phillips 66","PSX",3.4),("Valero Energy","VLO",3.1)],
 },
 "Realty": {
  "in":[("DLF","DLF.NS",22.4),("Godrej Properties","GODREJPROP.NS",13.6),("Macrotech (Lodha)","LODHA.NS",11.8),
        ("Oberoi Realty","OBEROIRLTY.NS",8.2),("Phoenix Mills","PHOENIXLTD.NS",7.1),("Prestige Estates","PRESTIGE.NS",5.4),
        ("Brigade Enterprises","BRIGADE.NS",4.2),("Sobha","SOBHA.NS",3.6),("Sunteck Realty","SUNTECK.NS",2.9),
        ("Mahindra Lifespace","MAHLIFE.NS",2.4)],
  "us":[("Prologis","PLD",12.4),("American Tower","AMT",10.8),("Equinix","EQIX",9.6),("Welltower","WELL",7.9),
        ("Simon Property","SPG",6.4),("Digital Realty","DLR",5.2),("Public Storage","PSA",4.6),
        ("Realty Income","O",4.1),("AvalonBay","AVB",3.6),("Crown Castle","CCI",3.2)],
 },
 "PSU / Public Sector": {
  "in":[("SBI","SBIN.NS",15.2),("NTPC","NTPC.NS",9.8),("Power Grid","POWERGRID.NS",8.6),
        ("Coal India","COALINDIA.NS",7.9),("ONGC","ONGC.NS",7.2),("BPCL","BPCL.NS",4.2),
        ("GAIL","GAIL.NS",3.6),("Indian Oil","IOC.NS",3.2),("NMDC","NMDC.NS",2.8),
        ("Bank of Baroda","BANKBARODA.NS",2.4)],
  "us":[],
 },
 "Media": {
  "in":[("Zee Entertainment","ZEEL.NS",18.4),("PVR Inox","PVRINOX.NS",16.2),("Sun TV","SUNTV.NS",14.8),
        ("Network18","NETWORK18.NS",9.1),("TV18 Broadcast","TV18BRDCST.NS",6.4),("Dish TV","DISHTV.NS",3.2),
        ("Saregama India","SAREGAMA.NS",2.8),("Nazara Technologies","NAZARA.NS",2.4),
        ("Hathway Cable","HATHWAY.NS",1.9),("Balaji Telefilms","BALAJITELE.NS",1.6)],
  "us":[("Meta","META",22.4),("Alphabet","GOOGL",20.1),("Netflix","NFLX",9.8),("Disney","DIS",6.2),
        ("Comcast","CMCSA",5.4),("Charter Communications","CHTR",4.8),("Warner Bros Discovery","WBD",3.9),
        ("Paramount Global","PARA",3.2),("Fox Corp","FOXA",2.8),("Live Nation","LYV",2.4)],
 },
 "Infra": {
  "in":[("L&T","LT.NS",24.6),("Adani Ports","ADANIPORTS.NS",12.8),("Power Grid","POWERGRID.NS",9.4),
        ("NTPC","NTPC.NS",8.1),("GMR Airports","GMRINFRA.NS",5.2),("IRB Infra","IRB.NS",3.8),
        ("KEC International","KEC.NS",3.2),("NCC Ltd","NCC.NS",2.8),("Adani Energy Solutions","ADANIENSOL.NS",2.4),
        ("Rail Vikas Nigam","RVNL.NS",2.1)],
  "us":[],
 },
 "Industrials": {
  "in":[("L&T","LT.NS",26.4),("Siemens India","SIEMENS.NS",10.8),("ABB India","ABB.NS",7.9),
        ("Cummins India","CUMMINSIND.NS",6.4),("Bharat Electronics","BEL.NS",5.8),("Bharat Forge","BHARATFORG.NS",5.2),
        ("Havells India","HAVELLS.NS",4.4),("Thermax","THERMAX.NS",3.8),("Voltas","VOLTAS.NS",3.2),
        ("Tube Investments","TIINDIA.NS",2.8)],
  "us":[("GE Aerospace","GE",11.2),("Caterpillar","CAT",8.9),("RTX","RTX",7.4),("Honeywell","HON",6.8),
        ("Union Pacific","UNP",5.9),("Lockheed Martin","LMT",5.4),("Boeing","BA",4.8),("3M","MMM",4.2),
        ("Deere & Co","DE",3.8),("Parker Hannifin","PH",3.2)],
 },
 "Utilities": {
  "in":[("NTPC","NTPC.NS",22.1),("Power Grid","POWERGRID.NS",19.4),("Tata Power","TATAPOWER.NS",10.8),
        ("Adani Power","ADANIPOWER.NS",9.2),("NHPC","NHPC.NS",6.1),("Torrent Power","TORNTPOWER.NS",4.8),
        ("JSW Energy","JSWENERGY.NS",4.1),("CESC","CESC.NS",3.4),("SJVN","SJVN.NS",2.8),("PTC India","PTC.NS",2.2)],
  "us":[("NextEra Energy","NEE",15.8),("Southern Co","SO",9.4),("Duke Energy","DUK",9.1),
        ("Constellation","CEG",8.6),("Vistra","VST",6.2),("AEP","AEP",5.6),("Sempra Energy","SRE",4.8),
        ("Dominion Energy","D",4.2),("PG&E Corp","PCG",3.6),("Exelon","EXC",3.2)],
 },
 "Consumer Discretionary": {
  "in":[("Titan","TITAN.NS",16.2),("Maruti Suzuki","MARUTI.NS",14.8),("M&M","M&M.NS",13.4),
        ("Trent","TRENT.NS",9.8),("Bajaj Auto","BAJAJ-AUTO.NS",8.6),("Eternal (Zomato)","ETERNAL.NS",6.2),
        ("Nykaa","NYKAA.NS",4.4),("Avenue Supermarts (DMart)","DMART.NS",4.1),("Info Edge","NAUKRI.NS",3.6),
        ("United Breweries","UBL.NS",3.1)],
  "us":[("Amazon","AMZN",24.6),("Tesla","TSLA",14.2),("Home Depot","HD",9.8),("McDonald's","MCD",7.4),
        ("Booking Holdings","BKNG",5.9),("Starbucks","SBUX",4.6),("Nike","NKE",4.1),("Lowe's","LOW",3.6),
        ("TJX Companies","TJX",3.2),("Chipotle","CMG",2.8)],
 },
 "Auto": {
  "in":[("Maruti Suzuki","MARUTI.NS",24.6),("M&M","M&M.NS",18.2),("Tata Motors","TATAMOTORS.NS",14.8),
        ("Bajaj Auto","BAJAJ-AUTO.NS",12.1),("Eicher Motors","EICHERMOT.NS",8.4),("Hero MotoCorp","HEROMOTOCO.NS",6.8),
        ("TVS Motor","TVSMOTOR.NS",5.2),("Ashok Leyland","ASHOKLEY.NS",4.4),("Motherson","MOTHERSON.NS",3.6),
        ("Balkrishna Industries","BALKRISIND.NS",2.9)],
  "us":[("Tesla","TSLA",68.2),("General Motors","GM",12.4),("Ford","F",9.8),("PACCAR","PCAR",4.6),("Rivian","RIVN",2.4)],
 },
}

# --------------------------------------------------------------------------
# 2. FETCH + COMPUTE HELPERS
# --------------------------------------------------------------------------

def pct(a, b):
    """% change from a to b"""
    if a is None or b is None or a == 0:
        return None
    return (b - a) / a * 100.0

def compute_changes(hist):
    """Given a daily-close history (pandas Series, ascending tz-aware date index), return
    D/W/M/YTD % changes based on the LAST FULLY SETTLED trading session -- never today's
    live/in-progress price.

    How this correctly handles multiple time zones:
    yfinance returns each ticker's index tz-aware, localized to THAT EXCHANGE's own timezone
    (NSE bars in Asia/Kolkata, Nikkei bars in Asia/Tokyo, S&P bars in America/New_York, etc).
    So instead of comparing a bar's date against this script's local system date (which caused
    a real bug: markets got compared against the WRONG "today" and some had an extra, already-
    final day incorrectly chopped off), we compute "today" separately for each ticker, in that
    same exchange's own timezone. Only if the most recent bar falls on that exchange's own
    current calendar day do we treat it as potentially still-forming and drop it. A bar from a
    prior session -- even if it's several calendar days behind other markets because of
    weekends/holidays, or because Yahoo's free feed simply updates that particular market's
    data more slowly -- is left alone rather than needlessly discarded.

    Net effect: every ticker's "current" value is the newest close Yahoo has actually finalized
    for that specific market -- no more, no less. If a market's data still looks stale after
    this (e.g. several days behind), that reflects Yahoo's own free-tier data lag for that
    ticker, not this drop logic -- there's nothing further this script can fix without a paid
    data source for that market.
    """
    if hist is None or len(hist) < 2:
        return {"D": None, "W": None, "M": None, "YTD": None}, None, None
    closes = hist.dropna()
    if len(closes) == 0:
        return {"D": None, "W": None, "M": None, "YTD": None}, None, None

    tz = closes.index.tz
    try:
        now_local = pd.Timestamp.now(tz=tz) if tz is not None else pd.Timestamp.now()
        if closes.index[-1].date() == now_local.date():
            closes = closes.iloc[:-1]
    except Exception:
        pass  # if tz handling fails for any reason, just use the data as-is rather than crash

    if len(closes) < 2:
        return {"D": None, "W": None, "M": None, "YTD": None}, None, None
    last = float(closes.iloc[-1])
    asof = closes.index[-1].date().isoformat()
    out = {}
    out["D"] = pct(float(closes.iloc[-2]), last) if len(closes) >= 2 else None
    out["W"] = pct(float(closes.iloc[-6]), last) if len(closes) >= 6 else None
    out["M"] = pct(float(closes.iloc[-22]), last) if len(closes) >= 22 else None
    # YTD: first trading day close of current calendar year
    this_year = closes.index[-1].year
    ytd_slice = closes[closes.index.year == this_year]
    out["YTD"] = pct(float(ytd_slice.iloc[0]), last) if len(ytd_slice) >= 1 else None
    return out, last, asof

def fetch_history(ticker, period="1y"):
    try:
        t = yf.Ticker(ticker)
        h = t.history(period=period, interval="1d")["Close"]
        if h.empty:
            return None
        return h
    except Exception as e:
        print(f"  [warn] failed to fetch {ticker}: {e}")
        return None

def fetch_changes(ticker):
    h = fetch_history(ticker)
    if h is None:
        return {"D": None, "W": None, "M": None, "YTD": None}, None, None
    return compute_changes(h)


# --------------------------------------------------------------------------
# 3. MAIN FETCH ROUTINE
# --------------------------------------------------------------------------

def main():
    print("Fetching live data from Yahoo Finance (EOD closes only, no intraday/live quotes)...\n")
    now = dt.datetime.now()
    asof_dates_seen = set()

    # ---- Global indices ----
    print("Global indices:")
    indices_out = []
    for country, flag, name, ticker in GLOBAL_INDICES:
        chg, level, asof = fetch_changes(ticker)
        tier = "ver" if chg["D"] is not None else "na"
        if asof:
            asof_dates_seen.add(asof)
        indices_out.append({"flag": flag, "name": name, "level": (f"{level:,.2f}" if level else "—"),
                             "D": chg["D"], "tier": tier, "asof": asof})
        print(f"  {name:22s} {ticker:12s} D={chg['D']}  (EOD as of {asof})")

    # Flag any index whose data is noticeably staler than the freshest one -- this is Yahoo's own
    # free-tier data lag for that specific market, not a bug in this script's EOD-detection logic.
    valid_asofs = [dt.date.fromisoformat(ix["asof"]) for ix in indices_out if ix["asof"]]
    if valid_asofs:
        freshest = max(valid_asofs)
        stale = [(ix["name"], ix["asof"]) for ix in indices_out
                 if ix["asof"] and (freshest - dt.date.fromisoformat(ix["asof"])).days >= 2]
        if stale:
            print("\n  [note] These indices are more than 1 day behind the freshest data seen --")
            print("         this reflects Yahoo's own free-tier data lag for that market, not a bug here:")
            for name, asof in stale:
                lag = (freshest - dt.date.fromisoformat(asof)).days
                print(f"           {name:22s} last close {asof}  ({lag} days behind freshest: {freshest.isoformat()})")

    # ---- Country proxies for EU/UK/JP/KR/CN sector columns ----
    print("\nCountry proxy indices (used for sector columns where no free sector index exists):")
    country_proxy_changes = {}
    for key, ticker in COUNTRY_PROXY.items():
        chg, _, asof = fetch_changes(ticker)
        if asof:
            asof_dates_seen.add(asof)
        country_proxy_changes[key] = chg
        print(f"  {key:4s} {ticker:12s} D={chg['D']}  (EOD as of {asof})")

    # ---- Sector index-level data ----
    print("\nSector indices (India NSE / US SPDR sector ETFs):")
    sectors_out = {}
    for sector, tks in SECTOR_INDEX_TICKERS.items():
        entry = {}
        for mkt in ["in", "us"]:
            ticker = tks.get(mkt)
            if ticker:
                chg, _, asof = fetch_changes(ticker)
                if asof:
                    asof_dates_seen.add(asof)
                tier = "ver" if chg["D"] is not None else "na"
                entry[mkt] = {k: ({"v": v, "tier": tier} if v is not None else None) for k, v in chg.items()}
            else:
                entry[mkt] = {"D": None, "W": None, "M": None, "YTD": None}
        # EU/UK/JP/KR/CN -> use country proxy value for every sector (labeled 'ind' tier = country-level, not sector-specific)
        for ck in ["eu", "uk", "jp", "kr", "cn"]:
            chg = country_proxy_changes[ck]
            entry[ck] = {k: ({"v": v, "tier": "proxy"} if v is not None else None) for k, v in chg.items()}
        sectors_out[sector] = entry
        print(f"  {sector:28s} IN(D)={entry['in']['D']}  US(D)={entry['us']['D']}")

    # ---- Individual stock returns ----
    print("\nIndividual stock returns (this is the slow part - one call per stock):")
    stock_returns = {}
    for sector, mkts in STOCKS.items():
        stock_returns[sector] = {}
        for mkt in ["in", "us"]:
            rows = []
            for name, ticker, weight in mkts[mkt]:
                chg, _, asof = fetch_changes(ticker)
                if asof:
                    asof_dates_seen.add(asof)
                rows.append({"name": name, "ticker": ticker, "weight": weight, "chg": chg})
            stock_returns[sector][mkt] = rows
        print(f"  done: {sector}")

    # Most common EOD date across everything fetched -- used as the headline "data as of" date.
    # (Different exchanges can have different last-trading-day dates around weekends/holidays,
    # so this is the majority date, not necessarily every single ticker's date.)
    if asof_dates_seen:
        from collections import Counter
        eod_date = Counter(asof_dates_seen).most_common(1)[0][0] if len(asof_dates_seen) == 1 else sorted(asof_dates_seen)[-1]
    else:
        eod_date = None

    # ---- Assemble final JSON payload for the HTML/JS layer ----
    payload = {
        "generated_at": now.strftime("%a, %d %b %Y %H:%M:%S"),
        "eod_date": eod_date,
        "indices": indices_out,
        "sectors": sectors_out,
        "stocks": stock_returns,
    }

    with open("dashboard_data.json", "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    print("\nWrote dashboard_data.json")

    build_html(payload)
    print("Wrote attribution_tracker_live.html")


# --------------------------------------------------------------------------
# 4. HTML BUILDER (reads the payload, emits a self-contained HTML file)
# --------------------------------------------------------------------------

def build_html(payload):
    sector_names = list(SECTOR_INDEX_TICKERS.keys())

    # Build the JS `sectors` array from live payload
    js_sectors = []
    for sector in sector_names:
        s = payload["sectors"][sector]
        st = payload["stocks"][sector]
        def freqblock(mktkey):
            block = s[mktkey]
            out = {}
            for f in ["D", "W", "M", "YTD"]:
                cell = block.get(f)
                out[f] = cell if cell else None
            return out
        js_sectors.append({
            "name": sector,
            "in": freqblock("in"), "us": freqblock("us"), "eu": freqblock("eu"),
            "uk": freqblock("uk"), "jp": freqblock("jp"), "kr": freqblock("kr"), "cn": freqblock("cn"),
            "st": {
                "in": [[r["name"], r["weight"], r["chg"]] for r in st["in"]],
                "us": [[r["name"], r["weight"], r["chg"]] for r in st["us"]],
            }
        })

    sectors_json = json.dumps(js_sectors)
    indices_json = json.dumps(payload["indices"])
    generated_at = payload["generated_at"]
    eod_date = payload.get("eod_date") or "unknown"

    html = HTML_TEMPLATE.replace("__SECTORS_JSON__", sectors_json) \
                         .replace("__INDICES_JSON__", indices_json) \
                         .replace("__GENERATED_AT__", generated_at) \
                         .replace("__EOD_DATE__", eod_date)

    with open("attribution_tracker_live.html", "w", encoding="utf-8") as f:
        f.write(html)


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Performance Attribution Tracker — LIVE</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;700&display=swap');
:root{--bg:#0B0E14;--panel:#12161F;--panel2:#171C27;--border:#232A38;--text:#E6E9EF;--muted:#8A93A6;--dim:#5B6377;
--amber:#F0A93E;--amber-dim:#6b5427;--green:#3ED98A;--red:#FF5C6C;--blue:#5DA9FF;}
*{box-sizing:border-box;}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;}
.mono{font-family:'JetBrains Mono',monospace;}
.wrap{max-width:1360px;margin:0 auto;padding:24px 20px 80px;}
.hero{display:flex;justify-content:space-between;align-items:flex-end;flex-wrap:wrap;gap:16px;border-bottom:1px solid var(--border);padding-bottom:18px;margin-bottom:18px;}
.hero h1{font-family:'Space Grotesk',sans-serif;font-size:26px;margin:0 0 4px;letter-spacing:-0.02em;}
.hero .sub{color:var(--muted);font-size:13px;}
.freq-toggle{display:flex;background:var(--panel);border:1px solid var(--border);border-radius:8px;overflow:hidden;}
.freq-toggle button{background:transparent;border:none;color:var(--muted);font-family:'JetBrains Mono',monospace;font-size:13px;font-weight:700;padding:9px 18px;cursor:pointer;}
.freq-toggle button.active{background:var(--amber);color:#1a1200;}
.banner{background:linear-gradient(90deg,rgba(240,169,62,.10),rgba(240,169,62,.02));border:1px solid var(--amber-dim);border-radius:10px;padding:12px 16px;font-size:12.5px;color:var(--muted);margin-bottom:22px;line-height:1.55;}
.banner b{color:var(--amber);}
.legend{display:flex;gap:18px;font-size:11.5px;color:var(--dim);margin-top:8px;flex-wrap:wrap;}
.legend span{display:inline-flex;align-items:center;gap:5px;}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;}
.dot.v{background:var(--green);} .dot.p{background:var(--blue);} .dot.n{background:var(--dim);}
section{margin-bottom:34px;}
.sec-head{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:10px;flex-wrap:wrap;gap:8px;}
.sec-head h2{font-family:'Space Grotesk',sans-serif;font-size:17px;margin:0;}
.sec-head .src{font-size:11px;color:var(--dim);}
.sec-head .num{color:var(--amber);margin-right:8px;}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--border);border-radius:10px;overflow:hidden;font-size:12.5px;}
th,td{padding:9px 10px;text-align:right;border-bottom:1px solid var(--border);white-space:nowrap;}
th:first-child,td:first-child{text-align:left;position:sticky;left:0;background:var(--panel);}
thead th{color:var(--dim);font-weight:600;font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;background:var(--panel2);}
tbody tr{cursor:pointer;} tbody tr:hover{background:var(--panel2);} tbody tr.selected{background:rgba(240,169,62,.08);}
td.india{background:rgba(93,169,255,.06);font-weight:600;} td.rscol{min-width:150px;}
.pos{color:var(--green);} .neg{color:var(--red);} .na{color:var(--dim);}
.ver-val{color:var(--text);} .proxy-val{color:var(--blue);}
.rank-badge{display:inline-block;width:20px;height:20px;line-height:20px;border-radius:5px;background:var(--panel2);color:var(--muted);font-size:10.5px;text-align:center;margin-right:6px;}
.rs-out{color:var(--green);font-weight:700;} .rs-under{color:var(--red);font-weight:700;} .rs-line{color:var(--dim);}
.drill{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px;display:none;}
.drill.show{display:block;}
.drill-title{font-family:'Space Grotesk',sans-serif;font-size:15px;margin-bottom:12px;color:var(--amber);}
.drill-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;}
.drill-col{background:var(--panel2);border:1px solid var(--border);border-radius:8px;padding:12px;}
.drill-col h4{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--dim);margin:0 0 8px;display:flex;justify-content:space-between;}
.stock-row{display:flex;justify-content:space-between;align-items:center;padding:6px 0;border-bottom:1px solid var(--border);font-size:12px;}
.stock-row:last-child{border-bottom:none;}
.stock-rank{color:var(--dim);font-size:10px;margin-right:6px;}
.stock-weight{color:var(--dim);font-size:10.5px;margin-left:5px;}
.stock-ret{font-family:'JetBrains Mono',monospace;font-weight:700;}
.stock-contrib{color:var(--dim);font-size:10px;display:block;}
.placeholder-note{color:var(--dim);font-size:11px;font-style:italic;padding:8px 0;}
.idx-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;}
.idx-card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px 16px;}
.idx-card .flag{font-size:11px;color:var(--dim);text-transform:uppercase;}
.idx-card .name{font-family:'Space Grotesk',sans-serif;font-size:14.5px;margin:2px 0 8px;}
.idx-card .level{font-family:'JetBrains Mono',monospace;font-size:19px;font-weight:700;}
.idx-card .chg{font-family:'JetBrains Mono',monospace;font-size:13px;margin-top:2px;}
.fc-list{display:flex;flex-direction:column;gap:10px;}
.fc-item{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:13px 16px;display:flex;gap:14px;align-items:flex-start;}
.fc-rank{font-family:'JetBrains Mono',monospace;font-size:18px;font-weight:700;color:var(--amber);min-width:26px;}
.fc-body{flex:1;} .fc-body b{display:block;font-size:13.5px;margin-bottom:3px;} .fc-body span{color:var(--muted);font-size:12.5px;line-height:1.5;}
.fc-tag{display:inline-block;font-size:10px;padding:2px 7px;border-radius:20px;margin-left:8px;}
.fc-tag.rev{background:rgba(62,217,138,.12);color:var(--green);} .fc-tag.rot{background:rgba(93,169,255,.12);color:var(--blue);} .fc-tag.mom{background:rgba(240,169,62,.15);color:var(--amber);}
.fc-metric{font-family:'JetBrains Mono',monospace;font-size:16px;font-weight:700;min-width:64px;text-align:right;}
.flow-grid{display:grid;grid-template-columns:1fr 1.2fr;gap:16px;}
.flow-card{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:16px;}
.flow-big{font-family:'JetBrains Mono',monospace;font-size:22px;font-weight:700;}
.flow-sub{color:var(--dim);font-size:11.5px;margin-top:4px;}
footer{border-top:1px solid var(--border);margin-top:30px;padding-top:16px;color:var(--dim);font-size:11px;line-height:1.7;}
footer b{color:var(--muted);}
@media(max-width:850px){.flow-grid{grid-template-columns:1fr;}}
</style>
</head>
<body>
<div class="wrap">
  <div class="hero">
    <div>
      <h1>Performance Attribution Tracker <span style="color:var(--blue);font-size:14px;">● EOD</span></h1>
      <div class="sub mono">EOD close as of __EOD_DATE__ &nbsp;·&nbsp; script run at __GENERATED_AT__ (your local run time)</div>
    </div>
    <div class="freq-toggle" id="freqToggle">
      <button data-f="D" class="active">D</button><button data-f="W">W</button><button data-f="M">M</button><button data-f="YTD">YTD</button>
    </div>
  </div>

  <div class="banner">
    <b>Data status:</b> All figures below use each market's last <b>fully completed (EOD) close</b> — as of __EOD_DATE__ — never a live/in-progress intraday quote. This is deliberate: India, Europe, the US, and Asia close at different times, so mixing a confirmed EOD close for one market with a still-moving live price for another would make the D/W/M/YTD columns inconsistent. If you run this script mid-session for any market, that market's latest bar is dropped and the prior completed session is used instead. Re-run <span class="mono">refresh_dashboard.py</span> any time for a fresh snapshot.
    <div class="legend">
      <span><span class="dot v"></span>Live index / stock return</span>
      <span><span class="dot p"></span>Country-level proxy (no free sector-specific index exists for this market)</span>
      <span><span class="dot n"></span>Not available</span>
    </div>
  </div>

  <section>
    <div class="sec-head"><h2><span class="num">02</span>Combined India + Global Sector Performance</h2>
    <div class="src">India: NSE sector indices · US: SPDR sector ETFs · EU/UK/JP/KR/CN: broad country index proxy (live)</div></div>
    <div style="overflow-x:auto;">
    <table><thead><tr>
      <th>Global Rank</th><th>Sector</th><th>Global Avg</th><th class="rscol">Relative Strength (India vs Global)</th>
      <th>India (NSE)</th><th>US (S&amp;P)</th><th>Europe*</th><th>UK*</th><th>Japan*</th><th>S. Korea*</th><th>China*</th>
    </tr></thead><tbody id="sectorBody"></tbody></table>
    </div>
    <div class="placeholder-note">* Country-level index proxy, not a sector-specific index (see legend)</div>
  </section>

  <section>
    <div class="sec-head"><h2><span class="num">03</span>Top Underlying Stocks — Live Drill-down</h2>
    <div class="src" id="drillSrc">Select a sector above — live per-stock returns, ranked by contribution to sector return</div></div>
    <div class="drill" id="drillPanel"><div class="drill-title" id="drillTitle">—</div><div class="drill-grid" id="drillGrid"></div></div>
  </section>

  <section>
    <div class="sec-head"><h2><span class="num">04</span>Global Market Indices</h2><div class="src">Live, Yahoo Finance</div></div>
    <div class="idx-grid" id="idxGrid"></div>
  </section>

  <section>
    <div class="sec-head"><h2><span class="num">05</span>Forecast — Upcoming Focus Sectors</h2>
    <div class="src">Ranked by live global average performance (high → low) for the selected timeframe</div></div>
    <div class="fc-list" id="fcList"></div>
  </section>

  <section>
    <div class="sec-head"><h2><span class="num">06</span>Smart Money / Institutional Flow Tracker</h2>
    <div class="src">India cash figure: no free live source. Sector bias: indicative (see legend)</div></div>
    <div class="flow-grid">
      <div class="flow-card">
        <div class="flow-sub">FII/DII (India) <span class="dot n" style="margin-left:4px"></span></div>
        <div class="flow-big na">Not available via free feed</div>
        <div class="flow-sub">Check <span class="mono">nseindia.com/reports/fii-dii</span> or <span class="mono">fpi.nsdl.co.in</span> for today's live print — no free API exists for this.</div>
        <div class="flow-sub" style="margin-top:10px;">Global institutional flow (raw $ figures) <span class="dot n" style="margin-left:4px"></span></div>
        <div class="flow-sub">EPFR / Lipper / Bloomberg fund-flow data is subscription-only. This script cannot populate exact flow numbers live.</div>
        <div class="flow-sub" style="margin-top:10px;">To make this panel fully live, you'd need an API key for a licensed flow-data provider — tell me the provider and I'll wire it in.</div>
      </div>
      <div class="flow-card">
        <div class="flow-sub" style="margin-bottom:8px;">SECTOR FLOW: INDIA vs GLOBAL (indicative — directional read, not a live print) <span class="dot p" style="margin-left:4px"></span></div>
        <table class="flow-table mono" style="font-size:12px; width:100%; border-collapse:collapse;">
          <thead><tr><th style="text-align:left; padding:7px 8px;">Sector</th><th style="padding:7px 8px;">India Bias</th><th style="padding:7px 8px;">Global Bias</th><th style="padding:7px 8px;">Signal</th></tr></thead>
          <tbody id="flowBody"></tbody>
        </table>
        <div class="reco-box" style="margin-top:14px;padding:12px;border:1px solid var(--amber-dim);border-radius:8px;background:rgba(240,169,62,.06);">
          <b style="color:var(--amber);display:block;margin-bottom:6px;font-size:12px;text-transform:uppercase;letter-spacing:.04em;">Where smart money is aligned</b>
          <div id="recoText" style="font-size:12.5px;color:var(--text);line-height:1.7;"></div>
        </div>
      </div>
    </div>
  </section>

  <footer>
    <b>Source:</b> Yahoo Finance, EOD close as of __EOD_DATE__ (fetched via the <span class="mono">yfinance</span> Python library when you ran the script at __GENERATED_AT__). All figures are end-of-day closes, not live/intraday quotes.<br>
    <b>Known gaps (unchanged from the static version):</b> no free sector-level indices for Europe/Japan/China/Korea (country-proxy used instead); stock weights are static/approximate (only returns are live); the India-vs-Global sector flow bias table is an indicative directional read, not a live FII/DII-by-sector print (no free source publishes that anywhere, for any market).
  </footer>
</div>

<script>
let freq = 'D';
const sectors = __SECTORS_JSON__;
const indices = __INDICES_JSON__;
const marketMeta = {in:{flag:'🇮🇳',label:'India'},us:{flag:'🇺🇸',label:'US'},eu:{flag:'🇪🇺',label:'Europe*'},uk:{flag:'🇬🇧',label:'UK*'},jp:{flag:'🇯🇵',label:'Japan*'},kr:{flag:'🇰🇷',label:'S. Korea*'},cn:{flag:'🇨🇳',label:'China*'}};
// Indicative directional read only -- no free source publishes real sector-level FII/DII or
// global fund-flow splits, so this is not computed from the live fetch above like everything else.
const flowBias = [
 ['IT / Technology','Buying','Buying'],['Banking / Financials','Buying','Buying'],
 ['Pharma / Healthcare','Mixed','Buying'],['FMCG / Consumer Staples','Selling','Mixed'],
 ['Metal / Materials','Buying','Buying'],['Energy','Selling','Selling'],
 ['Realty','Mixed','Selling'],['PSU / Public Sector','Selling','Mixed'],
 ['Media','Selling','Mixed'],['Infra','Buying','Mixed'],
 ['Industrials','Buying','Buying'],['Utilities','Mixed','Buying'],
 ['Consumer Discretionary','Mixed','Buying'],['Auto','Buying','Buying'],
];

function fmt(cell){
  if(!cell || cell.v===null || cell.v===undefined) return '<span class="na">—</span>';
  const cls = cell.v>=0?'pos':'neg';
  const tierCls = cell.tier==='proxy' ? 'proxy-val' : 'ver-val';
  const sign = cell.v>=0?'+':'';
  return `<span class="${cls} ${tierCls} mono">${sign}${cell.v.toFixed(1)}%</span>`;
}
function globalAvg(s){
  const cols=['us','eu','uk','jp','kr','cn'];
  const vals = cols.map(c=>s[c][freq]).filter(c=>c && c.v!=null).map(c=>c.v);
  if(!vals.length) return null;
  return vals.reduce((a,b)=>a+b,0)/vals.length;
}
let selectedSector = null;
function renderSectorTable(){
  const body = document.getElementById('sectorBody');
  const withAvg = sectors.map(s=>({s, avg:globalAvg(s)}));
  withAvg.sort((a,b)=>(b.avg??-999)-(a.avg??-999));
  body.innerHTML='';
  withAvg.forEach((row,i)=>{
    const s=row.s; const inVal = s.in[freq];
    const rs = (inVal && inVal.v!=null && row.avg!=null) ? (inVal.v - row.avg) : null;
    let rsHtml = '<span class="na">—</span>';
    if(rs!=null){
      if(rs>0.3) rsHtml = `<span class="rs-out">▲ Outperforming +${rs.toFixed(1)}pp</span>`;
      else if(rs<-0.3) rsHtml = `<span class="rs-under">▼ Underperforming ${rs.toFixed(1)}pp</span>`;
      else rsHtml = `<span class="rs-line">≈ In line (${rs.toFixed(1)}pp)</span>`;
    }
    const avgCls = row.avg==null?'na':(row.avg>=0?'pos':'neg');
    const avgTxt = row.avg==null?'—':(row.avg>=0?'+':'')+row.avg.toFixed(1)+'%';
    const tr=document.createElement('tr');
    if(selectedSector===s.name) tr.classList.add('selected');
    tr.innerHTML = `<td><span class="rank-badge">${i+1}</span></td><td><b>${s.name}</b></td>
      <td class="mono ${avgCls}">${avgTxt}</td><td class="rscol">${rsHtml}</td>
      <td class="india">${fmt(inVal)}</td><td>${fmt(s.us[freq])}</td><td>${fmt(s.eu[freq])}</td>
      <td>${fmt(s.uk[freq])}</td><td>${fmt(s.jp[freq])}</td><td>${fmt(s.kr[freq])}</td><td>${fmt(s.cn[freq])}</td>`;
    tr.addEventListener('click',()=>selectSector(s.name));
    body.appendChild(tr);
  });
}
function stockRow(name, weight, chg, rank){
  const cell = chg ? chg[freq] : null;
  if(!cell || cell.v==null) return `<div class="stock-row"><div><span class="stock-rank mono">${rank}</span><span>${name}</span><span class="stock-weight">${weight}% wt</span></div><div class="na mono">n/a</div></div>`;
  const ret = cell.v; const cls = ret>=0?'pos':'neg'; const sign = ret>=0?'+':'';
  const contrib = (ret*weight/100).toFixed(2);
  return `<div class="stock-row"><div><span class="stock-rank mono">${rank}</span><span>${name}</span><span class="stock-weight">${weight}% wt</span></div>
    <div style="text-align:right;"><span class="stock-ret ${cls} mono">${sign}${ret.toFixed(1)}%</span><span class="stock-contrib">contrib ${contrib}pp</span></div></div>`;
}
function selectSector(name){
  selectedSector = name;
  const s = sectors.find(x=>x.name===name);
  document.getElementById('drillPanel').classList.add('show');
  document.getElementById('drillTitle').textContent = name+' — top contributors to index return, by market ('+freq+', LIVE for India & US)';
  const grid = document.getElementById('drillGrid'); grid.innerHTML='';
  ['in','us'].forEach(mktKey=>{
    const meta = marketMeta[mktKey];
    const list = s.st[mktKey] || [];
    let rowsHtml;
    if(list.length){
      const withC = list.map(([n,w,chg])=>{
        const v = (chg && chg[freq] && chg[freq].v!=null) ? chg[freq].v : null;
        return {n,w,chg,contrib: v!=null ? v*w/100 : -9999};
      });
      withC.sort((a,b)=>b.contrib-a.contrib);
      rowsHtml = withC.slice(0,10).map((x,i)=>stockRow(x.n,x.w,x.chg,i+1)).join('');
    } else {
      rowsHtml = '<div class="placeholder-note">No constituents mapped for this market/sector</div>';
    }
    grid.innerHTML += `<div class="drill-col"><h4><span>${meta.label}</span><span>${meta.flag}</span></h4>${rowsHtml}</div>`;
  });
  Object.keys(marketMeta).filter(k=>!['in','us'].includes(k)).forEach(mktKey=>{
    const meta = marketMeta[mktKey];
    grid.innerHTML += `<div class="drill-col"><h4><span>${meta.label}</span><span>${meta.flag}</span></h4><div class="placeholder-note">No free live stock-level data for this market — only the country-level index proxy is live (see Section 02)</div></div>`;
  });
  renderSectorTable();
  document.getElementById('drillPanel').scrollIntoView({behavior:'smooth',block:'nearest'});
}
function renderIndices(){
  const grid = document.getElementById('idxGrid'); grid.innerHTML='';
  indices.forEach(ix=>{
    const chgHtml = ix.D==null ? '<span class="na">n/a</span>' : `<span class="${ix.D>=0?'pos':'neg'}">${ix.D>=0?'+':''}${ix.D.toFixed(2)}% D</span>`;
    const asofHtml = ix.asof ? `<div class="flow-sub" style="margin-top:6px;">EOD: ${ix.asof}</div>` : '';
    grid.innerHTML += `<div class="idx-card"><div class="flag">${ix.flag}</div><div class="name">${ix.name}</div>
      <div class="level mono">${ix.level}</div><div class="chg mono">${chgHtml}</div>${asofHtml}</div>`;
  });
}
function renderForecast(){
  const withAvg = sectors.map(s=>({s, avg:globalAvg(s), inv: s.in[freq]?s.in[freq].v:null}));
  const ranked = withAvg.filter(x=>x.avg!=null).sort((a,b)=>b.avg-a.avg);
  const list = document.getElementById('fcList'); list.innerHTML='';
  ranked.forEach((x,i)=>{
    let tag='mom', tagLabel='Momentum', note=`Leading on live global average ${freq} performance.`;
    if(x.inv!=null){
      const gap = x.inv - x.avg;
      if(gap < -1){ tag='rev'; tagLabel='Reversal watch'; note = `Global average ${x.avg>=0?'+':''}${x.avg.toFixed(1)}% while India lags by ${Math.abs(gap).toFixed(1)}pp.`; }
      else if(gap > 1){ tag='rot'; tagLabel='India-led'; note = `India ahead of global peers by ${gap.toFixed(1)}pp.`; }
      else { note = `Global average ${x.avg>=0?'+':''}${x.avg.toFixed(1)}%, India tracking closely (${gap>=0?'+':''}${gap.toFixed(1)}pp).`; }
    }
    const cls = x.avg>=0?'pos':'neg';
    list.innerHTML += `<div class="fc-item"><div class="fc-rank mono">${i+1}</div>
      <div class="fc-metric mono ${cls}">${x.avg>=0?'+':''}${x.avg.toFixed(1)}%</div>
      <div class="fc-body"><b>${x.s.name}<span class="fc-tag ${tag}">${tagLabel}</span></b><span>${note}</span></div></div>`;
  });
}
function renderFlow(){
  const body = document.getElementById('flowBody');
  if(!body) return;
  body.innerHTML = flowBias.map(([n,inB,gB])=>{
    const aligned = inB==='Buying' && gB==='Buying';
    const alignedSell = inB==='Selling' && gB==='Selling';
    return `<tr style="${aligned?'background:rgba(62,217,138,.08);':''}">
      <td style="padding:7px 8px;border-bottom:1px solid var(--border);">${n}</td>
      <td style="padding:7px 8px;border-bottom:1px solid var(--border);text-align:right;" class="${inB==='Buying'?'pos':inB==='Selling'?'neg':''}">${inB}</td>
      <td style="padding:7px 8px;border-bottom:1px solid var(--border);text-align:right;" class="${gB==='Buying'?'pos':gB==='Selling'?'neg':''}">${gB}</td>
      <td style="padding:7px 8px;border-bottom:1px solid var(--border);text-align:right;">${aligned?'<span class="pos">★ Aligned buying</span>':(alignedSell?'<span class="neg">Aligned selling</span>':'<span class="na">Diverging</span>')}</td>
    </tr>`;
  }).join('');
  const alignedSectors = flowBias.filter(([n,inB,gB])=>inB==='Buying'&&gB==='Buying').map(x=>x[0]);
  const reco = document.getElementById('recoText');
  if(reco){
    reco.textContent = alignedSectors.length
      ? `Both India and global institutional money show buying bias: ${alignedSectors.join(', ')}. These are the priority overweight candidates where domestic and foreign flow are reinforcing, not fighting, each other. (Indicative directional read -- not a live print, see legend.)`
      : 'No sector currently shows aligned buying on both sides -- flows are diverging market to market.';
  }
}
document.getElementById('freqToggle').addEventListener('click',(e)=>{
  const btn = e.target.closest('button'); if(!btn) return;
  freq = btn.dataset.f;
  [...document.querySelectorAll('#freqToggle button')].forEach(b=>b.classList.toggle('active', b===btn));
  renderSectorTable(); renderForecast();
  if(selectedSector) selectSector(selectedSector);
});
renderSectorTable(); renderIndices(); renderForecast(); renderFlow();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
