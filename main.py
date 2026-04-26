"""
Ultimate Trader - Agno Agent System for Simulated Stock/ETF Trading

This system simulates trading with real market data from Yahoo Finance,
with risk management, stop-loss, daily spending limits, and automated reporting.
"""

import os
import json
import logging
import schedule
import time
import base64
from datetime import datetime, date
from typing import Optional

from dotenv import load_dotenv
from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.yfinance import YFinanceTools

# Load environment variables early
load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("ultimate_trader")


# ============================================================================
# TRADING ENGINE - Core Logic and Calculations
# ============================================================================

class TradingEngine:
    """Core trading logic including calculations, risk management, reporting, and portfolio persistence."""

    def __init__(self):
        # Load configuration from environment or use defaults
        self.start_capital = float(os.getenv("START_CAPITAL", "10000.0"))
        self.stop_loss_threshold = float(os.getenv("STOP_LOSS_THRESHOLD", "0.05"))
        self.daily_spending_limit = float(os.getenv("DAILY_SPENDING_LIMIT", "2000.0"))
        self.fee_rate = float(os.getenv("FEE_RATE", "0.001"))

        self.today_spent = 0.0
        self.last_trade_date = None

        # Portfolio persistence state
        self.state_file = os.path.join(os.path.dirname(__file__), "portfolio_state.json")
        self.portfolio = self._load_portfolio()

        # Email configuration loaded from environment
        self.email_config = {
            "smtp_server": os.getenv("SMTP_SERVER", "smtp.gmail.com"),
            "port": int(os.getenv("SMTP_PORT", "587")),
            "user": os.getenv("EMAIL_FROM", ""),
            "pass": os.getenv("SMTP_PASS", ""),
            "from": os.getenv("EMAIL_FROM", ""),
            "to": os.getenv("EMAIL_TO", ""),
        }

    def _load_portfolio(self) -> dict:
        """Load persisted portfolio state from JSON file."""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                logger.info("Portfolio state loaded from %s", self.state_file)
                return data
            except (json.JSONDecodeError, IOError) as exc:
                logger.warning("Failed to load portfolio state: %s. Starting fresh.", exc)
        return {
            "cash": self.start_capital,
            "positions": {},  # symbol -> {"shares": float, "avg_price": float}
            "history": [],    # list of trade records
        }

    def _save_portfolio(self) -> None:
        """Persist current portfolio state to JSON file atomically."""
        temp_file = self.state_file + ".tmp"
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(self.portfolio, f, indent=2, default=str)
            os.replace(temp_file, self.state_file)
            logger.info("Portfolio state saved (%d positions, %.2f € cash)",
                        len(self.portfolio["positions"]), self.portfolio["cash"])
        except IOError as exc:
            logger.error("Failed to save portfolio state: %s", exc)

    def record_trade(self, symbol: str, side: str, shares: float, price: float, fees: float) -> None:
        """Record a completed trade and update portfolio state."""
        symbol = symbol.upper()
        trade = {
            "timestamp": datetime.now().isoformat(),
            "symbol": symbol,
            "side": side,
            "shares": round(shares, 6),
            "price": round(price, 4),
            "fees": round(fees, 2),
        }
        self.portfolio["history"].append(trade)

        pos = self.portfolio["positions"].get(symbol, {"shares": 0.0, "avg_price": 0.0})
        current_shares = pos["shares"]
        current_avg = pos["avg_price"]

        if side == "buy":
            total_cost = current_shares * current_avg + shares * price
            new_shares = current_shares + shares
            new_avg = total_cost / new_shares if new_shares > 0 else 0.0
            self.portfolio["positions"][symbol] = {"shares": new_shares, "avg_price": round(new_avg, 4)}
            self.portfolio["cash"] -= (shares * price + fees)
        elif side == "sell":
            if shares > current_shares:
                # Sell all available if oversold
                shares = current_shares
            new_shares = current_shares - shares
            if new_shares <= 0:
                self.portfolio["positions"].pop(symbol, None)
            else:
                self.portfolio["positions"][symbol] = {"shares": new_shares, "avg_price": current_avg}
            proceeds = shares * price - fees
            self.portfolio["cash"] += proceeds

        self._save_portfolio()

    def check_budget(self, amount_euro: float) -> dict:
        """
        Prüft, ob das Tagesbudget noch ausreicht.
        Gibt ein Dict mit 'allowed' (bool) und 'remaining' (float) zurück.
        """
        current_date = date.today()

        # Reset des Budgets bei neuem Tag
        if self.last_trade_date != current_date:
            self.today_spent = 0.0
            self.last_trade_date = current_date

        if (self.today_spent + amount_euro) <= self.daily_spending_limit:
            self.today_spent += amount_euro
            return {"allowed": True, "remaining": self.daily_spending_limit - self.today_spent}
        return {"allowed": False, "remaining": 0.0, "excess": (self.today_spent + amount_euro) - self.daily_spending_limit}

    def calculate_trade(self, amount_euro: float, current_price: float, side: str) -> dict:
        """
        Berechnet exakte Stückzahlen und 0,1% Gebühren für einen Trade.
        side: 'buy' oder 'sell'
        """
        if side == "buy":
            # Kauf: Preis inkl. Gebührenaufschlag
            effective_price = current_price * (1 + self.fee_rate)
            shares = amount_euro / effective_price
            fees = amount_euro - (shares * current_price)
            return {
                "shares": round(shares, 4),
                "fees_euro": round(fees, 2),
                "price_per_share": round(current_price, 2),
                "total_invested": round(shares * current_price, 2)
            }
        else:
            # Verkauf: shares * price minus Gebühren
            shares = amount_euro / current_price
            gross_proceeds = shares * current_price
            fees = gross_proceeds * self.fee_rate
            net_proceed = gross_proceeds - fees
            return {
                "shares": round(shares, 4),
                "net_proceed_euro": round(net_proceed, 2),
                "fees_euro": round(fees, 2),
                "gross_proceeds": round(gross_proceeds, 2)
            }

    def check_volatility(self, beta: Optional[float]) -> str:
        """
        Bewertet das Risiko basierend auf dem Beta-Faktor.
        """
        if beta is None:
            return "Keine Daten verfügbar. Vorsicht geboten."
        if beta > 1.5:
            return f"HOCHRISIKO (Beta {beta}): Starke Schwankungen erwartet."
        if beta > 1.0:
            return f"MODERAT (Beta {beta}): Etwas volatiler als der Markt."
        if beta > 0.8:
            return f"LEICHT DEFENSIV (Beta {beta}): Geringes Risiko."
        return f"DEFENSIV (Beta {beta}): Sehr geringes Risiko."

    def monitor_stop_loss(self, symbol: str, purchase_price: float, current_price: float, shares: float) -> dict:
        """
        Prüft, ob der Stop-Loss von 5% ausgelöst wurde.
        Gibt ein Dict mit Status und Empfehlung zurück.
        """
        if purchase_price == 0:
            return {"status": "error", "message": "Ungültiger Kaufkurs"}

        loss_pct = (current_price - purchase_price) / purchase_price
        current_value = current_price * shares
        purchase_value = purchase_price * shares
        profit_loss = current_value - purchase_value
        profit_loss_pct = (profit_loss / purchase_value) * 100 if purchase_value > 0 else 0

        if loss_pct <= -self.stop_loss_threshold:
            return {
                "status": "STOP_LOSS_TRIGGERED",
                "symbol": symbol,
                "purchase_price": round(purchase_price, 2),
                "current_price": round(current_price, 2),
                "loss_pct": round(loss_pct * 100, 2),
                "current_value": round(current_value, 2),
                "recommendation": "SELL",
                "message": f"ALARM: {symbol} ist um {round(loss_pct*100, 2)}% gefallen. STOP-LOSS AUSGELÖST!"
            }
        return {
            "status": "HOLD",
            "symbol": symbol,
            "purchase_price": round(purchase_price, 2),
            "current_price": round(current_price, 2),
            "pnl_pct": round(loss_pct * 100, 2),
            "current_value": round(current_value, 2),
            "recommendation": "HOLD",
            "message": f"{symbol} Performance: {round(loss_pct*100, 2)}%. Halten."
        }

    def export_to_excel(self, portfolio_data: list) -> str:
        """
        Erstellt eine Excel-Datei aus den Portfoliodaten.
        """
        try:
            import pandas as pd
            filename = f"Portfolio_Status_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.xlsx"
            df = pd.DataFrame(portfolio_data)
            df.to_excel(filename, index=False)
            logger.info("Excel export: %s", filename)
            return f"Excel-Datei '{filename}' wurde erfolgreich erstellt."
        except ImportError:
            logger.error("Excel export failed: pandas not installed")
            return "Fehler: pandas nicht installiert. Bitte 'pip install pandas openpyxl' ausführen."

    def send_email_report(self, subject: str, body: str, html_body: str = "") -> str:
        """
        Sendet einen Statusbericht per E-Mail über Gmail API.
        Nutzt die Dienst-Account oder OAuth2-Zugangsdaten des Bots.
        """
        try:
            import json
            from googleapiclient.discovery import build
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart

            TOKEN_PATH = '/home/hermes/.hermes/google_token.json'
            with open(TOKEN_PATH, 'r') as fh:
                token_data = json.load(fh)

            creds = Credentials(
                token=token_data['token'],
                refresh_token=token_data.get('refresh_token'),
                token_uri=token_data.get('token_uri', 'https://oauth2.googleapis.com/token'),
                client_id=token_data['client_id'],
                client_secret=token_data['client_secret'],
                scopes=token_data['scopes']
            )

            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                token_data['token'] = creds.token
                with open(TOKEN_PATH, 'w') as fh:
                    json.dump(token_data, fh)

            service = build('gmail', 'v1', credentials=creds)

            sender = os.getenv("GMAIL_SENDER", "emil.mazdarati@gmail.com")
            recipient = os.getenv("GMAIL_RECIPIENT", "frank.b.reis@gmail.com")

            msg = MIMEMultipart('alternative')
            msg['Subject'] = f"Trading Bot Update: {subject}"
            msg['From'] = sender
            msg['To'] = recipient
            msg.attach(MIMEText(body, 'plain'))
            if html_body:
                msg.attach(MIMEText(html_body, 'html'))

            raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
            service.users().messages().send(userId='me', body={'raw': raw}).execute()

            logger.info("Gmail API: E-Mail '%s' gesendet an %s", subject, recipient)
            return f"Gmail-API: E-Mail '{subject}' erfolgreich gesendet an {recipient}."
        except Exception as exc:
            logger.error("Gmail API send failed: %s", exc)
            return f"Gmail-API E-Mail-Versand fehlgeschlagen: {exc}"


