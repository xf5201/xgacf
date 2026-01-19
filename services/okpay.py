import logging
import aiohttp
import hashlib
from urllib.parse import urlencode, unquote, quote
from typing import Dict, Any, Optional

from core.config import config
from core.database import db
from services.fragment import fragment_service
from aiogram import Bot

logger = logging.getLogger(__name__)

class OkPayService:
    # 实际 API URL 似乎是 okaypay.me，但为了兼容性，使用代码中的
    API_URL_PAYLINK = "https://api.okaypay.me/shop/payLink"

    def __init__(self, bot: Bot = None):
        self.bot = bot
        self.secret = config.OKPAY_SECRET 
        self.api_key = config.OKPAY_ID

    def _generate_sign(self, data: Dict[str, Any]) -> str:
        """生成 OkPay 签名 (兼容 okaypay.me 逻辑)"""
        data_with_id = data.copy()
        data_with_id['id'] = self.api_key
        data_with_id = {k: v for k, v in data_with_id.items() if v != '' and v is not None}
        sorted_data = dict(sorted(data_with_id.items()))
        query_string = urlencode(sorted_data, quote_via=quote)
        raw_string = unquote(query_string + '&token=' + self.secret)
        return hashlib.md5(raw_string.encode('utf-8')).hexdigest().upper()

    async def _get_notify_url(self) -> str:
        base_url = config.SERVER_DOMAIN.rstrip('/') if config.SERVER_DOMAIN else ""
        if not base_url:
            logger.warning("SERVER_DOMAIN not set in config!")
        return f"{base_url}/okpay/notify"

    async def create_order(self, order_data: Dict) -> Optional[str]:
        """创建 OkPay 订单并返回支付链接"""
        try:
            notify_url = await self._get_notify_url()
            payload = {
                "amount": str(order_data["amount_usdt"]),
                "coin": "USDT",
                "unique_id": order_data["order_id"],
                "name": f"Premium {order_data['months']}M",
                "callback_url": notify_url,
            }

            signed_data = self._generate_sign(payload)
            logger.info(f"Creating OkPay order: {order_data['order_id']}")

            async with aiohttp.ClientSession() as session:
                async with session.post(self.API_URL_PAYLINK, data=signed_data) as resp:
                    result = await resp.json()
                    if result.get("code") == 10000 or result.get("status") == "success":
                        payment_url = result.get("data", {}).get("pay_url")
                        return payment_url
                    else:
                        error_msg = result.get("msg") or result.get("message") or str(result)
                        logger.error(f"OkPay API error: {error_msg}")
                        return None
        except Exception as e:
            logger.error(f"OkPay request failed: {e}")
            return None

    def verify_sign(self, data: Dict) -> bool:
        """验证 OkPay 回调签名"""
        received_sign = data.get('sign')
        if not received_sign:
            logger.warning("Missing sign in callback data")
            return False

        data_to_check = data.copy()
        del data_to_check['sign']
        
        calculated_sign = self._generate_sign(data_to_check)

        if received_sign == calculated_sign:
            logger.info("OkPay signature verified")
            return True
        else:
            logger.error(f"OkPay signature mismatch. Received: {received_sign}, Calculated: {calculated_sign}")
            return False

    async def handle_notification(self, callback_data: dict) -> bool:
        """处理支付回调"""
        unique_id = callback_data.get("unique_id")
        status = callback_data.get("status")
        pay_type = callback_data.get("type")

        if not unique_id:
            return False

        # Only process successful deposit notifications
        if pay_type != "deposit" or status != 1:
            return True

        order = await db.get_order(unique_id)
        if not order:
            logger.warning(f"Order not found: {unique_id}")
            return True

        if order.get("status") == "completed":
            return True
        
        try:
            if order.get("status") != "paid":
                await db.update_order_status(unique_id, "paid")
                if self.bot:
                    try:
                        await self.bot.send_message(order.get("user_id"), "Payment received, processing fulfillment...")
                    except Exception: pass

            # Execute fulfillment
            if order.get("status") != "completed":
                logger.info(f"Executing purchase for @{order.get('target')}")
                result = await fragment_service.execute_purchase(order)
                
                if result.get("success"):
                    await db.update_order_status(unique_id, "completed")
                    if self.bot:
                        try:
                            await self.bot.send_message(order.get("user_id"), f"🎉 Fulfillment successful! Activated {order.get('months')} months Premium for @{order.get('target')}.")
                        except Exception: pass
                else:
                    error_msg = result.get("error", "Unknown error")
                    # Revert to pending/checking for retry
                    await db.update_order_status(unique_id, "checking")
                    logger.error(f"Purchase failed for {unique_id}: {error_msg}")

            return True
        except Exception as e:
            if unique_id:
                await db.update_order_status(unique_id, "checking")
            logger.error(f"Error handling notification: {e}")
            return True

okpay_service = OkPayService()
