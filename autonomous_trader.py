#!/usr/bin/env python3
"""
Ultimate Trader - Autonomer Modus

LAEUFT OHNE INTERAKTION:
- Taeglich 09:00 via Cronjob
- Scannt nach ETFs/Aktien via yfinance
- Fuehrt simulierte Trades durch
- Speichert Portfolio in portfolio_state.json
- Generiert Report als Markdown-Datei
- Versucht E-Mail-Versand (SMTP)
"""

import os
import sys
import json
import logging
import smtplib
import time
from datetime import datetime, date, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from typing import Optional

import yfinance as yf
import requests
from dotenv import load_dotenv

# Load .env
load_dotenv()

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("autonomous_trader")

WORK_DIR = Path(__file__).parent.resolve()
STATE_FILE = WORK_DIR / "portfolio_state.json"
REPORTS_DIR = WORK_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

# Konfiguration
START_CAPITAL = float(os.getenv("START_CAPITAL", "10000.0"))
DAILY_LIMIT = float(os.getenv("DAILY_SPENDING_LIMIT", "2000.0"))
STOP_LOSS = float(os.getenv("STOP_LOSS_THRESHOLD", "0.05"))
FEE_RATE = float(os.getenv("FEE_RATE", "0.001"))

# Core/Satellite/Bonds-Allokation (Norwegen-Modell: 70% Aktien, 30% Anleihen)
CORE_ALLOCATION = float(os.getenv("CORE_ALLOCATION", "0.70"))      # 70% Aktien gesamt
SATELLITE_ALLOCATION = float(os.getenv("SATELLITE_ALLOCATION", "0.30"))  # davon 30% Satellite
BOND_ALLOCATION = float(os.getenv("BOND_ALLOCATION", "0.30"))       # 30% Anleihen (Norwegen-Modell)
EQUITY_ALLOCATION = float(os.getenv("EQUITY_ALLOCATION", "0.70"))   # 70% Aktien gesamt
CORE_BETA_MAX = 0.90   # Beta ≤ 0.9 für Core (Minimum-Volatility, defensiv)
CORE_BETA_MIN = 0.40   # Beta ≥ 0.4 (keine extrem illiquiden Assets)
SATELLITE_BETA_MIN = 0.90  # Satellite: Beta 0.9–1.2 (moderat)

# Watchlist — Research-backed Core/Satellite (ISINs aus DeepSearch-Recherche 29.04.)
# CORE (Low-Beta, Minimum-Volatility, defensiv)
CORE_TICKERS = [
    "IQQ0.DE",    # iShares Edge MSCI World Min Vol (IE00B8FHGS14, TER 0.30%)
    "XDEB.DE",    # Xtrackers MSCI World Min Vol (IE00BL25JN58, TER 0.25%)
    "HDLV.DE",    # Invesco S&P500 HighDiv LowVol (IE00BWTN6Y99)
    "SPHD",       # Invesco S&P500 HighDiv LowVol US
    "MVEE.DE",    # iShares Edge MSCI Europe Min Vol
    "XLU",        # Utilities Select Sector SPDR (defensiv, Beta ~0.6)
    "XLV",        # Healthcare Select Sector SPDR (defensiv, Beta ~0.7)
    "EUNL.DE",    # iShares Core MSCI World (TV-Beta 0.80 → Core-geeignet)
    "XD9U.DE",    # iShares Core S&P 500 (TV-Beta 0.65 → Core-geeignet)
]

# SATELLITE (breite Markt-ETFs, moderate Beta für Rendite-Chancen)
SATELLITE_TICKERS = [
    "VWCE.DE",    # Vanguard FTSE All-World UCITS ETF
    "EUNL.DE",    # iShares Core MSCI World (Beta ~1.0)
    "XD9U.DE",    # iShares Core S&P 500
    "SPY",        # S&P 500 (US)
    "VTI",        # Vanguard Total Stock Market
    "QQQ",        # Nasdaq-100 (moderates Beta, Tech-Fokus)
    "QDVE.DE",    # iShares MSCI USA Quality Factor
]

# BONDS (30% Allokation — Norwegen-Modell: Staats- + Unternehmensanleihen)
BOND_TICKERS = [
    "DBXG.DE",    # Xtrackers II Global Government Bond UCITS ETF 1C EUR Hedged (LU0378818131)
    "LYQ2.DE",    # Amundi Core EUR Corporate Bond UCITS ETF EUR Acc (LU1434522988)
]

# DIVIDENDEN-QUALITÄTS-WATCHLIST (Core-geeignet, stabil wie Low-Beta)
DIVIDEND_TICKERS = [
    "TDIV.DE",    # VanEck Morningstar Developed Markets Dividend Leaders (NL0011683594, TER 0.38%)
    "VHYL.DE",    # Vanguard FTSE All-World High Dividend Yield (IE00B8GKDB10, TER 0.29%)
    "SD3P.DE",    # iShares STOXX Global Select Dividend 100 (DE000A0F5UH1, TER 0.46%)
]

# US-Exposure-Map (geschätzte US-Gewichtung in % für US-Tracker)
US_EXPOSURE_MAP = {
    "SPY": 100, "VTI": 100, "QQQ": 100, "QDVE.DE": 100,
    "XD9U.DE": 100, "SPHD": 100, "HNDL": 85, "HDLV.DE": 100,
    "EUNL.DE": 69, "IQQ0.DE": 65, "XDEB.DE": 65, "VWCE.DE": 59,
    "XLU": 100, "XLV": 100, "MVEE.DE": 0,
    "TDIV.DE": 35, "VHYL.DE": 37, "SD3P.DE": 25,
    "DBXG.DE": 0, "LYQ2.DE": 0,
}

# Alle Ticker (für Info-Screen)
SCAN_TICKERS = CORE_TICKERS + SATELLITE_TICKERS + BOND_TICKERS + DIVIDEND_TICKERS


