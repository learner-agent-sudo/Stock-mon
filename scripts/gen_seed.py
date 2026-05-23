"""Generate seed_holdings.csv from the user's ticker lists.

Run once to produce the CSV, then discard this script.
"""
import csv
from pathlib import Path

HEADER = ["symbol", "exchange", "currency", "category", "description",
          "target_price", "drop_pct_threshold", "quantity"]

# --- HK tickers: pad to 4 digits, add .HK ---
HK_CODES = {
    "1299": "AIA Group",
    "1810": "Xiaomi",
    "3690": "Meituan",
    "0388": "HKEX",
    "0700": "Tencent",
    "0823": "Link REIT",
    "9618": "JD.com",
    "9888": "Baidu",
}

# --- Singapore tickers ---
SG_TICKERS = {
    "AJBU.SI": "Keppel DC REIT",
    "C6L.SI": "Singapore Airlines",
    "N2IU.SI": "Mapletree Pan Asia Commercial",
}

# --- Canadian tickers (Group A) ---
CA_GROUP_A = {
    "ATD.TO": "Alimentation Couche-Tard",
    "BCE.TO": "BCE Inc",
    "BEPC.TO": "Brookfield Renewable",
    "BMO.TO": "Bank of Montreal",
    "CNQ.TO": "Canadian Natural Resources",
    "CSU.TO": "Constellation Software",
    "ENB.TO": "Enbridge",
    "IFC.TO": "Intact Financial",
    "SLF.TO": "Sun Life Financial",
    "TOU.TO": "Tourmaline Oil",
    "XDIV.TO": "iShares Core Dividend ETF",
    "ZWH.TO": "BMO US High Div Hedged ETF",
}

# --- Canadian tickers (Group B) ---
CA_GROUP_B = {
    "FTS.TO": "Fortis",
    "SU.TO": "Suncor Energy",
    "MFC.TO": "Manulife Financial",
    "OTEX.TO": "Open Text",
    "KXS.TO": "Kinaxis",
    "FFH.TO": "Fairfax Financial",
    "BIP-UN.TO": "Brookfield Infrastructure Partners",
    "QSR.TO": "Restaurant Brands International",
    "CRT-UN.TO": "CT REIT",
    "GSY.TO": "goeasy",
    "BNS.TO": "Bank of Nova Scotia",
    "CM.TO": "CIBC",
    "TD.TO": "TD Bank",
    "CU.TO": "Canadian Utilities",
    "SIS.TO": "Savaria",
    "POW.TO": "Power Corp",
    "DOL.TO": "Dollarama",
    "REAL.TO": "Real Matters",
    "GEI.TO": "Gibson Energy",
    "BN.TO": "Brookfield Corp",
    "PXT.TO": "Parex Resources",
    "TPZ.TO": "Topaz Energy",
    "NTR.TO": "Nutrien",
    "CPH.TO": "Cipher Pharmaceuticals",
    "CNR.TO": "CN Rail",
    "TSU.TO": "Trisura Group",
    "VDY.TO": "Vanguard FTSE Canadian Div ETF",
    "FIE.TO": "iShares CDN Financial Monthly Inc ETF",
    "PSD.TO": "Pulse Seismic",
    "DRM.TO": "Dream Unlimited",
    "TC.TO": "TC Energy",
    "GIB-A.TO": "CGI Inc",
    "VITL-UN.TO": "Vital Energy Trust",
    "APR-UN.TO": "Automotive Properties REIT",
    "DIR-UN.TO": "Dream Industrial REIT",
}