# ============================================================================
# TRADING STRATEGY 2026 - Market Context and Guidelines
# ============================================================================

STRATEGY_2026 = [
    "MARKT-KONTEXT 2026:",
    "- Bevorzuge 'Quality Growth' Aktien mit starken Cashflows, da die Zinsen auf einem stabilen, aber höheren Niveau verharren.",
    "- Gewichtung: 60% Welt-ETF (VWCE/EUNL), 20% Anleihen-ETFs (für Risiko-Minimierung), 20% selektive Blue-Chips.",
    "",
    "RISIKO-MANAGEMENT (ERWEITERT):",
    "- Sektor-Limit: Investiere niemals mehr als 25% des Gesamtkapitals in einen einzelnen Sektor (z.B. Tech oder Energie).",
    "- Beta-Filter: Der gewichtete Durchschnitt des Portfolio-Beta muss zwischen 0.8 und 1.0 liegen.",
    "- Diversifikation: Nicht alles in eine Aktie setzen (Maximal 10% pro Einzelaktie).",
    "",
    "UMSCHICHTUNGS-LOGIK:",
    "- Prüfe bei jedem Audit: Wenn eine Position mehr als 15% des Portfolios ausmacht (Klumpenrisiko), schlage eine Teil-Gewinnmitnahme vor.",
    "- Nutze bei hoher Marktvolatilität (VIX > 25) verstärkt defensive Werte wie Konsumgüter (KO, PEP) oder Gesundheitswesen.",
    "",
    "HANDLUNGSABLAUF:",
    "- Prüfe IMMER das Beta (Stock Fundamentals) via 'check_volatility' vor jedem Kauf.",
    "- Berechne den Trade exakt mit 'calculate_trade' (0,1% Gebühr).",
    "- Prüfe Stop-Loss bei jeder Interaktion mit 'monitor_stop_loss'.",
    "- Prüfe Tagesbudget mit 'check_budget' vor jedem Kauf.",
    "- Gib bei jeder Antwort eine 'DEPOT-ZUSAMMENFASSUNG' mit: Gesamtwert, Cash-Bestand, Performance seit Start (in %)."
]


