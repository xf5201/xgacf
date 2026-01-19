import asyncio
import logging
from aiogram import Bot

from core.database import db
from core.config import config

logger = logging.getLogger(__name__)

class OrderCleaner:
    def __init__(self, bot: Bot):
        self.bot = bot
        # 默认每 10000 秒检查一次，实际运行中可以根据需求调整
        self.check_interval = 10000

    async def start(self):
        logger.info(f"Order cleaner started (check every {self.check_interval}s)")
        while True:
            try:
                await self.clean_pending_orders()
                await self.clean_checking_orders()
            except Exception as e:
                logger.exception(f"Cleaner error: {e}")
            
            await asyncio.sleep(self.check_interval)

    async def clean_pending_orders(self):
        """清理已创建但未支付的超时订单"""
        try:
            timeout = getattr(config, 'ORDER_TIMEOUT_MINUTES', 30)
            expired_orders = await db.get_expired_pending_orders(timeout)
            if not expired_orders:
                return

            logger.info(f"Found {len(expired_orders)} expired pending orders")

            for order in expired_orders:
                try:
                    msg = (
                        f"Order #{order['order_id']} has expired.\n"
                        f"It was automatically canceled because you did not click 'I have paid' within {timeout} minutes."
                    )
                    await self.bot.send_message(order['user_id'], msg)
                    await db.delete_order(order['order_id'])
                    logger.info(f"Canceled and deleted expired order: {order['order_id']}")
                except Exception as e:
                    logger.error(f"Failed to clean pending order {order['order_id']}: {e}")

        except Exception as e:
            logger.error(f"Error checking pending orders: {e}")

    async def clean_checking_orders(self):
        """清理已点击支付但未到账的超时订单"""
        try:
            timeout = getattr(config, 'ORDER_TIMEOUT_MINUTES', 30)
            expired_orders = await db.get_expired_checking_orders(timeout)
            if not expired_orders:
                return

            logger.info(f"Found {len(expired_orders)} expired checking orders")

            for order in expired_orders:
                try:
                    await db.mark_order_expired(order['order_id'])
                    msg = (
                        f"Order #{order['order_id']} payment not received.\n"
                        f"No funds were detected after waiting for {timeout} minutes.\n\n"
                        f"If you have already transferred, please contact support with the transaction hash (TxID) for manual processing."
                    )
                    await self.bot.send_message(order['user_id'], msg)
                    logger.info(f"Marked checking order as expired: {order['order_id']}")
                except Exception as e:
                    logger.error(f"Failed to clean checking order {order['order_id']}: {e}")

        except Exception as e:
            logger.error(f"Error checking checking orders: {e}")

cleaner = None

def init_cleaner(bot):
    global cleaner
    cleaner = OrderCleaner(bot)
    return cleaner