# --- US tickers (Group A) ---
US_GROUP_A = [
    ("AAPL", "Apple"),
    ("ABNB", "Airbnb"),
    ("ADBE", "Adobe"),
    ("AGNT", "AGNT Inc"),
    ("AI", "C3.ai"),
    ("AMT", "American Tower"),
    ("AMZN", "Amazon"),
    ("APPN", "Appian"),
    ("ASML", "ASML Holding"),
    ("ASST", "Asset Entities"),
    ("ATG", "ACI Global"),
    ("AVGO", "Broadcom"),
    ("AXON", "Axon Enterprise"),
    ("AXP", "American Express"),
    ("BAND", "Bandwidth"),
    ("BB", "BlackBerry"),
    ("BBY", "Best Buy"),
    ("BFLY", "Butterfly Network"),
    ("BL", "BlackLine"),
    ("BOC", "Bank of Communications"),
    ("BRK-B", "Berkshire Hathaway B"),
    ("BTI", "British American Tobacco"),
    ("BYND", "Beyond Meat"),
    ("CDNS", "Cadence Design Systems"),
    ("CGS", "CGS International"),
    ("CHWY", "Chewy"),
    ("CLOV", "Clover Health"),
    ("CLPT", "ClearPoint Neuro"),
    ("CMBMF", "Canadian Western Bank OTC"),
    ("CMPS", "COMPASS Pathways"),
    ("CNTA", "Centessa Pharmaceuticals"),
    ("COIN", "Coinbase"),
    ("COST", "Costco"),
    ("CRAI", "CRA International"),
    ("CRBU", "Caribou Biosciences"),
    ("CRM", "Salesforce"),
    ("CRSP", "CRISPR Therapeutics"),
    ("CRWD", "CrowdStrike"),
    ("CURI", "CuriosityStream"),
    ("DAR", "Darling Ingredients"),
    ("DBI", "Designer Brands"),
    ("DDOG", "Datadog"),
    ("DFH", "Dream Finders Homes"),
    ("DIBS", "1stDibs"),
    ("DIS", "Walt Disney"),
    ("DNA", "Ginkgo Bioworks"),
    ("DOCU", "DocuSign"),
    ("DPZ", "Dominos Pizza"),
    ("DXCM", "DexCom"),
    ("EDIT", "Editas Medicine"),
    ("EEFT", "Euronet Worldwide"),
    ("ENPH", "Enphase Energy"),
    ("EPAM", "EPAM Systems"),
    ("EPR", "EPR Properties"),
    ("ESRT", "Empire State Realty"),
    ("ESTC", "Elastic"),
    ("ETSY", "Etsy"),
    ("FLGT", "Fulgent Genetics"),
    ("FROG", "JFrog"),
    ("FTHM", "Fathom Holdings"),
    ("FTNT", "Fortinet"),
    ("FUN", "Six Flags Entertainment"),
    ("FVRR", "Fiverr"),
    ("GLOB", "Globant"),
    ("GM", "General Motors"),
    ("GNP", "General Norte Potosina"),
    ("GOOG", "Alphabet"),
    ("GTM", "GTM Holdings"),
    ("GXO", "GXO Logistics"),
    ("HCAT", "Health Catalyst"),
    ("HD", "Home Depot"),
    ("HSY", "Hershey"),
    ("IDXX", "IDEXX Laboratories"),
    ("IPCO", "International Petroleum"),
    ("ISRG", "Intuitive Surgical"),
    ("JMIA", "Jumia Technologies"),
    ("JOE", "St. Joe Company"),
    ("KNSA", "Kiniksa Pharmaceuticals"),
    ("KNSL", "Kinsale Capital"),
    ("KO", "Coca-Cola"),
    ("KSI", "Karooooo"),
    ("LAND", "Gladstone Land"),
    ("LMND", "Lemonade"),
    ("LOB", "Live Oak Bancshares"),
    ("LPSN", "LivePerson"),
    ("LRCX", "Lam Research"),
    ("LTCH", "Latch"),
    ("MA", "Mastercard"),
    ("MASI", "Masimo"),
    ("MDB", "MongoDB"),
    ("MDI", "MDI Group"),
    ("MEDP", "Medpace"),
    ("MELI", "MercadoLibre"),
    ("META", "Meta Platforms"),
    ("MITK", "Mitek Systems"),
    ("MOMO", "Hello Group"),
    ("MPT", "Medical Properties Trust"),
    ("MSFT", "Microsoft"),
    ("MTCH", "Match Group"),
    ("MUSA", "Murphys USA"),
    ("NBIX", "Neurocrine Biosciences"),
    ("NCNO", "nCino"),
    ("NEE", "NextEra Energy"),
    ("NET", "Cloudflare"),
    ("NFLX", "Netflix"),
    ("NIO", "NIO"),
    ("NNOX", "Nano-X Imaging"),
    ("NOW", "ServiceNow"),
    ("NVDA", "NVIDIA"),
    ("O", "Realty Income"),
    ("ODFL", "Old Dominion Freight"),
    ("OKTA", "Okta"),
    ("OM", "Outset Medical"),
    ("ONL", "Orion Office REIT"),
    ("OPEN", "Opendoor Technologies"),
    ("ORCL", "Oracle"),
    ("PAYO", "Payoneer"),
    ("PENG", "Penguin Solutions"),
    ("PFE", "Pfizer"),
    ("PGR", "Progressive"),
    ("PINS", "Pinterest"),
    ("PLTR", "Palantir"),
    ("PUBM", "PubMatic"),
    ("PWH", "Penumbra"),
    ("PYPL", "PayPal"),
    ("QS", "QuantumScape"),
    ("QTRH", "Quarterhill"),
    ("QTUM", "Defiance Quantum ETF"),
    ("RBLX", "Roblox"),
    ("RKT", "Rocket Companies"),
    ("RMNI", "Rimini Street"),
    ("ROKU", "Roku"),
    ("RXO", "RXO"),
    ("SAM", "Boston Beer"),
    ("SBUX", "Starbucks"),
    ("SDGR", "Schrodinger"),
    ("SE", "Sea Limited"),
    ("SEDG", "SolarEdge Technologies"),
    ("SHOP", "Shopify"),
    ("SJ", "Scientia Global"),
    ("SKLZ", "Skillz"),
    ("SKYH", "Sky Harbour"),
    ("SMG", "Scotts Miracle-Gro"),
    ("SMID", "Smith-Midland"),
    ("SNOW", "Snowflake"),
    ("SOFI", "SoFi Technologies"),
    ("SPG", "Simon Property Group"),
    ("SPXU", "ProShares UltraPro Short S&P500"),
    ("TDOC", "Teladoc Health"),
    ("TEAM", "Atlassian"),
    ("TGT", "Target"),
    ("TMDX", "TransMedics"),
    ("TPICQ", "TPI Composites"),
    ("TRUP", "Trupanion"),
    ("TSLA", "Tesla"),
    ("TTD", "The Trade Desk"),
    ("TTWO", "Take-Two Interactive"),
    ("TWST", "Twist Bioscience"),
    ("TXG", "10x Genomics"),
    ("TXT", "Textron"),
    ("U", "Unity Software"),
    ("ULTA", "Ulta Beauty"),
    ("UPST", "Upstart"),
    ("V", "Visa"),
    ("VINP", "Vinci Partners"),
    ("VRSK", "Verisk Analytics"),
    ("VTRS", "Viatris"),
    ("WEX", "WEX Inc"),
    ("WFC", "Wells Fargo"),
    ("WHR", "Whirlpool"),
    ("WINEL", "Winel Industrial"),
    ("WIX", "Wix.com"),
    ("WMT", "Walmart"),
    ("XPO", "XPO"),
    ("XYZ", "Block Inc"),
    ("YAMZ", "Yamz Corp"),
    ("ZBRA", "Zebra Technologies"),
    ("ZM", "Zoom Video"),
    ("ZS", "Zscaler"),
]

