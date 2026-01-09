import logging
import aiohttp
import asyncio
import base64
import os
from typing import Dict, Optional, Tuple, Any
from dotenv import load_dotenv

from pytoniq_core import begin_cell, Address, Cell
from tonsdk.utils import to_nano
from pytoniq import LiteClient, WalletV4R2, LiteServerError
from pytoniq_core.tlb.account import StateInit

from core.config import config
from services.hd_wallet import hd_manager 

load_dotenv()

logger = logging.getLogger(__name__)
FRAGMENT_API_BASE = "https://fragment.com"

class FragmentService:
    def __init__(self):
        self.cookie = config.FRAGMENT_COOKIE
        self.hash_param = config.FRAGMENT_HASH
        self.max_retries = 3
        self.retry_delay = 5
        self.api_url = f"{FRAGMENT_API_BASE}/api?hash={self.hash_param}" if self.hash_param else f"{FRAGMENT_API_BASE}/api"

    def _get_headers(self) -> Dict[str, str]:
        """获取请求头"""
        headers = {
            "accept": "application/json, text/javascript, */*; q=0.01",
            "accept-language": "zh-CN,zh;q=0.9",
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Cookie": self.cookie,
            "origin": "https://fragment.com",
            "referer": "https://fragment.com/premium/gift",
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
            "x-requested-with": "XMLHttpRequest"
        }
        return headers

    async def _send_fragment_request(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """发送请求到Fragment API"""
        data = {}
        for key in ["query", "months", "recipient", "id", "show_sender", "mode", "lv", "dh", "transaction"]:
            if key in payload and payload[key] is not None:
                data[key] = str(payload[key]) if isinstance(payload[key], (int, bool)) else payload[key]
        
        if "method" not in payload:
            logger.error("请求必须包含 method 参数")
            return None
        data["method"] = payload["method"]

        for attempt in range(self.max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(self.api_url, data=data, headers=self._get_headers()) as response:
                        result = await response.json()
                        if response.status != 200:
                            logger.warning(f"⚠️ Fragment API 响应状态异常: {response.status}")
                            await asyncio.sleep(1)
                            continue
                        if result.get("error"):
                            logger.error(f"Fragment API 返回错误: {result['error']}")
                            return None
                        return result
            except Exception as e:
                logger.error(f"❌ Fragment API 请求异常: {e}")
                await asyncio.sleep(1)
        return None

    async def create_premium_order_and_get_price(self, username: str, months: int) -> Optional[Dict]:
        """创建订单并获取价格"""
        try:
            logger.info(f"📡 开始为 @{username} 获取 {months}个月 Premium...")
            
            search_result = await self._send_fragment_request({
                "query": username,
                "months": months,
                "method": "searchPremiumGiftRecipient"
            })
            
            if not search_result or "found" not in search_result:
                logger.error(f"❌ 未找到用户 @{username}")
                return None
                
            recipient = search_result["found"]["recipient"]
            userName = search_result["found"].get("name", "未知")
            logger.info(f"✅ 找到用户: {userName} (标识: {recipient})")
            
            init_result = await self._send_fragment_request({
                "recipient": recipient,
                "months": months,
                "method": "initGiftPremiumRequest"
            })
            
            if not init_result:
                logger.error("❌ 创建支付订单失败")
                return None
                
            req_id = init_result["req_id"]
            amount = init_result["amount"]
            logger.info(f"✅ 订单创建成功: ID={req_id}, 金额={amount} TON")
            
            order_result = await self._send_fragment_request({
                "id": req_id,
                "show_sender": 1,
                "transaction": "1",
                "method": "getGiftPremiumLink"
            })
            
            if not order_result or "transaction" not in order_result:
                logger.error("❌ 获取订单详情失败")
                return None
            
            transaction = order_result["transaction"]
            messages = transaction.get("messages", [])
            
            if not messages:
                logger.error("❌ 交易中未找到 messages 数组")
                return None

            message = messages[0]
            
            if not message.get("amount"):
                logger.error("❌ 订单中未找到金额信息")
                return None
            
            pay_amount = float(message["amount"]) / 1e9
            dest_address_str = message.get('address') or message.get('destination') or message.get('to')
            
            if not dest_address_str:
                logger.error(f"❌ 订单中未找到收款地址: {message}")
                return None
                
            logger.info(f"🎯 成功获取动态收款地址: {dest_address_str}")

            payload_data = message.get('payload', '')
            ref_id = self._extract_ref_from_payload(payload_data)
            
            if not ref_id:
                logger.error("❌ 无法从payload中提取ref_id")
                return None
                
            logger.info(f"✅ 订单详情解析成功: 金额={pay_amount} TON, ref_id={ref_id}")
            
            return {
                "ref_id": ref_id,
                "amount": str(pay_amount),
                "dest_address": dest_address_str,
                "username": username,
                "months": months,
                "req_id": req_id,
                "recipient": recipient
            }
            
        except Exception as e:
            logger.error(f"❌ 创建订单流程异常: {e}")
            return None

    def _extract_ref_from_payload(self, payload: str) -> Optional[str]:
        """从payload中提取ref_id"""
        if not payload:
            return None
        try:
            def correct_padding(s):
                return s + '=' * ((4 - len(s) % 4) % 4)
            decoded_bytes = base64.b64decode(correct_padding(payload))
            decoded_str = decoded_bytes.decode('utf-8', errors='ignore')
            ref_index = decoded_str.find('#')
            if ref_index != -1:
                ref = decoded_str[ref_index + 1:].split()[0]
                return ref
        except Exception as e:
            logger.error(f"❌ 解析payload失败: {e}")
        return None

    def _build_payload(self, months: int, ref_id: str) -> Cell:
        """构建交易 payload"""
        text = f"Telegram Premium for {months} months \n\nRef#{ref_id}"
        return begin_cell().store_uint(0, 32).store_string(text).end_cell()

    async def _get_payment_wallet(self, client: LiteClient) -> Optional[WalletV4R2]:
        """获取支付钱包"""
        try:
            payment_mnemonic = os.getenv("PAYMENT_MNEMONIC")
            if payment_mnemonic:
                logger.info("💰 使用专用 PAYMENT_MNEMONIC 钱包...")
                mnemonic_list = payment_mnemonic.strip().split()
                wallet = await WalletV4R2.from_mnemonic(
                    provider=client,
                    mnemonics=mnemonic_list, 
                    wallet_id=0, 
                    wc=0
                )
                logger.info(f"💰 [支付钱包] 地址: {wallet.address.to_str()}")
                return wallet
            logger.warning("⚠️ 未配置 PAYMENT_MNEMONIC，回退默认钱包")
            return await hd_manager._get_wallet_obj(0)
        except Exception as e:
            logger.error(f"❌ 初始化支付钱包失败: {e}")
            return None

    async def get_realtime_price(self, username: str, months: int) -> Tuple[float, bool]:
        try:
            api_result = await self.create_premium_order_and_get_price(username, months)
            if api_result and api_result.get("amount"):
                ton_amount = float(api_result["amount"])
                ton_price_usdt = getattr(config, 'TON_PRICE_USDT', 5.0)
                usd_price = ton_amount * ton_price_usdt
                return usd_price, True
        except Exception:
            pass
        return 0.0, False

    async def _send_transaction_with_retry(self, client: LiteClient, query_cell: Cell) -> Tuple[bool, str]:
        """使用 client.raw_send_message 发送交易并重试"""
        for attempt in range(self.max_retries):
            try:
                await client.raw_send_message(query_cell)
                tx_hash = query_cell.hash().hex()
                logger.info(f"✅ 交易发送成功! 交易哈希: {tx_hash}")
                return True, tx_hash
            except LiteServerError as e:
                logger.warning(f"⚠️ 发送交易出错 ({e.code})，正在重试...")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)
                    continue
            except Exception as e:
                logger.error(f"❌ 发送交易异常: {e}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay)
                    continue
        return False, "交易发送失败"

    async def execute_purchase(self, order: dict) -> Tuple[bool, str]:
        """
        执行购买流程（统一走 wallet.raw_transfer 路径，不再手动拼外层消息）
        返回: (成功与否, 交易哈希或错误信息)
        """
        target_user = order.get('target')
        months = order.get('months')
        
        if not target_user or not months:
            return False, "参数错误"

        logger.info(f"🛒 开始为 @{target_user} 购买 {months}个月 Premium...")
        order_info = await self.create_premium_order_and_get_price(target_user, months)
        if not order_info:
            return False, "无法从 Fragment 获取订单信息"

        ref_id = order_info['ref_id']
        dest_address = order_info['dest_address']
        pay_amount_ton = order_info.get('amount')
        
        amount_nano = to_nano(float(pay_amount_ton), 'ton')
        payload = self._build_payload(months, ref_id)
        
        logger.info(f"💸 支付信息: {pay_amount_ton} TON -> {dest_address}")

        # 外层重试循环：处理网络不同步 (651) 等问题
        for attempt in range(self.max_retries):
            try:
                await hd_manager.ensure_connected()
                client = hd_manager.client
                if not client:
                    return False, "无法连接到 TON 节点"

                wallet = await self._get_payment_wallet(client)
                if not wallet:
                    return False, "钱包初始化失败"

                # 1) 确定是否需要部署（seqno=0）
                seqno = 0
                try:
                    seqno = await wallet.get_seqno()
                    logger.info(f"🔢 钱包序列号: {seqno}")
                except LiteServerError as e:
                    if e.code == -256:
                        logger.warning("⚠️ 钱包未初始化 (-256)，将使用 seqno=0 进行部署...")
                        seqno = 0
                    else:
                        # 其他网络错误（如 651），外层循环重试
                        raise

                # 2) 构建内部消息（发给 Fragment 的支付指令）
                # 如果 seqno == 0，表示这是部署交易，需要在内部消息上挂上 state_init
                state_init_for_deploy = None
                if seqno == 0:
                    state_init_for_deploy = wallet.create_state_init()
                    logger.info("📦 已生成 StateInit，将在内部消息中附带（部署钱包）")

                internal_msg = wallet.create_wallet_internal_message(
                    destination=Address(dest_address),
                    value=amount_nano,
                    body=payload,
                    state_init=state_init_for_deploy,
                    send_mode=3
                )

                # 3) 使用官方提供的 raw_transfer，传入我们确定的 seqno
                # 注意：seqno_from_get_meth=False，避免它再跑一次 get_seqno（部署时是 -256）
                await wallet.raw_transfer(
                    msgs=[internal_msg],
                    seqno_from_get_meth=False,
                    seqno=seqno
                )

                tx_hash = internal_msg.message.hash().hex()
                logger.info(f"✅ raw_transfer 发送成功，交易哈希: {tx_hash}")
                logger.info(f"🔗 查看交易: https://tonscan.org/tx/{tx_hash}")
                return True, tx_hash

            except LiteServerError as e:
                # 网络错误重试
                logger.warning(f"⚠️ 捕获到网络错误: Code {e.code} - {e.message}")
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (attempt + 1)
                    logger.info(f"🔄 等待 {wait_time} 秒后重试 ({attempt + 1}/{self.max_retries})...")
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    return False, f"网络错误重试次数耗尽: {e.message}"
            
            except Exception as e:
                logger.error(f"❌ 购买流程异常: {e}")
                logger.exception("详细堆栈:")
                return False, str(e)

        return False, "未知错误"

fragment_service = FragmentService()
