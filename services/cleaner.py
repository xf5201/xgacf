import asyncio
import logging
from aiogram import Bot

from core.database import db
from core.config import config

logger = logging.getLogger(__name__)

class OrderCleaner:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.check_interval = 10000

    async def start(self):
        logger.info(f"🧹 订单清理任务已启动 (每 {self.check_interval} 秒检查一次超时)")
        while True:
            try:
                # 执行清理逻辑
                await self.clean_pending_orders()
                await self.clean_checking_orders()
            except Exception as e:
                # 使用 logger.exception 会自动打印完整的堆栈信息
                logger.exception(f"❌ Cleaner Error: {e}")
            
            # ✅ 核心修复：无论上面是否出错，都休眠固定时间，防止死循环导致CPU飙升
            await asyncio.sleep(self.check_interval)

    async def clean_pending_orders(self):
        """处理场景1：用户下了单，但半小时内没点“我已支付”"""
        try:
            timeout = getattr(config, 'ORDER_TIMEOUT_MINUTES', 30)
            expired_orders = await db.get_expired_pending_orders(timeout)
            if not expired_orders:
                return

            logger.info(f"🧹 发现 {len(expired_orders)} 个未支付的过期订单...")

            for order in expired_orders:
                try:
                    msg = (
                        f"⚠️ 订单 #{order['order_id']} 已超时。\n"
                        f"因您超过 {timeout} 分钟未点击“我已支付”，该订单已自动取消。"
                    )
                    await self.bot.send_message(order['user_id'], msg)
                    await db.delete_order(order['order_id'])
                    logger.info(f"✅ 已取消并删除超时订单: {order['order_id']}")
                except Exception as e:
                    logger.error(f"清理 Pending 订单 {order['order_id']} 失败: {e}")

        except Exception as e:
            logger.error(f"❌ 检查 Pending 订单超时失败: {e}")

    async def clean_checking_orders(self):
        """处理场景2：用户点了“我已支付”，但半小时内没到账"""
        try:
            timeout = getattr(config, 'ORDER_TIMEOUT_MINUTES', 30)
            expired_orders = await db.get_expired_checking_orders(timeout)
            if not expired_orders:
                return

            logger.info(f"🧹 发现 {len(expired_orders)} 个未到账的过期订单...")

            for order in expired_orders:
                try:
                    await db.mark_order_expired(order['order_id'])
                    msg = (
                        f"❌ 订单 #{order['order_id']} 未收到付款。\n"
                        f"等待超过 {timeout} 分钟仍未检测到资金入账。\n\n"
                        f"如果您已经转账，请联系客服并提供交易哈希(TxID)进行人工处理。"
                    )
                    await self.bot.send_message(order['user_id'], msg)
                    logger.info(f"✅ 已标记 Checking 订单为过期: {order['order_id']}")
                except Exception as e:
                    logger.error(f"清理 Checking 订单 {order['order_id']} 失败: {e}")

        except Exception as e:
            logger.error(f"❌ 检查 Checking 订单超时失败: {e}")

cleaner = None

def init_cleaner(bot):
    global cleaner
    cleaner = OrderCleaner(bot)
    return cleaner
