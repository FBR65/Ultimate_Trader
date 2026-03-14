"""
Unit tests for the TradingEngine class in Ultimate Trader.
"""
import unittest
from datetime import date, timedelta
from main import TradingEngine


class TestTradingEngine(unittest.TestCase):
    """Test cases for TradingEngine class."""

    def setUp(self):
        """Set up test fixtures."""
        self.engine = TradingEngine()

    def test_initial_values(self):
        """Test that initial values are set correctly."""
        self.assertEqual(self.engine.start_capital, 10000.0)
        self.assertEqual(self.engine.stop_loss_threshold, 0.05)
        self.assertEqual(self.engine.daily_spending_limit, 2000.0)
        self.assertEqual(self.engine.fee_rate, 0.001)

    def test_check_budget_within_limit(self):
        """Test budget check when amount is within daily limit."""
        result = self.engine.check_budget(500.0)
        self.assertTrue(result["allowed"])
        self.assertEqual(result["remaining"], 1500.0)

    def test_check_budget_exceeds_limit(self):
        """Test budget check when amount exceeds daily limit."""
        # First use most of the budget
        self.engine.check_budget(1800.0)
        # Try to exceed
        result = self.engine.check_budget(300.0)
        self.assertFalse(result["allowed"])
        self.assertEqual(result["remaining"], 0.0)
        self.assertEqual(result["excess"], 100.0)

    def test_check_budget_resets_next_day(self):
        """Test that budget resets when date changes."""
        # Use some budget
        self.engine.check_budget(1500.0)
        self.assertEqual(self.engine.today_spent, 1500.0)
        
        # Simulate next day by changing last_trade_date
        self.engine.last_trade_date = date.today() - timedelta(days=1)
        result = self.engine.check_budget(500.0)
        
        self.assertTrue(result["allowed"])
        self.assertEqual(result["remaining"], 1500.0)
        self.assertEqual(self.engine.today_spent, 500.0)

    def test_calculate_trade_buy(self):
        """Test buy trade calculations."""
        result = self.engine.calculate_trade(1000.0, 100.0, "buy")
        
        self.assertEqual(result["shares"], 9.9900)  # 1000 / (100 * 1.001)
        self.assertEqual(result["fees_euro"], 1.00)
        self.assertEqual(result["price_per_share"], 100.0)
        self.assertEqual(result["total_invested"], 999.0)

    def test_calculate_trade_sell(self):
        """Test sell trade calculations."""
        result = self.engine.calculate_trade(1000.0, 100.0, "sell")
        
        self.assertEqual(result["net_proceed_euro"], 999.0)  # 1000 - 0.1%
        self.assertEqual(result["fees_euro"], 1.0)  # 0.1% of 1000
        self.assertEqual(result["gross_proceeds"], 1000.0)

    def test_check_volatility_high_risk(self):
        """Test high volatility assessment."""
        result = self.engine.check_volatility(1.8)
        self.assertIn("HOCHRISIKO", result)
        self.assertIn("1.8", result)

    def test_check_volatility_moderate(self):
        """Test moderate volatility assessment."""
        result = self.engine.check_volatility(1.2)
        self.assertIn("MODERAT", result)

    def test_check_volatility_defensive(self):
        """Test defensive volatility assessment."""
        result = self.engine.check_volatility(0.7)
        self.assertIn("DEFENSIV", result)

    def test_check_volatility_no_data(self):
        """Test when beta is None."""
        result = self.engine.check_volatility(None)
        self.assertIn("Keine Daten verfügbar", result)

    def test_monitor_stop_loss_triggered(self):
        """Test stop loss trigger when loss exceeds threshold."""
        result = self.engine.monitor_stop_loss(
            symbol="VWCE",
            purchase_price=100.0,
            current_price=90.0,  # 10% loss
            shares=10.0
        )
        
        self.assertEqual(result["status"], "STOP_LOSS_TRIGGERED")
        self.assertEqual(result["recommendation"], "SELL")
        self.assertIn("ALARM", result["message"])

    def test_monitor_stop_loss_hold(self):
        """Test hold recommendation when loss is below threshold."""
        result = self.engine.monitor_stop_loss(
            symbol="VWCE",
            purchase_price=100.0,
            current_price=97.0,  # 3% loss, below 5% threshold
            shares=10.0
        )
        
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["recommendation"], "HOLD")

    def test_monitor_stop_loss_invalid_purchase_price(self):
        """Test error handling for zero purchase price."""
        result = self.engine.monitor_stop_loss(
            symbol="VWCE",
            purchase_price=0.0,
            current_price=100.0,
            shares=10.0
        )
        
        self.assertEqual(result["status"], "error")
        self.assertIn("Ungültiger Kaufkurs", result["message"])

    def test_monitor_stop_loss_profit(self):
        """Test monitoring when position is in profit."""
        result = self.engine.monitor_stop_loss(
            symbol="VWCE",
            purchase_price=100.0,
            current_price=110.0,  # 10% profit
            shares=10.0
        )
        
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["pnl_pct"], 10.0)

    def test_export_to_excel(self):
        """Test Excel export functionality."""
        test_data = [
            {"symbol": "VWCE", "shares": 10.0, "price": 100.0, "value": 1000.0}
        ]
        result = self.engine.export_to_excel(test_data)
        
        self.assertIn("Excel-Datei", result)
        self.assertIn("Portfolio_Status", result)

    def test_send_email_report(self):
        """Test email report preparation."""
        result = self.engine.send_email_report("Test Subject", "Test Body")
        
        self.assertIn("E-Mail-Bericht", result)
        self.assertIn("Test Subject", result)


if __name__ == "__main__":
    unittest.main()
