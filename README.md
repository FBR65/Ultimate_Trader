# Ultimate Trader

An AI-powered trading agent system for simulated stock and ETF trading, built with the Agno framework.

## Overview

Ultimate Trader is an automated trading system that uses real market data from Yahoo Finance to execute simulated trades. The system features comprehensive risk management, automated daily routines, and detailed reporting capabilities.

## Features

- **AI-Powered Trading Agent**: Uses an LLM-based agent to analyze market data and execute trades
- **Risk Management**: Stop-loss monitoring, daily spending limits, and volatility assessment
- **Automated Scheduling**: Daily trading routines based on the trading calendar
- **Portfolio Reporting**: Excel export and email notifications for portfolio status
- **Backtesting Framework**: Unit tests for trading engine components

## System Requirements

- Python 3.12 or higher
- Ollama (for local LLM inference)
- Yahoo Finance API access

## Installation

1. Clone the repository:
   ```bash
   git clone <repository-url>
   cd Ultimate_Trader
   ```

2. Install dependencies using uv:
   ```bash
   uv sync
   ```

3. Configure Ollama:
   - Ensure Ollama is running locally
   - Pull the qwen2.5 model: `ollama pull qwen2.5`

4. Configure email settings (optional):
   - Edit `main.py` and update `email_config` with your SMTP settings

## Usage

Run the application:
```bash
python main.py
```

The system provides three execution modes:

1. **Single Execution**: Run one trading session immediately
2. **Automated Scheduler**: Run daily trading at 09:05 UTC
3. **Manual Query**: Execute custom trading queries

## Trading Strategy

The system follows a diversified investment strategy:

- **Asset Allocation**: 60% World ETF (VWCE/EUNL), 20% Bond ETFs, 20% Blue Chips
- **Risk Limits**:
  - Sector exposure: Maximum 25% of capital in a single sector
  - Weighted Beta: Target range 0.8 to 1.0
  - Individual position: Maximum 10% of portfolio
- **Stop-Loss**: 5% threshold for automatic position closure
- **Daily Budget**: Maximum €2,000 per trading day

## Trading Engine

The [`TradingEngine`](main.py:23) class provides core functionality:

- [`check_budget()`](main.py:42): Validates daily spending limits
- [`calculate_trade()`](main.py:59): Computes exact share quantities and fees (0.1%)
- [`check_volatility()`](main.py:86): Assesses risk via Beta factor
- [`monitor_stop_loss()`](main.py:100): Tracks positions for stop-loss triggers
- [`export_to_excel()`](main.py:136): Generates portfolio status reports
- [`send_email_report()`](main.py:149): Sends status notifications

## Testing

Run unit tests:
```bash
uv run python -m unittest test_trading_engine.py
```

## Project Structure

```
Ultimate_Trader/
├── main.py                 # Core trading agent and engine
├── test_trading_engine.py  # Unit tests for trading engine
├── pyproject.toml          # Project dependencies and configuration
├── .gitignore             # Git ignore rules
└── README.md              # Project documentation
```

## Dependencies

- agno >= 1.0.0
- yfinance >= 0.2.40
- schedule >= 1.2.0
- pandas >= 2.0.0
- openpyxl >= 3.1.0
- openai >= 1.0.0

## License

This project is provided as-is for educational and simulation purposes only.

## Disclaimer

This system is designed for simulated trading only. Do not use with real money or live trading accounts without thorough testing and validation.