def load_portfolio() -> dict:
    """Laedt Portfolio aus JSON. Erstellt neues, falls nicht vorhanden."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            logger.info("Portfolio geladen: €%.2f Cash, %d Positionen",
                        data.get("cash", START_CAPITAL),
                        len(data.get("positions", {})))
            return data
        except (json.JSONDecodeError, IOError) as exc:
            logger.warning("Fehler beim Laden: %s. Neues Portfolio wird erstellt.", exc)

    # Frisches Start-Portfolio
    portfolio = {
        "cash": START_CAPITAL,
        "total_invested": 0.0,
        "positions": {},
        "history": [],
        "trade_count": 0,
        "last_run": None,
        "today_spent": 0.0,
        "today_date": str(date.today()),
    }
    save_portfolio(portfolio)
    return portfolio


def save_portfolio(portfolio: dict) -> None:
    """Speichert Portfolio atomisch."""
    temp = STATE_FILE.with_suffix(".tmp")
    try:
        with open(temp, "w", encoding="utf-8") as f:
            json.dump(portfolio, f, indent=2, default=str)
        os.replace(str(temp), str(STATE_FILE))
        logger.info("Portfolio gespeichert: €%.2f Cash, %d Positionen",
                    portfolio["cash"], len(portfolio["positions"]))
    except IOError as exc:
        logger.error("Speichern fehlgeschlagen: %s", exc)


def reset_daily_budget(portfolio: dict) -> None:
    """Zuruecksetzen des Tageslimits bei Datumwechsel."""
    today = str(date.today())
    if portfolio.get("today_date") != today:
        portfolio["today_date"] = today
        portfolio["today_spent"] = 0.0
        logger.info("Tagesbudget auf €%.2f zurueckgesetzt.", DAILY_LIMIT)


def get_price(symbol: str) -> Optional[float]:
    """Holt aktuellen Preis von yfinance."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="1d")
        if not hist.empty:
            price = float(hist["Close"].iloc[-1])
            return round(price, 2)
    except Exception as exc:
        logger.debug("Kursabfrage %s fehlgeschlagen: %s", symbol, exc)
    return None


def get_info(symbol: str, tv_beta_cache: dict = None) -> dict:
    """Holt Basis-Infos (Beta, Sektor, etc.). Nutzt TradingView-Beta-Daten wenn verfügbar,
    yfinance als Fallback, Volatilitäts-Schätzung als letzten Fallback."""
    beta = None
    vol_w = None
    vol_m = None

    # 1. TradingView-Beta (primär, falls Cache vorhanden)
    #    Normalisiere Symbol: Strip .DE für TV-Abfrage, außer es ist bereits ohne Suffix
    tv_symbol = symbol.replace(".DE", "") if ".DE" in symbol else symbol
    if tv_beta_cache and tv_symbol in tv_beta_cache:
        tv = tv_beta_cache[tv_symbol]
        beta = tv.get("beta_1y")
        vol_w = tv.get("vol_w")
        vol_m = tv.get("vol_m")
        if beta is not None:
            logger.debug("TradingView-Beta für %s: %.3f", symbol, beta)

    # 2. yfinance-Fallback (nur wenn TV kein Beta hatte)
    if beta is None:
        try:
            ticker = yf.Ticker(symbol)
            info = ticker.info
            beta = info.get("beta")
        except Exception:
            pass

    # 3. Volatilitäts-basierte Beta-Schätzung (letzter Fallback)
    #    Beta ≈ Vol(ETF) / Vol(Markt). Nutze VolW als Proxy.
    #    XDEB als Benchmark: VolW=0.50%, Beta=0.34 → Markt-VolW ≈ 0.50/0.34 = 1.47
    if beta is None and vol_w is not None and vol_w > 0:
        MARKET_VOL_W = 1.47  # Geschätzte Markt-Volatilität (Woche)
        beta = min(vol_w / MARKET_VOL_W, 0.90)  # Nach oben begrenzt
        logger.debug("Volatilitäts-Schätzung für %s: Beta=%.3f (VolW=%.3f%%)", symbol, beta, vol_w)

    # Yfinance-Infos für Name/PE/Sektor
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info
        return {
            "beta": beta,
            "sector": info.get("sector", "Unbekannt"),
            "name": info.get("shortName", symbol),
            "trailing_pe": info.get("trailingPE"),
            "vol_w": vol_w,
            "vol_m": vol_m,
        }
    except Exception as exc:
        logger.debug("Info-Abfrage %s fehlgeschlagen: %s", symbol, exc)
        return {"beta": beta, "name": symbol, "vol_w": vol_w, "vol_m": vol_m}


def estimate_us_exposure(portfolio: dict) -> tuple:
    """Schätzt US-Anteil des Aktien-Portfolios anhand der US_EXPOSURE_MAP.
    Returns: (us_pct, us_value, total_equity_value, warning_string_or_none)"""
    total_equity = 0.0
    us_value = 0.0
    for sym, pos in portfolio["positions"].items():
        tier = pos.get("tier", "core")
        if tier == "bonds":
            continue  # Anleihen zählen nicht zum Aktien-Exposure
        val = pos["shares"] * pos.get("last_price", pos["avg_price"])
        total_equity += val
        us_pct = US_EXPOSURE_MAP.get(sym, 50)  # Default: 50% falls unbekannt
        us_value += val * (us_pct / 100)

    if total_equity > 0:
        us_overall_pct = (us_value / total_equity) * 100
    else:
        us_overall_pct = 0.0

    warning = None
    if us_overall_pct > 65:
        warning = f"⚠️ US-Anteil {us_overall_pct:.0f}% überschreitet 65%-Schwelle (Ziel: ≤55%)"
    return us_overall_pct, us_value, total_equity, warning



