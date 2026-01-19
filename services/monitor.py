import logging
import asyncio
import aiohttp
from aiogram import Bot
from pytoniq import Address

from core.database import db
from services.fragment import fragment_service
from services.hd_wallet import hd_manager
from core.config import config

logger = logging.getLogger(__name__)

class Monitor:
    def __init__(self, bot: Bot):
        self.bot = bot
        self.is_running = False
        self.debug_mode = config.TESTNET
    
    async def check_orders_loop(self):
        logger.info("Monitor loop started")
        while self.is_running:
            try:
                await self.check_orders()
                
                # 动态休眠: 没订单时多睡会儿
                checking_orders = await db.get_all_checking_orders()
                sleep_time = 10 if checking_orders else 15
                await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                logger.info("Monitor received stop signal")
                break
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
                await asyncio.sleep(10)

    async def check_orders(self):
        try:
            client = await hd_manager.ensure_connected()
        except Exception as e:
            logger.error(f"Failed to connect to TON: {e}")
            return 

        orders = await db.get_all_checking_orders()
        if not orders:
            return

        logger.info(f"Found {len(orders)} orders to check")

        for order in orders:
            try:
                currency = order.get("payment_method")
                status = order.get("status")
                
                if status == "completed":
                    continue

                if currency == "ton":
                    await self.check_ton_payment(order, client)
                elif currency == "trc20":
                    await self.check_trc20_payment(order)
                # OkPay is handled by callback, skip here

            except Exception as e:
                logger.error(f"Error checking order {order['order_id']}: {e}")

    async def check_ton_payment(self, order, client):
        order_id = order.get("order_id")
        ton_addr = order.get("ton_addr")
        expected_usdt = order.get("amount_usdt")

        if not ton_addr:
            return

        try:
            jetton_wallet_address = Address(ton_addr)
            
            result = await client.run_get_method(
                address=jetton_wallet_address,
                method="get_wallet_data",
                stack=[]
            )

            if result and len(result) > 0:
                current_balance = int(result[0])
                required_balance = int(expected_usdt * 1_000_000)

                logger.info(f"[TON] Order {order_id}: {current_balance / 1_000_000} USDT")

                if current_balance >= required_balance:
                    logger.info(f"Order {order_id} paid successfully!")
                    await self._process_success(order)
                else:
                    logger.debug(f"Order {order_id} balance insufficient, keep monitoring")

        except Exception as e:
            logger.error(f"Failed to query TON balance for {order_id}: {e}")

    async def check_trc20_payment(self, order):
        order_id = order.get("order_id")
        trc20_addr = order.get("trc20_addr")
        expected_usdt = order.get("amount_usdt")
        contract_address = config.TRC20_USDT_CONTRACT
        trongrid_key = config.TRONGRID_API_KEY

        if not trc20_addr:
            return

        try:
            url = f"https://api.trongrid.io/v1/accounts/{trc20_addr}/trc20"
            params = {
                "limit": 1,
                "contract_address": contract_address
            }
            headers = {}
            if trongrid_key:
                headers["TRON-PRO-API-KEY"] = trongrid_key
            
            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("data") and len(data["data"]) > 0:
                            balance_str = data["data"][0].get("balance", "0")
                            current_balance = int(balance_str)
                            required_balance = int(expected_usdt * 1_000_000)
                            
                            logger.info(f"[TRC20] Order {order_id}: {current_balance / 1_000_000} USDT")

                            if current_balance >= required_balance:
                                logger.info(f"Order {order_id} paid successfully!")
                                await self._process_success(order)
                        else:
                            logger.debug(f"Order {order_id} TRC20 no data")
                    else:
                        logger.warning(f"TronGrid API failed Status: {resp.status}")

        except Exception as e:
            logger.error(f"Failed to query TRC20 balance for {order_id}: {e}")

    async def _process_success(self, order):
        order_id = order.get("order_id")
        try:
            current_order = await db.get_order(order_id)
            if current_order.get("status") in ["paid", "completed"]:
                logger.info(f"Order {order_id} already processed, skipping.")
                return

            await db.update_order_status(order_id, "paid")

            logger.info(f"Processing fulfillment for @{order.get('target')} ({order.get('months')}M)...")
            
            if self.debug_mode:
                logger.warning(f"TESTNET mode: Skipping actual purchase, simulating success")
                success = True
            else:
                success = await fragment_service.execute_purchase(order)

            if success:
                await db.update_order_status(order_id, "completed")
                logger.info(f"Order {order_id} completed!")

                user_id = order.get("user_id")
                if user_id:
                    try:
                        await self.bot.send_message(
                            user_id,
                            f"Payment successful! Premium activated for {order.get('months')} months."
                        )
                    except Exception as e:
                        logger.error(f"Failed to send user notification {user_id}: {e}")
            else:
                await db.update_order_status(order_id, "checking")
                logger.error(f"Order {order_id} fulfillment failed, will retry.")

        except Exception as e:
            logger.error(f"Error processing success for order {order_id}: {e}")
    
    async def start(self):
        self.is_running = True
        await self.check_orders_loop()

    def stop(self):
        self.is_running = False

_monitor_instance: Monitor = None

def init_monitor(bot: Bot) -> Monitor:
    global _monitor_instance
    _monitor_instance = Monitor(bot)
    return _monitor_instance

async def stop_monitor():
    global _monitor_instance
    if _monitor_instance:
        _monitor_instance.stop()
        _monitor_instance = None
