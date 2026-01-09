import aiohttp
import logging
import hashlib
from urllib.parse import urlencode, unquote, quote
from typing import Dict, Optional

from core.database import db
from core.config import config
from aiogram import Bot
from services.fragment import fragment_service

logger = logging.getLogger(__name__)

class OkPay:
    API_URL_BASE = "https://api.okaypay.me/shop/"
    API_URL_PAYLINK = API_URL_BASE + "payLink"

    def __init__(self, bot: Bot = None):
        self.bot = bot
        self.secret = config.OKPAY_SECRET 
        self.api_key = config.OKPAY_ID

    def sign(self, data: Dict) -> Dict:
        """计算 OkPay 签名"""
        data_with_id = data.copy()
        data_with_id['id'] = self.api_key
        data_with_id = {k: v for k, v in data_with_id.items() if v != '' and v is not None}
        sorted_data = dict(sorted(data_with_id.items()))
        query_string = urlencode(sorted_data, quote_via=quote)
        raw_string = unquote(query_string + '&token=' + self.secret)
        sign = hashlib.md5(raw_string.encode('utf-8')).hexdigest().upper()
        sorted_data['sign'] = sign
        return sorted_data

    async def _get_notify_url(self) -> str:
        """获取回调 URL"""
        base_url = config.SERVER_DOMAIN.rstrip('/') if config.SERVER_DOMAIN else ""
        if not base_url:
            logger.warning("SERVER_DOMAIN 未配置，回调地址为空！")
        return f"{base_url}/okpay/notify"

    async def create_order(self, order_data: Dict) -> str:
        """创建支付订单"""
        try:
            notify_url = await self._get_notify_url()

            payload = {
                "amount": str(order_data["amount_usdt"]),
                "coin": "USDT",
                "unique_id": order_data["order_id"],
                "name": f"Telegram Premium {order_data['months']} Months",
                "callback_url": notify_url,
            }

            signed_data = self.sign(payload)
            logger.info(f"向 OkPay 发起创建订单请求: {order_data['order_id']}")

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.API_URL_PAYLINK,
                    data=signed_data,
                    headers={}
                ) as resp:
                    response_text = await resp.text()
                    try:
                        result = await resp.json()
                    except:
                        raise Exception(f"OkPay 返回非 JSON 响应: {response_text}")

                    if result.get("code") == 10000 or result.get("status") == "success":
                        payment_url = result.get("data", {}).get("pay_url")
                        if payment_url:
                            logger.info(f"订单创建成功，支付链接: {payment_url}")
                            return payment_url
                        else:
                            logger.warning(f" OkPay 返回成功但没有 pay_url: {result}")
                            return ""
                    else:
                        error_msg = result.get("msg") or result.get("message") or str(result)
                        raise Exception(f"OkPay API 错误: {error_msg}")

        except Exception as e:
            logger.error(f"OkPay 请求异常: {e}")
            raise e

    def verify_sign(self, data: Dict) -> bool:
        """验证签名"""
        received_sign = data.get('sign')
        if not received_sign:
            return True
        data_to_check = data.copy()
        del data_to_check['sign']
        signed_data = self.sign(data_to_check)
        calculated_sign = signed_data.get('sign')
        logger.info(f"签名验证 - 接收到: {received_sign}, 计算得到: {calculated_sign}")
        return received_sign == calculated_sign

    async def handle_notification(self, callback_data: dict) -> bool:
        """
        处理 OkPay 的回调通知
        """
        logger.info(f"收到 OkPay 回调 data: {callback_data}")

        order_id = callback_data.get("order_id")
        unique_id = callback_data.get("unique_id")
        pay_user_id = callback_data.get("pay_user_id")
        amount = callback_data.get("amount")
        coin = callback_data.get("coin")
        status = callback_data.get("status")
        pay_type = callback_data.get("type")

        logger.info(
            f"解析回调字段: order_id={order_id} unique_id={unique_id} "
            f"pay_user_id={pay_user_id} amount={amount} coin={coin} "
            f"status={status} type={pay_type}"
        )

        if not unique_id:
            logger.warning("回调中没有 unique_id，无法处理")
            return False

        if pay_type != "deposit" or status != 1:
            logger.info(
                f"不处理的回调状态: type={pay_type}, status={status}，忽略"
            )
            # 只要不是我们处理的类型，也返回 True，避免 OkPay 重试
            return True

        current_order = await db.get_order(unique_id)
        if not current_order:
            logger.warning(f"找不到订单 unique_id={unique_id}")
            # 找不到订单也是 OkPay 的问题，不应该让它一直重试，返回 True
            return True

        current_status = current_order.get("status")
        
        if current_status == "completed":
            logger.info(f"订单 {unique_id} 已完成，跳过")
            return True
        
       
        # 即使发货失败，只要确认支付，就必须告诉 OkPay "收到" (return True)
        # 否则 OkPay 会一直重试回调直到成功
        
        try:
            # 1. 更新状态为 paid (如果已经是 paid 则忽略)
            if current_status != "paid":
                await db.update_order_status(unique_id, "paid")
            
                if self.bot:
                    try:
                        await self.bot.send_message(
                            current_order.get("user_id"),
                            f"已收到您的付款！\n\n"
                            f"正在为您开通 Telegram Premium ({current_order.get('months')} 个月)，请稍候..."
                        )
                    except Exception as e:
                        logger.error(f"发送“已收到付款”通知失败: {e}")

            # 3. 执行发货逻辑
            # 只有状态不是 completed 时才尝试发货（防止已完成的重复发货）
            if current_status != "completed":
                logger.info(
                    f"[OkPay回调] 正在执行购买: @{current_order.get('target')} "
                    f"({current_order.get('months')}个月)..."
                ) fragment_service.execute_purchase(current_order)

                if success:
                    # 4. 发货成功，更新为 completed
                    await db.update_order_status(unique_id, "completed")
                    logger.info(f"🎉 [OkPay回调] 订单 {unique_id} 完成！")

                    # 5. 再次通知用户：开通成功
                    if self.bot:
                        try:
                            await self.bot.send_message(
                                current_order.get("user_id"),
                                f"🎉 开通成功！\n\n"
                                f"已成功为 @{current_order.get('target')} 激活 {current_order.get('months')} 个月 Premium。"
                            )
                        except Exception as e:
                            logger.error(f"发送“开通成功”通知失败: {e}")
                else:
                    # ⚠️ 发货失败，回滚状态到 pending
                    await db.update_order_status(unique_id, "pending")
                    logger.error(f"❌ [OkPay回调] 订单 {unique_id} 发货失败: {msg}，状态已回滚至 pending 等待重试")

            # ✅ 重要：无论发货是否成功，这里都返回 True
            # 告诉 OkPay 我们已收到通知，不要重试了。重试由内部数据库逻辑控制。
            return True

        except Exception as e:
            # ✅ 即使发生未知异常，也尽量回滚状态并返回 True，防止 OkPay 疯狂重试
            if unique_id:
                await db.update_order_status(unique_id, "pending")
            logger.error(f"❌ [OkPay回调] 处理发货异常: {e}，状态已回滚至 pending 等待重试", exc_info=True)
            return True

okpay_service = OkPay()