def fetch_tv_beta(tickers: list = None) -> dict:
    """Ruft Beta-Daten von TradingView ETF Screener API ab.
    Nutzt Name-basierte Suche für exaktes Matching.
    Args:
        tickers: Liste von TV-Symbolen (ohne .DE). Wenn None → CORE+SATELLITE.
    Returns: {"TICKER": {"beta_1y": float, "vol_w": float, "vol_m": float}, ...}
    Leerer Dict bei Timeout/Fehler — yfinance-Fallback greift dann."""
    try:
        url = 'https://scanner.tradingview.com/global/scan'
        if tickers is None:
            raw = [t.replace(".DE", "") for t in CORE_TICKERS + SATELLITE_TICKERS + BOND_TICKERS + DIVIDEND_TICKERS]
            tickers = list(dict.fromkeys(raw))
        else:
            tickers = list(dict.fromkeys(tickers))  # Dedup

        payload = {
            'filter': [
                {'left': 'name', 'operation': 'in_range', 'right': tickers},
            ],
            'options': {'lang': 'de'},
            'symbols': {'query': {'types': []}},
            'columns': ['name', 'beta_1_year', 'beta_3_year', 'beta_5_year',
                        'Volatility.W', 'Volatility.M'],
            'range': [0, 100]
        }
        resp = requests.post(url, json=payload, timeout=15)
        data = resp.json()

        cache = {}
        for item in data.get('data', []):
            d = item['d']
            name = d[0]
            b1y = d[1]
            if name not in cache or (cache[name]['beta_1y'] is None and b1y is not None):
                cache[name] = {
                    "beta_1y": b1y,
                    "beta_3y": d[2],
                    "beta_5y": d[3],
                    "vol_w": d[4],
                    "vol_m": d[5],
                }

        logger.info("TradingView-Beta-Cache: %d/%d Ticker gefunden", len(cache), len(tickers))
        return cache

    except Exception as exc:
        logger.warning("TradingView-Beta-Abruf fehlgeschlagen: %s. Verwende yfinance-Fallback.", exc)
        return {}


def discover_etfs() -> dict:
    """Findet neue ETFs per TradingView Beta-Range-Filter.
    Returns: {"core": [...], "satellite": [...]} mit beta, vol_w, name, exchange.
    Leere Listen bei Timeout/Fehler."""
    url = 'https://scanner.tradingview.com/global/scan'
    COLUMNS = ['name', 'description', 'beta_1_year', 'beta_3_year', 'beta_5_year',
               'Volatility.W', 'Volatility.M', 'exchange', 'currency']

    result = {"core": [], "satellite": []}

    def _scan(beta_range: list, exchanges: list, label: str, max_results: int = 25):
        try:
            payload = {
                'filter': [
                    {'left': 'type', 'operation': 'equal', 'right': 'fund'},
                    {'left': 'subtype', 'operation': 'equal', 'right': 'etf'},
                    {'left': 'beta_1_year', 'operation': 'in_range', 'right': beta_range},
                    {'left': 'exchange', 'operation': 'in_range', 'right': exchanges},
                ],
                'options': {'lang': 'de'},
                'symbols': {'query': {'types': []}},
                'columns': COLUMNS,
                'range': [0, max_results]
            }
            resp = requests.post(url, json=payload, timeout=15)
            data = resp.json()

            for item in data.get('data', []):
                d = item['d']
                b1y = d[2]
                if b1y is None:
                    continue  # Nur ETFs mit Beta-Daten
                result[label].append({
                    "name": d[0],           # TV-Symbol (z.B. "GQWD", "ESGU")
                    "description": d[1] or d[0],
                    "beta_1y": b1y,
                    "beta_3y": d[3],
                    "beta_5y": d[4],
                    "vol_w": d[5],
                    "vol_m": d[6],
                    "exchange": d[7],
                    "currency": d[8],
                })
        except Exception as exc:
            logger.warning("ETF-Discovery (%s) fehlgeschlagen: %s", label, exc)

    # Core-Scan: Beta 0.4–0.9, alle Hauptbörsen
    _scan([0.40, 0.90], ['XETR', 'NYSE', 'NASDAQ'], "core")
    # Satellite-Scan: Beta 0.9–1.2
    _scan([0.90, 1.20], ['XETR', 'NYSE', 'NASDAQ'], "satellite")

    logger.info("ETF-Discovery: %d Core / %d Satellite gefunden",
                len(result["core"]), len(result["satellite"]))
    return result


def calculate_buy_shares(amount: float, price: float) -> tuple:
    """Berechnet Stueckzahl inkl. 0.1% Gebuehr."""
    effective_price = price * (1 + FEE_RATE)
    shares = amount / effective_price
    fees = amount - (shares * price)
    return round(shares, 4), round(fees, 2), amount


def execute_buy(portfolio: dict, symbol: str, amount: float, tier: str = "core") -> Optional[dict]:
    """Fuehrt simulierten Kauf aus."""
    price = get_price(symbol)
    if price is None:
        logger.warning("Kein Preis fuer %s verfuegbar. Kauf abgebrochen.", symbol)
        return None

    reset_daily_budget(portfolio)
    if portfolio["today_spent"] + amount > DAILY_LIMIT:
        logger.warning("Tageslimit ueberschritten. Kauf abgebrochen.")
        return None

    if amount > portfolio["cash"]:
        logger.warning("Nicht genug Cash (€%.2f verfuegbar). Kauf abgebrochen.", portfolio["cash"])
        return None

    shares, fees, total = calculate_buy_shares(amount, price)

    # Portfolio aktualisieren
    portfolio["cash"] -= total
    portfolio["today_spent"] += amount
    portfolio["trade_count"] = portfolio.get("trade_count", 0) + 1

    pos = portfolio["positions"].get(symbol, {"shares": 0.0, "avg_price": 0.0, "tier": tier})
    old_shares = pos["shares"]
    old_avg = pos["avg_price"]

    new_shares = old_shares + shares
    new_avg = (old_shares * old_avg + shares * price) / new_shares if new_shares > 0 else 0.0

    portfolio["positions"][symbol] = {
        "shares": new_shares,
        "avg_price": round(new_avg, 4),
        "last_price": price,
        "tier": tier,  # Core vs. Satellite
    }

    trade = {
        "timestamp": datetime.now().isoformat(),
        "symbol": symbol,
        "side": "buy",
        "shares": shares,
        "price": price,
        "fees": fees,
        "amount": amount,
        "tier": tier,
    }
    portfolio["history"].append(trade)
    portfolio["total_invested"] = portfolio.get("total_invested", 0) + amount

    save_portfolio(portfolio)
    logger.info("GEKAUFT [%s]: %s | %.4f Stk @ €%.2f | Summe: €%.2f | Gebuehr: €%.2f",
                tier.upper(), symbol, shares, price, amount, fees)
    return trade


