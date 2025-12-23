from typing import Dict, List, Optional

from pydantic import BaseModel


class FeatureSet(BaseModel):
    """
    Defines a named collection of features that a Solver depends on.
    """

    set_id: str
    description: str
    feature_keys: List[str]


class FeatureRegistry:
    """
    Central repository of known Feature Sets.
    """

    _registry: Dict[str, FeatureSet] = {
        "v1_legacy": FeatureSet(
            set_id="v1_legacy",
            description="All available features (OHLCV + Basic Indicators + Simple Flow)",
            feature_keys=["*"],  # Implicitly "all"
        ),
        "v2_intraday": FeatureSet(
            set_id="v2_intraday",
            description="Optimized feature set for intraday logic",
            feature_keys=["close", "volume", "rsi_14", "sma_20", "vwap", "session_volatility", "flow_net_premium_15m"],
        ),
        "v2_swing": FeatureSet(
            set_id="v2_swing",
            description="Optimized feature set for swing logic (daily context)",
            feature_keys=[
                "close",
                "sma_50",
                "sma_200",
                "rsi_14",
                "atr_14",
                "flow_call_premium_1h",
                "flow_put_premium_1h",
            ],
        ),
    }

    @classmethod
    def get(cls, set_id: str) -> Optional[FeatureSet]:
        return cls._registry.get(set_id)

    @classmethod
    def validate_id(cls, set_id: str) -> bool:
        return set_id in cls._registry

    @classmethod
    def list_all(cls) -> List[str]:
        return list(cls._registry.keys())
