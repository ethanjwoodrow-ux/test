"""Configuration for the AI crypto trader (loaded from .env)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(ENV_PATH)

# Must be set exactly to this in .env before the bot will place real orders.
LIVE_CONFIRM_PHRASE = "I_UNDERSTAND_THE_RISKS"


def _float(name: str, default: str) -> float:
    return float(os.getenv(name, default))


@dataclass
class RiskLimits:
    max_position_pct: float = 25.0    # no single coin above this % of total equity
    max_trade_pct: float = 10.0       # no single buy above this % of total equity
    min_cash_pct: float = 20.0        # always keep this % of equity in cash
    min_confidence: float = 0.6       # ignore AI calls below this confidence (0-1)
    max_daily_loss_pct: float = 5.0   # stop opening new positions after this daily drop
    stop_loss_pct: float = 8.0        # auto-sell a coin this far below its entry price
    min_order_usd: float = 10.0       # skip orders smaller than this


@dataclass
class Settings:
    mode: str                  # "paper" or "live"
    exchange: str              # any ccxt exchange id, e.g. kraken, binance, coinbase
    symbols: list[str]
    quote_currency: str
    model: str
    effort: str
    data_dir: Path
    starting_cash: float
    paper_fee_pct: float
    paper_slippage_pct: float
    exchange_api_key: str
    exchange_api_secret: str
    exchange_password: str
    live_confirmed: bool
    limits: RiskLimits

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    @classmethod
    def from_env(cls) -> "Settings":
        symbols = [s.strip().upper() for s in os.getenv("TRADER_SYMBOLS", "BTC/USD,ETH/USD,SOL/USD").split(",") if s.strip()]
        mode = os.getenv("TRADER_MODE", "paper").strip().lower()
        if mode not in ("paper", "live"):
            raise ValueError(f"TRADER_MODE must be 'paper' or 'live', got {mode!r}")
        return cls(
            mode=mode,
            exchange=os.getenv("TRADER_EXCHANGE", "kraken").strip().lower(),
            symbols=symbols,
            quote_currency=symbols[0].split("/")[1] if symbols else "USD",
            model=os.getenv("TRADER_MODEL", "claude-opus-5-5"),
            effort=os.getenv("TRADER_EFFORT", "high"),
            data_dir=Path(os.getenv("TRADER_DATA_DIR", str(Path.home() / "ai_trader_data"))),
            starting_cash=_float("PAPER_STARTING_CASH", "10000"),
            paper_fee_pct=_float("PAPER_FEE_PCT", "0.26"),
            paper_slippage_pct=_float("PAPER_SLIPPAGE_PCT", "0.05"),
            exchange_api_key=os.getenv("EXCHANGE_API_KEY", ""),
            exchange_api_secret=os.getenv("EXCHANGE_API_SECRET", ""),
            exchange_password=os.getenv("EXCHANGE_API_PASSWORD", ""),
            live_confirmed=os.getenv("LIVE_TRADING_CONFIRMED", "") == LIVE_CONFIRM_PHRASE,
            limits=RiskLimits(
                max_position_pct=_float("RISK_MAX_POSITION_PCT", "25"),
                max_trade_pct=_float("RISK_MAX_TRADE_PCT", "10"),
                min_cash_pct=_float("RISK_MIN_CASH_PCT", "20"),
                min_confidence=_float("RISK_MIN_CONFIDENCE", "0.6"),
                max_daily_loss_pct=_float("RISK_MAX_DAILY_LOSS_PCT", "5"),
                stop_loss_pct=_float("RISK_STOP_LOSS_PCT", "8"),
                min_order_usd=_float("RISK_MIN_ORDER_USD", "10"),
            ),
        )