def execute_sell(portfolio: dict, symbol: str, shares: Optional[float] = None) -> Optional[dict]:
    """Fuehrt simulierten Verkauf aus (default: komplette Position)."""
    if symbol not in portfolio["positions"]:
        return None

    price = get_price(symbol)
    if price is None:
        logger.warning("Kein Preis fuer %s verfuegbar. Verkauf abgebrochen.", symbol)
        return None

    pos = portfolio["positions"][symbol]
    max_shares = pos["shares"]
    sell_shares = shares if shares is not None and shares <= max_shares else max_shares

    gross = sell_shares * price
    fees = gross * FEE_RATE
    net = gross - fees

    # Portfolio aktualisieren
    portfolio["cash"] += net
    portfolio["trade_count"] = portfolio.get("trade_count", 0) + 1

    remaining = max_shares - sell_shares
    if remaining <= 0:
        del portfolio["positions"][symbol]
    else:
        pos["shares"] = remaining
        portfolio["positions"][symbol] = pos

    trade = {
        "timestamp": datetime.now().isoformat(),
        "symbol": symbol,
        "side": "sell",
        "shares": sell_shares,
        "price": price,
        "fees": fees,
        "net": net,
    }
    portfolio["history"].append(trade)
    save_portfolio(portfolio)

    logger.info("VERKAUFT: %s | %.4f Stk @ €%.2f | Netto: €%.2f | Gebuehr: €%.2f",
                symbol, sell_shares, price, net, fees)
    return trade


def check_stop_loss(portfolio: dict) -> list:
    """Prueft alle Positionen auf Stop-Loss (-5%). Gibt Liste der Verkaeufe zurueck."""
    triggered = []
    for symbol, pos in list(portfolio["positions"].items()):
        current_price = get_price(symbol)
        if current_price is None:
            continue

        avg_price = pos["avg_price"]
        loss_pct = (current_price - avg_price) / avg_price

        # Aktualisiere letzten Preis
        pos["last_price"] = current_price

        if loss_pct <= -STOP_LOSS:
            trade = execute_sell(portfolio, symbol)
            if trade:
                triggered.append({
                    "symbol": symbol,
                    "avg_price": avg_price,
                    "sell_price": current_price,
                    "loss_pct": round(loss_pct * 100, 2),
                })
    return triggered


