"""
资金费率套利策略
"""
from src.strategies.base import BaseStrategy
from src.core.context import SharedContext, TradeSignal


class FundingArbitrageStrategy(BaseStrategy):
    """资金费率套利 — 多现货 + 空合约"""

    @property
    def name(self) -> str:
        return "arbitrage"

    def get_interval(self) -> int:
        return self.config.get("loop_interval", 300)

    def get_symbols(self, ctx: SharedContext) -> list[str]:
        return self.config.get("whitelist", [])

    def on_cycle(self, ctx: SharedContext) -> list[TradeSignal]:
        # TODO Phase 4: 迁移 FundingArbitrage 逻辑
        return []
