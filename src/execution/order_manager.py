"""
订单管理器 — 入场/出场/止损/止盈/移动止损
"""
import logging
import time
from typing import Optional

from src.core.context import SharedContext, TradeSignal, Position
from src.core.events import EventBus, Events
from src.data.rest_client import BinanceRESTClient
from src.execution.position import PositionStore
from src.utils.constants import Direction, CloseReason

logger = logging.getLogger("unified_trader.execution.order")


class OrderManager:
    """
    统一订单管理器

    职责：
    1. 入场开仓（设置杠杆、保证金模式、市价买入）
    2. 出场平仓（市价卖出、记录盈亏）
    3. 止损/止盈/移动止损检测
    4. 仓位持久化和崩溃恢复
    """

    def __init__(self, ctx: SharedContext, events: EventBus,
                 rest: BinanceRESTClient, config: dict):
        self.ctx = ctx
        self.events = events
        self.rest = rest
        self.config = config
        self.dry_run = config.get("dry_run", True)
        self.store = PositionStore("data/positions.json")

        # 加载已有持仓
        self.store.load()

    # ═══════ 入场 ═══════

    def open_position(self, signal: TradeSignal, strategy_name: str,
                      risk_profile: dict) -> Optional[Position]:
        """
        根据信号开仓

        Args:
            signal: 策略生成的交易信号
            strategy_name: 策略名称
            risk_profile: 策略的风控配置

        Returns:
            Position 如果开仓成功，None 如果失败
        """
        sym = signal.symbol

        # 防重入
        if self.store.has(sym):
            logger.info("[%s] 已有持仓，跳过", sym)
            return None

        # 计算仓位参数
        order_usdt = risk_profile.get("order_usdt_per_symbol", 10)
        leverage = risk_profile.get("leverage", 3)
        sl_pct = risk_profile.get("stop_loss_pct", 15.0) / 100
        tp_pct = risk_profile.get("take_profit_pct", 10.0) / 100

        # 获取当前价格
        market = self.ctx.get_market(sym)
        if not market:
            price = self.rest.get_price(sym)
        else:
            bids = market.orderbook.get("bids", []) if market.orderbook else []
            asks = market.orderbook.get("asks", []) if market.orderbook else []
            best_bid = bids[0][0] if bids else 0
            best_ask = asks[0][0] if asks else 0
            price = best_ask or best_bid or market.mark_price

        if price <= 0:
            logger.warning("[%s] 无法获取价格，跳过入场", sym)
            return None

        # 计算数量
        qty = order_usdt * leverage / price

        # 精度处理
        symbol_info = self.rest.get_symbol_info(sym)
        step_size = symbol_info.get("step_size", 0.001)
        qty = round(qty / step_size) * step_size

        if qty <= 0:
            logger.warning("[%s] 数量为0，跳过", sym)
            return None

        # SL/TP 价格
        if signal.direction == Direction.LONG:
            sl_price = price * (1 - sl_pct)
            tp_price = price * (1 + tp_pct)
            side = "BUY"
        else:
            sl_price = price * (1 + sl_pct)
            tp_price = price * (1 - tp_pct)
            side = "SELL"

        # 执行
        if not self.dry_run:
            # 设置杠杆和保证金
            self.rest.set_leverage(sym, leverage)
            self.rest.set_margin_type(sym, "ISOLATED")

            # 下单
            result = self.rest.place_order(sym, side, "MARKET", qty)
            if not result:
                logger.error("[%s] 下单失败", sym)
                return None
            logger.info("[%s] 开仓成功: %s %.4f @ %.4f", sym, side, qty, price)

        # 创建仓位
        pos = Position(
            symbol=sym,
            direction=signal.direction,
            entry_price=price,
            quantity=qty,
            order_usdt=order_usdt,
            leverage=leverage,
            sl_price=sl_price,
            tp_price=tp_price,
            highest_price=price,
            lowest_price=price,
            entry_time=time.time(),
            signal_snapshot=signal.features,
            strategy_name=strategy_name,
        )

        # 持久化
        self.store.add(pos)
        self.ctx.add_position(pos)
        self.ctx.daily_trades += 1

        self.events.emit(Events.POSITION_OPENED, {"position": pos})

        if self.dry_run:
            logger.info(
                "[DRY] %s 开仓 %s @ %.4f qty=%.4f SL=%.4f TP=%.4f",
                sym, signal.direction.value, price, qty, sl_price, tp_price,
            )

        return pos

    # ═══════ 出场 ═══════

    def close_position(self, symbol: str, reason: CloseReason,
                       exit_price: Optional[float] = None) -> Optional[float]:
        """
        平仓

        Returns:
            已实现盈亏 (USDT)，如果失败返回 None
        """
        pos_data = self.store.get(symbol)
        if not pos_data:
            return None

        pos = PositionStore.dict_to_pos(pos_data)

        # 获取出场价格
        if exit_price is None:
            market = self.ctx.get_market(symbol)
            if market:
                bids = market.orderbook.get("bids", []) if market.orderbook else []
                asks = market.orderbook.get("asks", []) if market.orderbook else []
                if pos.direction == Direction.LONG:
                    exit_price = bids[0][0] if bids else market.mark_price
                else:
                    exit_price = asks[0][0] if asks else market.mark_price
            if exit_price is None or exit_price <= 0:
                exit_price = self.rest.get_price(symbol)
            if exit_price <= 0:
                logger.error("[%s] 无法获取平仓价格", symbol)
                return None

        # 执行
        if not self.dry_run:
            side = "SELL" if pos.direction == Direction.LONG else "BUY"
            result = self.rest.place_order(symbol, side, "MARKET", pos.quantity)
            if not result:
                logger.error("[%s] 平仓下单失败", symbol)
                return None

        # 计算盈亏
        if pos.direction == Direction.LONG:
            pnl = (exit_price - pos.entry_price) * pos.quantity
        else:
            pnl = (pos.entry_price - exit_price) * pos.quantity

        pnl_pct = pnl / (pos.order_usdt * pos.leverage) * 100

        # 更新风控状态
        self.ctx.daily_pnl += pnl
        if pnl < 0:
            self.ctx.consecutive_losses += 1
        else:
            self.ctx.consecutive_losses = 0

        # 更新仓位
        pos.exit_price = exit_price
        pos.exit_time = time.time()
        pos.exit_reason = reason.value

        # 持久化
        self.store.remove(symbol)
        self.ctx.remove_position(symbol)

        # 事件
        self.events.emit(Events.POSITION_CLOSED, {
            "position": pos,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "reason": reason.value,
            "exit_price": exit_price,
            "hold_minutes": (pos.exit_time - pos.entry_time) / 60 if pos.exit_time else 0,
        })

        prefix = "[DRY]" if self.dry_run else ""
        logger.info(
            "%s [%s] %s 平仓 @ %.4f PnL=%+.3fU(%+.1f%%) 原因=%s | 持仓%.0f分钟",
            prefix, symbol, pos.direction.value, exit_price,
            pnl, pnl_pct, reason.value,
            (time.time() - pos.entry_time) / 60,
        )

        return pnl

    # ═══════ 止损/止盈/移动止损检测 ═══════

    def check_exits(self) -> list[tuple[str, CloseReason, float]]:
        """
        检查所有持仓是否需要出场

        Returns:
            [(symbol, reason, current_price), ...] 需要出场的列表
        """
        exits = []
        symbols = self.store.get_all().keys()

        # 批量获取价格
        if symbols:
            prices = self.rest.get_prices_batch(list(symbols))
        else:
            return exits

        for sym, pos_data in self.store.get_all().items():
            pos = PositionStore.dict_to_pos(pos_data)
            price = prices.get(sym, 0)
            if price <= 0:
                continue

            # 更新高低价
            if price > pos.highest_price:
                pos_data["highest_price"] = price
                self.store.update(pos)
            if price < pos.lowest_price:
                pos_data["lowest_price"] = price
                self.store.update(pos)

            # 检查 TP
            if pos.direction == Direction.LONG and price >= pos.tp_price:
                exits.append((sym, CloseReason.TAKE_PROFIT, price))
                continue
            elif pos.direction == Direction.SHORT and price <= pos.tp_price:
                exits.append((sym, CloseReason.TAKE_PROFIT, price))
                continue

            # 检查移动止损
            prof_pct = (price - pos.entry_price) / pos.entry_price
            if pos.direction == Direction.LONG:
                # 激活移动止损
                activate = self.config.get("trailing_stop_activate_pct", 7.0) / 100
                callback = self.config.get("trailing_stop_callback_pct", 5.0) / 100

                if prof_pct >= activate and not pos.trailing_stop_active:
                    pos_data["trailing_stop_active"] = True
                    pos_data["sl_price"] = pos.highest_price * (1 - callback)
                    self.store.update(pos)
                    logger.debug("[%s] 移动止损激活 @ %.4f", sym, price)

                # 检查移动止损触发
                if pos.trailing_stop_active:
                    trail_sl = pos.highest_price * (1 - callback)
                    if price <= trail_sl:
                        exits.append((sym, CloseReason.TRAILING_STOP, price))
                        continue

            # 检查硬止损
            if pos.direction == Direction.LONG and price <= pos.sl_price:
                exits.append((sym, CloseReason.STOP_LOSS, price))
            elif pos.direction == Direction.SHORT and price >= pos.sl_price:
                exits.append((sym, CloseReason.STOP_LOSS, price))

        return exits

    # ═══════ 崩溃恢复 ═══════

    def recover_from_exchange(self) -> list[Position]:
        """从交易所恢复未知持仓"""
        try:
            exchange_positions = self.rest.get_all_positions()
            new_positions = self.store.sync_with_exchange(exchange_positions)

            recovered = []
            for sym, ep in new_positions.items():
                pos = Position(
                    symbol=sym,
                    direction=Direction.LONG if float(ep.get("positionAmt", 0)) > 0 else Direction.SHORT,
                    entry_price=float(ep.get("entryPrice", 0)),
                    quantity=abs(float(ep.get("positionAmt", 0))),
                    strategy_name="recovered",
                )
                self.store.add(pos)
                self.ctx.add_position(pos)
                recovered.append(pos)
                logger.warning("恢复未知持仓: %s %s", sym, pos.direction.value)

            return recovered
        except Exception as e:
            logger.warning("从交易所恢复持仓失败: %s", e)
            return []