def scan_opportunities(portfolio: dict) -> dict:
    """Scannt Watchlist + dynamische ETF-Discovery nach Kaufgelegenheiten.

    Strategie (Norwegen-Modell):
    - BONDS (30%): Staats- + Unternehmensanleihen-ETFs
    - CORE (70% der Aktien): Low-Beta-ETFs (Beta 0.4–0.9), Minimum-Volatility + Dividenden-Qualität
      Scoring belohnt NIEDRIGES Beta. Dividenden-ETFs: belohnt 2.5-4.5% Rendite.
    - SATELLITE (30% der Aktien): Breite Markt-ETFs (Beta 0.9–1.2)
      Scoring belohnt Beta nahe 1.0.

    Super-Dividend-Schutz: Ausschüttungsrendite >6% → überspringen.

    Kombiniert statische Watchlist mit dynamischer ETF-Discovery via TradingView-API.

    Returns: {"core": [...], "satellite": [...], "bonds": [...]} — sortierte Kandidaten."""
    core_candidates = []
    satellite_candidates = []
    bond_candidates = []
    seen_symbols = set()  # Dedup statisch vs. dynamisch

    # 1. Dynamische ETF-Discovery (TradingView Beta-Range-Scan)
    discovered = discover_etfs()

    # 2. TradingView-Beta-Daten für alle Ticker abrufen
    static_raw = [t.replace(".DE", "") for t in CORE_TICKERS + SATELLITE_TICKERS + BOND_TICKERS + DIVIDEND_TICKERS]
    discovered_names = []
    for tier in ("core", "satellite"):
        for etf in discovered.get(tier, []):
            name = etf["name"]
            discovered_names.append(name)
            if name not in seen_symbols:
                seen_symbols.add(name)
                info = {
                    "symbol": name,
                    "price": None,
                    "beta": etf["beta_1y"],
                    "pe": None,
                    "score": 0.0,
                    "tier": tier,
                    "name": etf["description"],
                    "beta_source": "tv-discovery",
                    "vol_w": etf.get("vol_w"),
                    "vol_m": etf.get("vol_m"),
                    "dividend_yield": None,
                }

                if tier == "core":
                    beta = etf["beta_1y"]
                    score = 100 - ((beta - CORE_BETA_MIN) / (CORE_BETA_MAX - CORE_BETA_MIN)) * 50
                    info["score"] = round(score, 1)
                    core_candidates.append(info)
                else:
                    beta = etf["beta_1y"]
                    score = 100 - abs(beta - 1.0) * 50
                    info["score"] = round(score, 1)
                    satellite_candidates.append(info)

    # 3. Statische Watchlist-Ticker (+ Beta von TV holen)
    all_tv_names = list(set(static_raw + discovered_names))
    tv_beta_cache = fetch_tv_beta(tickers=all_tv_names)

    # --- Core-Ticker ---
    for symbol in CORE_TICKERS:
        tv_sym = symbol.replace(".DE", "")
        if tv_sym in seen_symbols:
            continue
        seen_symbols.add(tv_sym)

        price = get_price(symbol)
        info = get_info(symbol, tv_beta_cache=tv_beta_cache)
        beta = info.get("beta")
        if not price or beta is None:
            continue
        pe = info.get("trailingPE")

        if CORE_BETA_MIN <= beta <= CORE_BETA_MAX and (pe is None or pe < 30):
            score = 100 - ((beta - CORE_BETA_MIN) / (CORE_BETA_MAX - CORE_BETA_MIN)) * 50
            core_candidates.append({
                "symbol": symbol, "price": price, "beta": beta, "pe": pe,
                "score": round(score, 1), "tier": "core",
                "name": info.get("name", symbol),
                "beta_source": "tv" if tv_beta_cache else "yfinance",
                "dividend_yield": None,
            })

    # --- Satellite-Ticker ---
    for symbol in SATELLITE_TICKERS:
        tv_sym = symbol.replace(".DE", "")
        if tv_sym in seen_symbols:
            continue
        seen_symbols.add(tv_sym)

        price = get_price(symbol)
        info = get_info(symbol, tv_beta_cache=tv_beta_cache)
        beta = info.get("beta")
        if not price or beta is None:
            continue
        pe = info.get("trailingPE")

        if SATELLITE_BETA_MIN <= beta <= 1.20 and (pe is None or pe < 30):
            score = 100 - abs(beta - 1.0) * 50
            satellite_candidates.append({
                "symbol": symbol, "price": price, "beta": beta, "pe": pe,
                "score": round(score, 1), "tier": "satellite",
                "name": info.get("name", symbol),
                "beta_source": "tv" if tv_beta_cache else "yfinance",
                "dividend_yield": None,
            })

    # --- Dividenden-Qualitäts-Ticker (Core, Scoring nach Ausschüttungsrendite) ---
    for symbol in DIVIDEND_TICKERS:
        tv_sym = symbol.replace(".DE", "")
        if tv_sym in seen_symbols:
            continue
        seen_symbols.add(tv_sym)

        price = get_price(symbol)
        if not price:
            continue

        # Super-Dividend-Schutz: yfinance dividendYield prüfen
        div_yield = None
        try:
            ticker = yf.Ticker(symbol)
            div_yield = ticker.info.get("dividendYield")
        except Exception:
            pass

        if div_yield is not None and div_yield > 0.06:
            logger.info("Super-Dividend-Falle ausgeschlossen: %s (Yield: %.1f%%)", symbol, div_yield * 100)
            continue

        # Dividenden-Scoring: sweet spot 2.5–4.5%, bestrafen >6%
        if div_yield and 0.025 <= div_yield <= 0.045:
            div_score = 90.0
        elif div_yield:
            div_score = max(30, 100 - abs(div_yield - 0.035) * 1000)
        else:
            div_score = 60.0  # Unknown yield, neutral

        info = get_info(symbol, tv_beta_cache=tv_beta_cache)
        beta = info.get("beta")
        core_candidates.append({
            "symbol": symbol, "price": price,
            "beta": beta if beta else 0.7,  # Dividenden-ETFs ≈ Low-Beta
            "pe": info.get("trailingPE"),
            "score": round(div_score, 1),
            "tier": "core",
            "name": info.get("name", symbol),
            "beta_source": "tv" if tv_beta_cache else "yfinance",
            "dividend_yield": div_yield,
        })

    # --- Anleihen-Ticker (Bonds) ---
    for symbol in BOND_TICKERS:
        tv_sym = symbol.replace(".DE", "")
        if tv_sym in seen_symbols:
            continue
        seen_symbols.add(tv_sym)

        price = get_price(symbol)
        if not price:
            continue

        bond_candidates.append({
            "symbol": symbol,
            "price": price,
            "beta": 0.0,  # Bonds ≈ beta 0
            "pe": None,
            "score": 80.0,  # Neutraler Score für Anleihen
            "tier": "bonds",
            "name": symbol,
            "beta_source": "static",
            "dividend_yield": None,
        })

    # 4. Preise für Discovery-Ticker nachladen (yfinance)
    unpriced = 0
    for cand in core_candidates + satellite_candidates:
        if cand["price"] is None:
            try:
                price = get_price(cand["symbol"])
            except Exception:
                price = None
            if price is not None:
                cand["price"] = price
            else:
                unpriced += 1

    core_candidates = [c for c in core_candidates if c["price"] is not None]
    satellite_candidates = [c for c in satellite_candidates if c["price"] is not None]

    if unpriced:
        logger.info("%d Discovery-Ticker ohne yfinance-Preis ausgefiltert", unpriced)

    core_candidates.sort(key=lambda x: x["score"], reverse=True)
    satellite_candidates.sort(key=lambda x: x["score"], reverse=True)
    bond_candidates.sort(key=lambda x: x["score"], reverse=True)

    logger.info("Kandidaten: %d Core / %d Satellite / %d Bonds (davon %d via Discovery)",
                len(core_candidates), len(satellite_candidates), len(bond_candidates),
                len(discovered.get("core", [])) + len(discovered.get("satellite", [])))

    return {"core": core_candidates[:10], "satellite": satellite_candidates[:5], "bonds": bond_candidates[:3]}


