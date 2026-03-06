from datetime import datetime
from uuid import UUID

from alpaca.broker.enums import PortfolioStatus, RunInitiatedFrom, RunStatus, RunType
from alpaca.broker.models.trading import Order
from alpaca.broker.requests import RebalancingConditions, Weight
from alpaca.common.models import ValidateBaseModel as BaseModel


class Portfolio(BaseModel):
    """
    Portfolio response model.

    https://docs.alpaca.markets/reference/get-v1-rebalancing-portfolios
    """

    id: UUID
    name: str
    description: str
    status: PortfolioStatus
    cooldown_days: int
    created_at: datetime
    updated_at: datetime
    weights: list[Weight]
    rebalance_conditions: list[RebalancingConditions] | None = None


class Subscription(BaseModel):
    """
    Subscription response model.

    https://docs.alpaca.markets/reference/get-v1-rebalancing-subscriptions-1
    """

    id: UUID
    account_id: UUID
    portfolio_id: UUID
    created_at: datetime
    last_rebalanced_at: datetime | None = None


class SkippedOrder(BaseModel):
    """
    Skipped order response model.

    https://docs.alpaca.markets/reference/get-v1-rebalancing-runs-run_id-1
    """

    symbol: str
    side: str | None = None
    notional: str | None = None
    currency: str | None = None
    reason: str
    reason_details: str


class RebalancingRun(BaseModel):
    """
    Rebalancing run response model.

    https://docs.alpaca.markets/reference/get-v1-rebalancing-runs
    """

    id: UUID
    account_id: UUID
    type: RunType
    amount: str | None = None
    portfolio_id: UUID
    weights: list[Weight]
    initiated_from: RunInitiatedFrom | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    canceled_at: datetime | None = None
    status: RunStatus
    reason: str | None = None
    orders: list[Order] | None = None
    failed_orders: list[Order] | None = None
    skipped_orders: list[SkippedOrder] | None = None
