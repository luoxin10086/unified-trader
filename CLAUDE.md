# CLAUDE.md — 统一交易框架

A unified algorithmic trading framework for Binance USDT-M perpetual futures, merging the strategies from `ai-hedge-fund-crypto` and `fresh-coin-trader` into a single, extensible system.

## Architecture

```
main.py (entry)
  └── Engine (src/core/engine.py)
        ├── SharedContext — all market data, positions, account state
        ├── EventBus — pub/sub for decoupled components
        ├── Scheduler — multi-period task scheduling (30s to 30min)
        │
        ├── Data Layer (src/data/)
        │   ├── FeedManager — WebSocket + REST orchestrator
        │   ├── BinanceWSClient — 5-stream WebSocket
        │   ├── BinanceRESTClient — unified API wrapper
        │   └── DataCollector — smart money, OI, funding rate polling
        │
        ├── Strategies (src/strategies/)
        │   ├── NewCoinStrategy — multi-window flow + orderflow patterns
        │   ├── TrendFollowingStrategy — AI direction + rule engine
        │   └── FundingArbitrageStrategy — spot+perp arbitrage
        │
        ├── Risk (src/risk/)
        │   └── RiskManager — account-level + per-trade filters
        │
        ├── Execution (src/execution/)
        │   └── OrderManager — entry/exit, SL/TP, position persistence
        │
        ├── Recording (src/recording/)
        │   └── DataRecorder — signals, orders, klines, equity curve
        │
        └── Notify (src/notify/)
            └── TelegramNotifier — trade alerts
```

## Key Design Principles

1. **Strategy as Plugin** — strategies implement `BaseStrategy`, share nothing
2. **Shared Infrastructure** — one WS connection, one risk engine, one recorder
3. **Scheduler, Not DAG** — simple time-based scheduling, no LangGraph
4. **Explicit State** — all state in `SharedContext`, not hidden in graph nodes
5. **Dry-Run First** — every strategy supports simulation mode

## Strategy Interface

```python
class BaseStrategy(ABC):
    name: str                          # unique identifier
    get_interval() -> int              # cycle interval (seconds)
    get_symbols(ctx) -> list[str]      # symbols to monitor
    on_cycle(ctx) -> list[TradeSignal] # called every cycle
    on_start(ctx) / on_stop(ctx)       # lifecycle hooks
    get_risk_profile() -> dict         # strategy risk params
```

## Running

```bash
# Dry run (default)
python main.py

# Live trading (set dry_run: false in config.yaml)
python main.py
```

## Migration Status

| Phase | Status |
|-------|--------|
| Phase 1: Foundation | ✅ Complete |
| Phase 2: Data Layer | ⏳ Pending |
| Phase 3: Risk + Execution | ⏳ Pending |
| Phase 4: Strategy Migration | ⏳ Pending |
| Phase 5: Recording + Analysis | ⏳ Pending |
| Phase 6: Deploy Scripts | ⏳ Pending |
