"""Market cap fetch and cap-tier auto-tagging."""
from __future__ import annotations

from dataclasses import dataclass

try:
    import yfinance as yf
except ImportError:  # pragma: no cover
    yf = None  # type: ignore

from .. import config, db


@dataclass
class Fundamentals:
    symbol: str
    market_cap_usd: float | None
    pe_ratio: float | None
    currency: str | None
    ok: bool
    error: str | None = None


def fetch_fundamentals(symbol: str) -> Fundamentals:
    if yf is None:
        return Fundamentals(symbol, None, None, None, False, "yfinance not installed")
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        return Fundamentals(
            symbol=symbol,
            market_cap_usd=info.get("marketCap"),
            pe_ratio=info.get("trailingPE"),
            currency=info.get("currency"),
            ok=True,
        )
    except Exception as exc:  # noqa: BLE001
        return Fundamentals(symbol, None, None, None, False, f"{type(exc).__name__}: {exc}")


def classify_cap_tier(market_cap_usd: float | None) -> str | None:
    if market_cap_usd is None:
        return None
    large = config.get_float("large_cap_marketcap_usd", 10_000_000_000)
    small = config.get_float("small_cap_marketcap_usd", 2_000_000_000)
    if market_cap_usd >= large:
        return "LARGE"
    if market_cap_usd >= small:
        return "MID"
    return "SMALL"


def tag_cap_tier(symbol: str) -> tuple[str | None, Fundamentals]:
    """Fetch fundamentals, classify, and store cap_tier in the DB if found."""
    f = fetch_fundamentals(symbol)
    tier = classify_cap_tier(f.market_cap_usd)
    if tier is not None:
        with db.connect() as conn:
            conn.execute(
                "UPDATE stocks SET cap_tier = ?, updated_at = CURRENT_TIMESTAMP WHERE symbol = ?",
                (tier, symbol),
            )
    return tier, f
