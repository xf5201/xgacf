# services/monitor.py
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
        # ✅ 使用 config.TESTNET 作为调试模式判断
        self.debug_mode = config.TESTNET
    
    async def check_orders_loop(self):
        """监控主循环"""
        logger.info("🔍 监控循环已启动... (仅在用户点击支付后激活检测)")
        while self.is_running:
            try:
                await self.check_orders()
                
                # 动态休眠优化
                checking_orders = await db.get_all_checking_orders()
                if not checking_orders:
                    await asyncio.sleep(15) 
                else:
                    await asyncio.sleep(10) 

            except asyncio.CancelledError:
                logger.info("🛑 监控收到停止信号")
                break
            except Exception as e:
                logger.error(f"⚠️ 监控循环出错: {e}")
                await asyncio.sleep(10)

    async def check_orders(self):
        """检查订单"""
        try:
            # 确保 TON 连接可用
            client = await hd_manager.ensure_connected()
        except Exception as e:
            logger.error(f"❌ 无法获取 TON 连接: {e}")
            return 

        # 获取订单
        orders = await db.get_all_checking_orders()
        if not orders:
            return

        logger.info(f"🔍 发现 {len(orders)} 个待检测的订单...")

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
                elif currency == "okpay":
                    # OkPay 通过回调处理，这里跳过
                    pass

            except Exception as e:
                logger.error(f"检查订单 {order['order_id']} 失败: {e}")

    async def check_ton_payment(self, order, client):
        """检查 TON USDT 余额"""
        order_id = order.get("order_id")
        ton_addr = order.get("ton_addr")
        expected_usdt = order.get("amount_usdt")
        ton_usdt_master = config.TON_USDT_MASTER  # ✅ 使用配置文件中的合约地址

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

                logger.info(f"💰 [TON] 订单 {order_id}: {current_balance / 1_000_000} USDT")

                if current_balance >= required_balance:
                    logger.info(f"✅ 订单 {order_id} 支付成功！")
                    await self._process_success(order)
                else:
                    logger.debug(f"⏳ 订单 {order_id} 余额不足，继续监控")

        except Exception as e:
            logger.error(f"❌ 查询 TON 余额失败 {order_id}: {e}")

    async def check_trc20_payment(self, order):
        """检查 TRC20 余额"""
        order_id = order.get("order_id")
        trc20_addr = order.get("trc20_addr")
        expected_usdt = order.get("amount_usdt")
        # ✅ 使用配置文件中的合约地址和 API Key
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
                            
                            logger.info(f"💰 [TRC20] 订单 {order_id}: {current_balance / 1_000_000} USDT")

                            if current_balance >= required_balance:
                                logger.info(f"✅ 订单 {order_id} 支付成功！")
                                await self._process_success(order)
                        else:
                            logger.debug(f"⏳ 订单 {order_id} TRC20 无数据")
                    else:
                        logger.warning(f"⚠️ TronGrid API 失败 Status: {resp.status}")

        except Exception as e:
            logger.error(f"❌ 查询 TRC20 余额失败 {order_id}: {e}")

    async def _process_success(self, order):
        """支付成功后的统一处理流程"""
        order_id = order.get("order_id")
        try:
            # 1. 防止重复处理
            current_order = await db.get_order(order_id)
            if current_order.get("status") in ["paid", "completed"]:
                logger.info(f"ℹ️ 订单 {order_id} 已在处理中或已完成，跳过。")
                return

            # 2. 更新状态为 paid
            await db.update_order_status(order_id, "paid")

            # 3. 发货 - 这里会正常调用 fragment.py
            logger.info(f"🎁 正在发货: @{order.get('target')} ({order.get('months')}个月)...")
            
            # ✅ 使用 config.TESTNET 判断是否跳过实际购买
            if self.debug_mode:
                logger.warning(f"🔧 TESTNET 模式: 跳过实际购买，模拟成功")
                success = True
            else:
                success = await fragment_service.execute_purchase(order)

            if success:
                # 4. 发货成功
                await db.update_order_status(order_id, "completed")
                logger.info(f"🎉 订单 {order_id} 完成！")

                # 5. 通知用户
                user_id = order.get("user_id")
                if user_id:
                    try:
                        await self.bot.send_message(
                            user_id,
                            f"✅ 支付成功！\n\n已为您激活 Telegram Premium ({order.get('months')} 个月)。"
                        )
                    except Exception as e:
                        logger.error(f"发送用户通知失败 {user_id}: {e}")
            else:
                # 发货失败，回滚状态以便重试
                await db.update_order_status(order_id, "checking")
                logger.error(f"❌ 订单 {order_id} 发货失败，下次重试")

        except Exception as e:
            logger.error(f"❌ 处理订单 {order_id} 成功流程时发生错误: {e}")
    
    async def start(self):
        self.is_running = True
        await self.check_orders_loop()

    def stop(self):
        self.is_running = False

_monitor_instance: Monitor = None

def init_monitor(bot: Bot) -> Monitor:
    """初始化监控实例"""
    global _monitor_instance
    _monitor_instance = Monitor(bot)
    return _monitor_instance

async def stop_monitor():
    global _monitor_instance
    if _monitor_instance:
        _monitor_instance.stop()
        _monitor_instance = None