def run_strategy(portfolio: dict) -> list:
    """Fuehrt die taegliche Handelsstrategie aus mit Bonds/Core/Satellite-Allokation.

    Allokation (Norwegen-Modell):
    - 30% BONDS: Staats- + Unternehmensanleihen-ETFs
    - 70% AKTIEN:
      - CORE: Low-Beta-ETFs (Beta 0.4–0.9), Minimum-Volatility + Dividenden-Qualität
      - SATELLITE: Breite Markt-ETFs (Beta 0.9–1.2)

    Nur Di–Do wird gekauft. Mo/Fr: Analyse & Rebalancing-Check.
    """
    today = date.today().weekday()
    actions = []

    # 1. Stop-Loss pruefen (jeden Tag)
    stop_losses = check_stop_loss(portfolio)
    if stop_losses:
        actions.append(f"STOP-LOSS: {len(stop_losses)} Position(en) verkauft.")
    else:
        actions.append("Keine Stop-Loss-Ausloesung.")

    # 2. Aktuellen Allokations-Status berechnen (Bonds/Core/Satellite)
    core_value = 0.0
    sat_value = 0.0
    bond_value = 0.0
    for sym, pos in portfolio["positions"].items():
        val = pos["shares"] * pos.get("last_price", pos["avg_price"])
        tier = pos.get("tier", "core")
        if tier == "bonds":
            bond_value += val
        elif tier == "core":
            core_value += val
        else:
            sat_value += val

    total_invested = core_value + sat_value + bond_value
    total_portfolio = portfolio["cash"] + total_invested
    core_pct = (core_value / total_portfolio * 100) if total_portfolio > 0 else 0
    sat_pct = (sat_value / total_portfolio * 100) if total_portfolio > 0 else 0
    bond_pct = (bond_value / total_portfolio * 100) if total_portfolio > 0 else 0
    equity_pct = core_pct + sat_pct

    actions.append(f"Allokation: BONDS {bond_pct:.0f}% / CORE {core_pct:.0f}% / SATELLITE {sat_pct:.0f}% (Ziel: {BOND_ALLOCATION*100:.0f}/{CORE_ALLOCATION*EQUITY_ALLOCATION*100:.0f}/{SATELLITE_ALLOCATION*EQUITY_ALLOCATION*100:.0f})")

    # 3. US-Exposure schätzen
    us_pct, us_value, total_equity_val, us_warning = estimate_us_exposure(portfolio)
    if us_warning:
        actions.append(us_warning)
    if total_equity_val > 0:
        actions.append(f"US-Anteil: {us_pct:.0f}% (Ziel: ≤55%, Wert: €{us_value:,.0f})")

    # 4. Nur Dienstag bis Donnerstag: Neue Käufe (Bonds/Core/Satellite-gesteuert)
    if 1 <= today <= 3:
        reset_daily_budget(portfolio)
        available = min(DAILY_LIMIT - portfolio.get("today_spent", 0), portfolio["cash"])

        if available >= 500:
            candidates = scan_opportunities(portfolio)
            core_cands = candidates.get("core", [])
            sat_cands = candidates.get("satellite", [])
            bond_cands = candidates.get("bonds", [])

            # Priorität 1: Bonds auffüllen wenn unter Ziel (Norwegen-Modell: 30%)
            if bond_pct < BOND_ALLOCATION * 100 - 5 and bond_cands and available >= 500:
                best = bond_cands[0]
                buy_amount = min(1000.0, available)
                trade = execute_buy(portfolio, best["symbol"], buy_amount, tier="bonds")
                if trade:
                    actions.append(
                        f"BOND-KAUF: {best['symbol']} | €{buy_amount:.2f} (Score: {best['score']})"
                    )
                    available -= buy_amount

            # Priorität 2: Core auffüllen, wenn unter Ziel-Allokation
            target_core_pct = CORE_ALLOCATION * EQUITY_ALLOCATION * 100
            if core_pct < target_core_pct - 5 and core_cands and available >= 500:
                best = core_cands[0]
                buy_amount = min(1200.0, available)
                trade = execute_buy(portfolio, best["symbol"], buy_amount, tier="core")
                if trade:
                    div_info = ""
                    if best.get("dividend_yield"):
                        div_info = f", Div: {best['dividend_yield']*100:.1f}%"
                    actions.append(
                        f"CORE-KAUF: {best['symbol']} | €{buy_amount:.2f} "
                        f"(Beta: {best['beta']:.2f}, Score: {best['score']}{div_info})"
                    )
                    available -= buy_amount

            # Priorität 3: Satellite auffüllen, wenn noch Budget
            target_sat_pct = SATELLITE_ALLOCATION * EQUITY_ALLOCATION * 100
            if sat_pct < target_sat_pct - 5 and sat_cands and available >= 500:
                best = sat_cands[0]
                buy_amount = min(800.0, available)
                trade = execute_buy(portfolio, best["symbol"], buy_amount, tier="satellite")
                if trade:
                    actions.append(
                        f"SATELLITE-KAUF: {best['symbol']} | €{buy_amount:.2f} "
                        f"(Beta: {best['beta']:.2f}, Score: {best['score']})"
                    )
                    available -= buy_amount

            if not core_cands and not sat_cands and not bond_cands:
                actions.append("Keine geeigneten Kaufkandidaten (Bonds/Core/Satellite).")
        else:
            actions.append(f"Budget zu gering (€{available:.2f}). Keine Käufe.")

    elif today == 0:
        # Montag: Rebalancing-Analyse (Norwegen-Modell: Bonds 25-35%, Core Equity, US ≤55%)
        drift_actions = []
        if core_pct > 80:
            drift_actions.append(f"Core übergewichtet ({core_pct:.0f}%) — Rebalancing prüfen")
        if sat_pct > 40:
            drift_actions.append(f"Satellite übergewichtet ({sat_pct:.0f}%) — Rebalancing prüfen")
        if bond_pct < 25:
            drift_actions.append(f"Bonds untergewichtet ({bond_pct:.0f}%) — Nachkauf empfohlen")
        if bond_pct > 35:
            drift_actions.append(f"Bonds übergewichtet ({bond_pct:.0f}%) — Rebalancing prüfen")
        if us_pct > 65:
            drift_actions.append(f"US-Exposure zu hoch ({us_pct:.0f}%) — nicht-US Core beimischen")
        if drift_actions:
            actions.extend(drift_actions)
        else:
            actions.append("Montag-Analyse: Allokation im Soll-Bereich (Norwegen-Modell).")

    elif today == 4:
        # Freitag: Wochen-Review
        bond_target = BOND_ALLOCATION * total_portfolio
        core_target = CORE_ALLOCATION * EQUITY_ALLOCATION * total_portfolio
        sat_target = SATELLITE_ALLOCATION * EQUITY_ALLOCATION * total_portfolio
        bond_drift = bond_value - bond_target
        core_drift = core_value - core_target
        sat_drift = sat_value - sat_target
        actions.append(
            f"Freitag-Review: Bond-Drift €{bond_drift:+.0f}, "
            f"Core-Drift €{core_drift:+.0f}, Sat-Drift €{sat_drift:+.0f}"
        )

    # 5. Klumpenrisiko (>22% in EINER Position = Warnung)
    if len(portfolio["positions"]) >= 3:
        for sym, pos in portfolio["positions"].items():
            value = pos["shares"] * pos.get("last_price", pos["avg_price"])
            if total_portfolio > 0 and (value / total_portfolio) > 0.22:
                actions.append(
                    f"WARNUNG: {sym} ({pos.get('tier', 'core')}) macht "
                    f">{value/total_portfolio*100:.0f}% des Portfolios aus."
                )

    return actions


