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
        data = {}
        for key in ["query", "months", "recipient", "id", "show_sender", "mode", "lv", "dh", "transaction"]:
            if key in payload and payload[key] is not None:
                data[key] = str(payload[key]) if isinstance(payload[key], (int, bool)) else payload[key]
        
        if "method" not in payload:
            logger.error("Request missing method parameter")
            return None
        data["method"] = payload["method"]

        for attempt in range(self.max_retries):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(self.api_url, data=data, headers=self._get_headers()) as response:
                        result = await response.json()
                        if response.status != 200:
                            logger.warning(f"Fragment API bad status: {response.status}")
                            await asyncio.sleep(1)
                            continue
                        if result.get("error"):
                            logger.error(f"Fragment API error: {result['error']}")
                            return None
                        return result
            except Exception as e:
                logger.error(f"Fragment API request exception: {e}")
                await asyncio.sleep(1)
        return None

    async def create_premium_order_and_get_price(self, username: str, months: int) -> Optional[Dict]:
        try:
            logger.info(f"Fetching price for @{username} {months}M Premium")
            
            search_result = await self._send_fragment_request({
                "query": username,
                "months": months,
                "method": "searchPremiumGiftRecipient"
            })
            
            if not search_result or "found" not in search_result:
                logger.error(f"User @{username} not found")
                return None
                
            recipient = search_result["found"]["recipient"]
            
            init_result = await self._send_fragment_request({
                "recipient": recipient,
                "months": months,
                "method": "initGiftPremiumRequest"
            })
            
            if not init_result:
                logger.error("Failed to create payment order")
                return None
                
            req_id = init_result["req_id"]
            
            order_result = await self._send_fragment_request({
                "id": req_id,
                "show_sender": 1,
                "transaction": "1",
                "method": "getGiftPremiumLink"
            })
            
            if not order_result or "transaction" not in order_result:
                logger.error("Failed to get order details")
                return None
            
            transaction = order_result["transaction"]
            messages = transaction.get("messages", [])
            
            if not messages:
                logger.error("No messages in transaction")
                return None

            message = messages[0]
            
            if not message.get("amount"):
                logger.error("No amount in order message")
                return None
            
            pay_amount = float(message["amount"]) / 1e9
            dest_address_str = message.get('address') or message.get('destination') or message.get('to')
            
            if not dest_address_str:
                logger.error(f"No destination address found: {message}")
                return None
                
            payload_data = message.get('payload', '')
            ref_id = self._extract_ref_from_payload(payload_data)
            
            if not ref_id:
                logger.error("Could not extract ref_id from payload")
                return None
                
            logger.info(f"Order parsed: Amount={pay_amount} TON, ref_id={ref_id}")
            
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
            logger.error(f"Order creation flow exception: {e}")
            return None

    def _extract_ref_from_payload(self, payload: str) -> Optional[str]:
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
            logger.error(f"Failed to parse payload: {e}")
        return None

    def _build_payload(self, months: int, ref_id: str) -> Cell:
        text = f"Telegram Premium for {months} months \n\nRef#{ref_id}"
        return begin_cell().store_uint(0, 32).store_string(text).end_cell()

    async def _get_payment_wallet(self, client: LiteClient) -> Optional[WalletV4R2]:
        try:
            payment_mnemonic = os.getenv("PAYMENT_MNEMONIC")
            if payment_mnemonic:
                mnemonic_list = payment_mnemonic.strip().split()
                wallet = await WalletV4R2.from_mnemonic(
                    provider=client,
                    mnemonics=mnemonic_list, 
                    wallet_id=0, 
                    wc=0
                )
                return wallet
            return await hd_manager._get_wallet_obj(0)
        except Exception as e:
            logger.error(f"Payment wallet init failed: {e}")
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

    async def execute_purchase(self, order: dict) -> Dict[str, Any]:
        target_user = order.get('target')
        months = order.get('months')
        
        if not target_user or not months:
            return {"success": False, "error": "Missing parameters"}

        logger.info(f"Starting purchase for @{target_user} {months}M Premium")
        order_info = await self.create_premium_order_and_get_price(target_user, months)
        if not order_info:
            return {"success": False, "error": "Failed to get order info from Fragment"}

        ref_id = order_info['ref_id']
        dest_address = order_info['dest_address']
        pay_amount_ton = order_info.get('amount')
        
        amount_nano = to_nano(float(pay_amount_ton), 'ton')
        payload = self._build_payload(months, ref_id)
        
        logger.info(f"Payment: {pay_amount_ton} TON -> {dest_address}")

        for attempt in range(self.max_retries):
            try:
                await hd_manager.ensure_connected()
                client = hd_manager.client
                if not client:
                    return {"success": False, "error": "Cannot connect to TON node"}

                wallet = await self._get_payment_wallet(client)
                if not wallet:
                    return {"success": False, "error": "Wallet initialization failed"}

                seqno = 0
                try:
                    seqno = await wallet.get_seqno()
                except LiteServerError as e:
                    if e.code == -256:
                        seqno = 0
                    else:
                        raise

                state_init_for_deploy = None
                if seqno == 0:
                    state_init_for_deploy = wallet.create_state_init()

                internal_msg = wallet.create_wallet_internal_message(
                    destination=Address(dest_address),
                    value=amount_nano,
                    body=payload,
                    state_init=state_init_for_deploy,
                    send_mode=3
                )

                await wallet.raw_transfer(
                    msgs=[internal_msg],
                    seqno_from_get_meth=False,
                    seqno=seqno
                )

                tx_hash = internal_msg.message.hash().hex()
                logger.info(f"Transaction sent: {tx_hash}")
                return {"success": True, "tx_hash": tx_hash}

            except LiteServerError as e:
                logger.warning(f"Network error: Code {e.code} - {e.message}")
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (attempt + 1)
                    await asyncio.sleep(wait_time)
                    continue
                else:
                    return {"success": False, "error": f"Network retry failed: {e.message}"}
            
            except Exception as e:
                logger.error(f"Purchase flow exception: {e}")
                return {"success": False, "error": str(e)}

        return {"success": False, "error": "Unknown error during purchase"}

fragment_service = FragmentService()
