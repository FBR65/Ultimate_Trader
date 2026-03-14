"""
Ultimate Trader - Agno Agent System for Simulated Stock/ETF Trading

This system simulates trading with real market data from Yahoo Finance,
with risk management, stop-loss, daily spending limits, and automated reporting.
"""

import schedule
import time
import decimal
from datetime import datetime, date
from typing import Optional

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.yfinance import YFinanceTools


# ============================================================================
# TRADING ENGINE - Core Logic and Calculations
# ============================================================================

class TradingEngine:
    """Core trading logic including calculations, risk management, and reporting."""
    
    def __init__(self):
        self.start_capital = 10000.0
        self.stop_loss_threshold = 0.05  # 5% stop loss
        self.daily_spending_limit = 2000.0  # Max spending per day
        self.today_spent = 0.0
        self.last_trade_date = None
        self.fee_rate = 0.001  # 0.1% trading fee
        
        # Email configuration (update with your actual SMTP settings)
        self.email_config = {
            "smtp_server": "smtp.gmail.com",
            "port": 587,
            "user": "your-email@gmail.com",
            "pass": "your-app-password"
        }
    
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
            # Verkauf: Erlös minus Gebühren
            gross_proceeds = amount_euro
            fees = gross_proceeds * self.fee_rate
            net_proceed = gross_proceeds - fees
            return {
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
            return f"Excel-Datei '{filename}' wurde erfolgreich erstellt."
        except ImportError:
            return "Fehler: pandas nicht installiert. Bitte 'pip install pandas openpyxl' ausführen."
    
    def send_email_report(self, subject: str, body: str) -> str:
        """
        Sendet einen Statusbericht per E-Mail.
        Hinweis: SMTP-Daten in self.email_config eintragen für echten Versand.
        """
        try:
            import smtplib
            from email.mime.text import MIMEText
            from email.mime.multipart import MIMEMultipart
            
            msg = MIMEMultipart()
            msg['Subject'] = f"Trading Bot Update: {subject}"
            msg['From'] = self.email_config["user"]
            msg['To'] = self.email_config["user"]  # An sich selbst senden
            msg.attach(MIMEText(body, 'plain'))
            
            # SMTP-Verbindung aufbauen (auskommentiert zur Sicherheit)
            # with smtplib.SMTP(self.email_config["smtp_server"], self.email_config["port"]) as server:
            #     server.starttls()
            #     server.login(self.email_config["user"], self.email_config["pass"])
            #     server.send_message(msg)
            
            return f"E-Mail-Bericht '{subject}' wurde vorbereitet (SMTP-Logik bereit)."
        except ImportError:
            return "Fehler: smtplib nicht verfügbar. E-Mail-Versand nicht möglich."
        except Exception as e:
            return f"E-Mail-Versand fehlgeschlagen: {str(e)}"


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

# Create the trading agent (agno 2.x compatible)
trading_agent = Agent(
    name="Ultimate-Trader",
    model=OpenAIChat(
        id="qwen2.5:latest",
        base_url="http://localhost:11434/v1",
        api_key="test"
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