def generate_report(portfolio: dict) -> tuple:
    """Generiert Markdown-Report. Gibt (plain_text, html_body) zurueck."""
    now = datetime.now()
    pnl_realized = sum(
        (t["net"] if "net" in t else -(t["amount"])) if t["side"] == "sell" else 0.0
        for t in portfolio["history"]
    )

    # US-Exposure
    us_pct, us_value, total_equity_val, _ = estimate_us_exposure(portfolio)

    # Aktuelle Portfoliobewertung
    positions_val = 0.0
    bond_positions = []     # Anleihen separat
    equity_positions = []   # Aktien (Core+Satellite) separat
    for symbol, pos in portfolio["positions"].items():
        current_p = get_price(symbol)
        if current_p is None:
            current_p = pos.get("last_price", pos["avg_price"])
        value = pos["shares"] * current_p
        pnl = (current_p - pos["avg_price"]) * pos["shares"]
        pnl_pct = ((current_p - pos["avg_price"]) / pos["avg_price"]) * 100 if pos["avg_price"] > 0 else 0
        positions_val += value
        tier = pos.get("tier", "core")

        # Versuche Dividendenrendite von yfinance
        div_yield = None
        if tier in ("core", "satellite"):
            try:
                div_yield = yf.Ticker(symbol).info.get("dividendYield")
            except Exception:
                pass

        if tier == "bonds":
            tier_label = "🏦"
            bond_positions.append(
                f"| {symbol} | {tier_label} BONDS | {pos['shares']:.4f} | €{pos['avg_price']:.2f} | €{current_p:.2f} | €{value:.2f} | {pnl_pct:+.2f}% |"
            )
        elif tier == "core":
            tier_label = "🛡️"
            div_suffix = f" (Div: {div_yield*100:.1f}%)" if div_yield else ""
            equity_positions.append(
                f"| {symbol} | {tier_label} CORE | {pos['shares']:.4f} | €{pos['avg_price']:.2f} | €{current_p:.2f} | €{value:.2f} | {pnl_pct:+.2f}%{div_suffix} |"
            )
        else:
            tier_label = "🚀"
            div_suffix = f" (Div: {div_yield*100:.1f}%)" if div_yield else ""
            equity_positions.append(
                f"| {symbol} | {tier_label} SAT | {pos['shares']:.4f} | €{pos['avg_price']:.2f} | €{current_p:.2f} | €{value:.2f} | {pnl_pct:+.2f}%{div_suffix} |"
            )

    total_value = portfolio["cash"] + positions_val
    total_return_pct = ((total_value - START_CAPITAL) / START_CAPITAL) * 100

    # Letzte 5 Trades
    recent_trades = portfolio["history"][-5:] if portfolio["history"] else []
    trades_table = ""
    for t in recent_trades:
        ts = t["timestamp"] if isinstance(t["timestamp"], str) else str(t["timestamp"])
        trades_table += f"| {ts[:10]} | {t['symbol']} | {t['side']} | {t['shares']:.4f} | €{t.get('price', 0):.2f} |\n"

    plain = f"""
============================================
ULTIMATE TRADER - TAGESBERICHT (Norwegen-Modell)
{now.strftime('%A, %d.%m.%Y %H:%M')}
============================================

KAPITALUEBERSICHT:
  Startkapital:     €{START_CAPITAL:,.2f}
  Cash-Bestand:     €{portfolio['cash']:,.2f}
  Positionen-Wert:  €{positions_val:,.2f}
  Gesamtwert:       €{total_value:,.2f}
  Gewinn/Verlust:   €{total_value - START_CAPITAL:,.2f} ({total_return_pct:+.2f}%)
  Trades bisher:    {portfolio.get('trade_count', 0)}

US-EXPOSURE (Aktien):
  US-Anteil: {us_pct:.0f}% (Ziel: ≤55%, Wert: €{us_value:,.0f})
  Gesamt-Aktienwert: €{total_equity_val:,.0f}

ANLEIHEN:
"""
    if bond_positions:
        for line in bond_positions:
            plain += "  " + line + "\n"
    else:
        plain += "  (Keine Anleihen-Positionen)\n"

    plain += """
AKTIEN:
  Symbol   Stueck     Einstand   Aktuell    Wert       P/L %     
  ------------------------------------------------------------
"""
    if equity_positions:
        for line in equity_positions:
            plain += "  " + line + "\n"
    else:
        plain += "  (Keine Aktien-Positionen)\n"

    plain += f"""
LETZTE TRADES:
  Datum      | Symbol   | Seite | Stueck    | Preis
  {'-'*50}
"""
    if recent_trades:
        plain += trades_table
    else:
        plain += "  (Noch keine Trades)\n"

    # HTML-Version fuer E-Mail
    html = f"""
<h1>🤖 Ultimate Trader - Tagesbericht (Norwegen-Modell)</h1>
<p><strong>{now.strftime('%A, %d.%m.%Y %H:%M')}</strong></p>

<h2>📊 Kapitaluebersicht</h2>
<ul>
  <li><b>Startkapital:</b> €{START_CAPITAL:,.2f}</li>
  <li><b>Cash:</b> €{portfolio['cash']:,.2f}</li>
  <li><b>Positionswert:</b> €{positions_val:,.2f}</li>
  <li><b>Gesamtwert:</b> €{total_value:,.2f}</li>
  <li><b>Performance:</b> <span style='color:{"green" if total_return_pct >= 0 else "red"};'>{total_return_pct:+.2f}%</span></li>
  <li><b>Trades:</b> {portfolio.get('trade_count', 0)}</li>
  <li><b>US-Anteil:</b> {us_pct:.0f}% (Ziel: ≤55%)</li>
</ul>

<h2>🏦 Anleihen</h2>
<table border='1' cellpadding='5'>
  <tr><th>Symbol</th><th>Tier</th><th>Stueck</th><th>Einstand</th><th>Aktuell</th><th>Wert</th><th>P/L %</th></tr>
"""
    for symbol, pos in portfolio["positions"].items():
        tier = pos.get("tier", "core")
        if tier != "bonds":
            continue
        current_p = get_price(symbol) or pos.get("last_price", pos["avg_price"])
        value = pos["shares"] * current_p
        pnl_pct = ((current_p - pos["avg_price"]) / pos["avg_price"]) * 100 if pos["avg_price"] > 0 else 0
        color = "green" if pnl_pct >= 0 else "red"
        html += f"  <tr><td>{symbol}</td><td>🏦 BONDS</td><td>{pos['shares']:.4f}</td><td>€{pos['avg_price']:.2f}</td><td>€{current_p:.2f}</td><td>€{value:.2f}</td><td style='color:{color}'>{pnl_pct:+.2f}%</td></tr>\n"
    html += "</table><p></p>"

    html += """
<h2>📈 Aktien</h2>
<table border='1' cellpadding='5'>
  <tr><th>Symbol</th><th>Tier</th><th>Stueck</th><th>Einstand</th><th>Aktuell</th><th>Wert</th><th>P/L %</th><th>Div</th></tr>
"""
    for symbol, pos in portfolio["positions"].items():
        tier = pos.get("tier", "core")
        if tier == "bonds":
            continue
        current_p = get_price(symbol) or pos.get("last_price", pos["avg_price"])
        value = pos["shares"] * current_p
        pnl_pct = ((current_p - pos["avg_price"]) / pos["avg_price"]) * 100 if pos["avg_price"] > 0 else 0
        color = "green" if pnl_pct >= 0 else "red"
        tier_label = "🛡️ CORE" if tier == "core" else "🚀 SAT"
        div_yield = None
        try:
            div_yield = yf.Ticker(symbol).info.get("dividendYield")
        except Exception:
            pass
        div_str = f"{div_yield*100:.1f}%" if div_yield else "—"
        html += f"  <tr><td>{symbol}</td><td>{tier_label}</td><td>{pos['shares']:.4f}</td><td>€{pos['avg_price']:.2f}</td><td>€{current_p:.2f}</td><td>€{value:.2f}</td><td style='color:{color}'>{pnl_pct:+.2f}%</td><td>{div_str}</td></tr>\n"
    html += "</table><p></p>"

    return plain, html