# ============================================================================
# CREATE TRADING AGENT
# ============================================================================

# Initialize the trading engine
engine = TradingEngine()

# Load LLM config from environment (OpenRouter by default, Ollama optional)
llm_provider = os.getenv("LLM_PROVIDER", "openrouter").lower()
if llm_provider == "ollama":
    llm_base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    llm_model = os.getenv("OLLAMA_MODEL", "qwen2.5:latest")
    llm_api_key = "test"
else:
    # OpenRouter (default)
    llm_base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    llm_model = os.getenv("OPENROUTER_MODEL", "anthropic/claude-3.5-sonnet")
    llm_api_key = os.getenv("OPENROUTER_API_KEY", "")
    if not llm_api_key:
        logger.warning("OPENROUTER_API_KEY nicht gesetzt! Agent kann nicht chatten.")

# Create the trading agent (agno 2.x compatible)
trading_agent = Agent(
    name="Ultimate-Trader",
    model=OpenAIChat(
        id=llm_model,
        base_url=llm_base_url,
        api_key=llm_api_key
    ),
    tools=[
        YFinanceTools(enable_stock_price=True, enable_stock_fundamentals=True, enable_analyst_recommendations=True),
        engine.check_budget,
        engine.calculate_trade,
        engine.check_volatility,
        engine.monitor_stop_loss,
        engine.export_to_excel,
        engine.send_email_report
    ],
    instructions=[
        f"Startkapital: {engine.start_capital} €.",
        f"Tageslimit: {engine.daily_spending_limit} €",
        f"Stop-Loss-Schwellwert: {int(engine.stop_loss_threshold * 100)}%",
        "",
    ] + STRATEGY_2026,
    add_history_to_context=True,
    num_history_runs=10,
    markdown=True,
)