# --- US tickers (Group B) ---
US_GROUP_B = [
    ("PLD", "Prologis"),
    ("MRVL", "Marvell Technology"),
    ("QYLG", "Global X Nasdaq 100 Covered Call Growth ETF"),
    ("OVL", "Overlay Shares"),
    ("QYLD", "Global X Nasdaq 100 Covered Call ETF"),
    ("FTHI", "First Trust BuyWrite Income ETF"),
    ("EOG", "EOG Resources"),
    ("ACN", "Accenture"),
    ("DG", "Dollar General"),
    ("T", "AT&T"),
    ("VYM", "Vanguard High Dividend Yield ETF"),
    ("VIG", "Vanguard Dividend Appreciation ETF"),
    ("VOO", "Vanguard S&P 500 ETF"),
    ("IVV", "iShares Core S&P 500 ETF"),
    ("SPY", "SPDR S&P 500 ETF"),
    ("QQQM", "Invesco Nasdaq 100 ETF"),
    ("SCHD", "Schwab US Dividend Equity ETF"),
    ("HYLB", "Xtrackers USD High Yield Bond ETF"),
    ("IGIB", "iShares Trust IG Corp Bond ETF"),
    ("XLP", "Consumer Staples Select SPDR"),
    ("DLO", "DLocal"),
    ("X", "United States Steel"),
    ("PTON", "Peloton"),
    ("WSO", "Watsco"),
    ("RGSI", "RGSI"),
    ("SES", "SES AI"),
]

OUT = Path(__file__).resolve().parent.parent / "data" / "seed_holdings.csv"

rows = []

def add(symbol, exchange, currency, cat, desc, target="", drop="", qty=""):
    rows.append({
        "symbol": symbol, "exchange": exchange, "currency": currency,
        "category": cat, "description": desc,
        "target_price": target, "drop_pct_threshold": drop, "quantity": qty,
    })

# HK
for code, desc in HK_CODES.items():
    add(f"{code}.HK", "SEHK", "HKD", "HOLDING", desc)

# Singapore
for sym, desc in SG_TICKERS.items():
    add(sym, "SGX", "SGD", "HOLDING", desc)

# Canadian Group A
for sym, desc in CA_GROUP_A.items():
    add(sym, "TSX", "CAD", "HOLDING", desc)

# US Group A
for sym, desc in US_GROUP_A:
    add(sym, "US", "USD", "HOLDING", desc)

# Canadian Group B
for sym, desc in CA_GROUP_B.items():
    add(sym, "TSX", "CAD", "WATCHLIST", desc)

# US Group B
for sym, desc in US_GROUP_B:
    add(sym, "US", "USD", "WATCHLIST", desc)

with open(OUT, "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=HEADER)
    w.writeheader()
    w.writerows(rows)

print(f"Wrote {len(rows)} rows to {OUT}")