def send_email(subject: str, body_plain: str, body_html: str = "") -> bool:
    """Sendet E-Mail via SMTP (Gmail App-Password empfohlen)."""
    smtp_host = os.getenv("EMAIL_SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("EMAIL_SMTP_PORT", "587"))
    smtp_user = os.getenv("EMAIL_ADDRESS", "")
    smtp_pass = os.getenv("EMAIL_PASSWORD", "")
    sender = os.getenv("GMAIL_SENDER", smtp_user)
    recipient = os.getenv("GMAIL_RECIPIENT", "frank.b.reis@gmail.com")

    if not all([smtp_user, smtp_pass, recipient]):
        logger.warning("SMTP-Credentials fehlen. E-Mail nicht versendet.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Ultimate Trader - {subject}"
        msg["From"] = sender
        msg["To"] = recipient
        msg.attach(MIMEText(body_plain, "plain"))
        if body_html:
            msg.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        logger.info("E-Mail gesendet an %s", recipient)
        return True
    except Exception as exc:
        logger.error("E-Mail-Versand fehlgeschlagen: %s", exc)
        return False


def save_report_to_file(report_plain: str) -> Path:
    """Speichert Report als lokale Datei. Gibt Pfad zurueck."""
    filename = f"report_{datetime.now().strftime('%Y-%m-%d')}.md"
    filepath = REPORTS_DIR / filename
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(report_plain)
    logger.info("Report gespeichert: %s", filepath)
    return filepath


def main():
    logger.info("=== AUTONOMER ULTIMATE TRADER GESTARTET ===")

    # 1. Portfolio laden
    portfolio = load_portfolio()
    portfolio["last_run"] = datetime.now().isoformat()

    # 2. Strategie ausfuehren
    actions = run_strategy(portfolio)

    # 3. Report generieren
    plain, html = generate_report(portfolio)
    report_path = save_report_to_file(plain)

    # 4. E-Mail versuchen
    weekday = date.today().weekday()
    subject = f"Tagesbericht ({date.today().strftime('%d.%m.%Y')})"
    if weekday == 4:
        subject = f"WOCHENBERICHT ({date.today().strftime('%d.%m.%Y')})"

    sent = send_email(subject, plain, html)

    # 5. Zusammenfassung loggen
    logger.info("=== RUN BEENDET ===")
    logger.info("Aktionen: %s", " | ".join(actions))
    logger.info("Report: %s", report_path)
    logger.info("E-Mail: %s", "Gesendet" if sent else "Nicht gesendet")

    # Optional: Bei Fehler Exit-Code > 0 fuer Monitoring
    if not sent:
        logger.info("Hinweis: E-Mail nicht versendet (lokaler Report aber verfuegbar).")


if __name__ == "__main__":
    main()