# ============================================================================
# DAILY TRADING ROUTINE
# ============================================================================

def run_daily_routine():
    """Führt den automatischen Handel basierend auf dem Wochentag aus."""
    weekday = datetime.now().weekday()  # 0 = Montag, 4 = Freitag
    print(f"\n{'='*60}")
    print(f"--- Automatischer Start am {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
    print(f"--- Wochentag: {['Montag', 'Dienstag', 'Mittwoch', 'Donnerstag', 'Freitag', 'Samstag', 'Sonntag'][weekday]} ---")
    print(f"{'='*60}\n")
    
    if weekday == 0:  # MONTAG: ANALYSE & KAUF
        query = (
            "Analysiere die aktuelle Marktlage für März 2026. Gibt es unterbewertete ETFs "
            "mit einem Beta < 1.1? Falls ja, investiere jeweils 1000€ in die zwei besten Optionen. "
            "Erstelle danach den Excel-Export."
        )
    elif weekday == 4:  # FREITAG: AUDIT & REPORT
        query = (
            "Führe ein komplettes Audit durch. Prüfe alle Stop-Loss Limits. Berechne die "
            "Wochenperformance im Vergleich zum Startkapital von 10.000€. "
            "Erstelle den Excel-Export und sende den E-Mail-Bericht."
        )
    elif 1 <= weekday <= 3:  # DIENSTAG BIS DONNERSTAG: MONITORING
        query = "Führe einen schnellen Portfolio-Check durch und löse Stop-Loss aus, falls nötig."
    else:  # WOCHENENDE
        print("Börsen geschlossen. Kein Handel am Wochenende.")
        return
    
    # Agent führt den Befehl aus
    try:
        trading_agent.print_response(query, stream=True)
    except Exception as e:
        print(f"Fehler bei der Agenten-Ausführung: {e}")


# ============================================================================
# MAIN EXECUTION
# ============================================================================

if __name__ == "__main__":
    print("="*60)
    print("ULTIMATE TRADER - Agno Agent System")
    print("="*60)
    print("\nSystem initialisiert:")
    print(f"- Startkapital: {engine.start_capital} €")
    print(f"- Tageslimit: {engine.daily_spending_limit} €")
    print(f"- Stop-Loss: {int(engine.stop_loss_threshold * 100)}%")
    print(f"- Handelsgebühr: {int(engine.fee_rate * 100)}%")
    print(f"- Modell: qwen2.5:latest")
    print("\nWähle eine Option:")
    print("1. Einmalige Ausführung (jetzt)")
    print("2. Automatischer Scheduler (täglich um 09:05 Uhr)")
    print("3. Manuelle Abfrage")
    
    choice = input("\nAuswahl (1-3): ").strip()
    
    if choice == "1":
        # Einmalige Ausführung
        run_daily_routine()
    elif choice == "2":
        # Automatischer Scheduler
        print("\nScheduler gestartet. Das System führt jeden Tag um 09:05 Uhr Handel aus.")
        print("Drücke Ctrl+C zum Beenden.\n")
        schedule.every().day.at("09:05").do(run_daily_routine)
        
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)
        except KeyboardInterrupt:
            print("\nScheduler beendet.")
    elif choice == "3":
        # Manuelle Abfrage
        print("\n--- Manuelle Abfrage ---")
        print("Gib deine Anfrage ein (z.B. 'Wie sieht mein Depot aus?' oder 'Kaufe 2000€ MSCI World'):")
        user_query = input("> ")
        trading_agent.print_response(user_query, stream=True)
    else:
        print("Ungültige Auswahl. Programm beendet.")
