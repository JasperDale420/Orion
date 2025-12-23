from enum import Enum


class ScreenerContractOrderByField(str, Enum):
    BID_ASK_VOL = "bid_ask_vol"
    BULL_BEAR_VOL = "bull_bear_vol"
    CONTRACT_PRICING = "contract_pricing"
    DAILY_PERC_CHANGE = "daily_perc_change"
    DIFF = "diff"
    DTE = "dte"
    EARNINGS = "earnings"
    EXPIRES = "expires"
    EXPIRY = "expiry"
    FLOOR_VOLUME = "floor_volume"
    FLOOR_VOLUME_RATIO = "floor_volume_ratio"
    FROM_HIGH = "from_high"
    FROM_LOW = "from_low"
    IV = "iv"
    MULTILEG_VOLUME = "multileg_volume"
    OPEN_INTEREST = "open_interest"
    PREMIUM = "premium"
    SPREAD = "spread"
    STOCK_PRICE = "stock_price"
    TAPE_TIME = "tape_time"
    TICKER = "ticker"
    TOTAL_MULTILEG_VOLUME_RATIO = "total_multileg_volume_ratio"
    TRADES = "trades"
    VOLUME = "volume"
    VOLUME_OI_RATIO = "volume_oi_ratio"
    VOLUME_TICKER_VOL_RATIO = "volume_ticker_vol_ratio"

    def __str__(self) -> str:
        return str(self.value)
