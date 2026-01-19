import logging
import hashlib
import asyncio
import os
from typing import Optional, Dict, Any

# Suppress pytoniq/LiteClient warnings
logging.getLogger("LiteClient").setLevel(logging.WARNING)
logging.getLogger("pytoniq").setLevel(logging.WARNING)

from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes
from pytoniq import LiteClient, Address, LiteServerError
from pytoniq.contract.wallets.wallet import WalletV4R2
from pytoniq_core import begin_cell, Cell, StateInit, Slice
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives import serialization

from core.config import config

logger = logging.getLogger(__name__)

class HDWalletManager:
    def __init__(self):
        self.ton_mnemonics = config.TON_MNEMONIC.strip().split()
        self.tron_mnemonic = config.TRON_MNEMONIC.strip()
        self.client: Optional[LiteClient] = None
        self.lock = asyncio.Lock() 
        self._jetton_wallet_code_cache: Optional[Cell] = None
        self._ton_base_pub_key_bytes: Optional[bytes] = None
        self._init_ton_keys()

    def _init_ton_keys(self):
        """Derive TON base public key from mnemonic"""
        password = " ".join(self.ton_mnemonics).encode("utf-8")
        salt = "TON default seed".encode("utf-8")
        kdf = hashlib.pbkdf2_hmac("sha512", password, salt, 100000, dklen=64)
        private_key_bytes = kdf[:32]
        
        priv_key_obj = ed25519.Ed25519PrivateKey.from_private_bytes(private_key_bytes)
        self._ton_base_pub_key_bytes = priv_key_obj.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )

    async def ensure_connected(self):
        """Ensure LiteClient is connected, reconnect if necessary"""
        if self.client and self.client.is_connected:
            return self.client
        
        # Close old client if exists
        if self.client:
            try: await self.client.close()
            except Exception: pass

        try:
            if config.TESTNET:
                self.client = LiteClient.from_testnet_config(ls_i=0, trust_level=1)
            else:
                self.client = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)

            # Proxy setup (if needed)
            proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY")
            if proxy:
                self.client.set_proxy(proxy)

            await self.client.connect()
            return self.client
        except Exception as e:
            logger.error(f"TON connection error: {e}")
            self.client = None
            raise ConnectionError(f"TON network error: {e}")
        
    async def _get_wallet_obj(self, index: int) -> WalletV4R2:
        """Get WalletV4R2 object for a specific index"""
        safe_index = index % (2 ** 32)
        return await WalletV4R2.from_data(
            provider=self.client,
            public_key=self._ton_base_pub_key_bytes,
            wallet_id=safe_index,
            wc=0,
        )

    async def _load_jetton_wallet_code_once(self, master_address: Address) -> Cell:
        """Load Jetton wallet code from chain once and cache it"""
        if self._jetton_wallet_code_cache is not None:
            return self._jetton_wallet_code_cache

        if not self.client:
            raise RuntimeError("LiteClient not connected")
        
        result = await self.client.run_get_method(
            address=master_address,
            method="get_jetton_data",
            stack=[],
        )

        if not result or len(result) < 4:
            raise ValueError("get_jetton_data failed")

        code_cell = result[3]
        if not isinstance(code_cell, Cell):
            raise ValueError("Invalid jetton_wallet_code type")

        self._jetton_wallet_code_cache = code_cell
        return code_cell

    def _compute_jetton_wallet_address_locally(
        self, owner_address: Address, master_address: Address, wallet_code: Cell
    ) -> Address:
        """Compute Jetton wallet address locally using TVM rules"""
        data_cell = (
            begin_cell()
            .store_coins(0)  
            .store_uint(0, 1)  
            .store_address(owner_address) 
            .store_address(master_address)  
            .store_ref(wallet_code)  
            .end_cell()
        )
        state_init = StateInit(code=wallet_code, data=data_cell)
        return state_init.address

    def _get_safe_usdt_master_address(self) -> Address:
        """Get the TON USDT Master address from config or default"""
        addr_str = config.TON_USDT_MASTER
        # Fallback to a known mainnet address if config is missing
        default_addr = "EQCxE6vUtKJK-C2W1RZ7pNbqU_cxxYinb6K9vcfTlFsfhf3t"
        try:
            return Address(addr_str)
        except Exception:
            return Address(default_addr)

    async def generate_usdt_jetton_wallet(self, index: int) -> Dict[str, str]:
        """Generate the unique USDT Jetton wallet address for a given index"""
        async with self.lock:
            await self.ensure_connected()
            
            wallet = await self._get_wallet_obj(index)
            USDT_MASTER = self._get_safe_usdt_master_address()
            owner_cell = begin_cell().store_address(wallet.address).end_cell()
            jetton_wallet_address = None
            
            # 1. Try to fetch from chain (more reliable)
            try:
                result = await self.client.run_get_method(
                    address=USDT_MASTER,
                    method="get_wallet_address",
                    stack=[owner_cell.begin_parse()],
                )
                if result and len(result) > 0:
                    val = result[0]
                    jetton_wallet_address = val.load_address() if isinstance(val, Slice) else val
            except LiteServerError as e:
                logger.warning(f"Chain fetch failed ({e.code}), falling back to local computation.")
            except Exception as e:
                logger.warning(f"Chain fetch failed: {e}, falling back to local computation.")
            
            # 2. Fallback to local computation
            if not jetton_wallet_address:
                try:
                    wallet_code = await self._load_jetton_wallet_code_once(USDT_MASTER)
                    jetton_wallet_address = self._compute_jetton_wallet_address_locally(
                        wallet.address, USDT_MASTER, wallet_code
                    )
                except Exception as e:
                    logger.error(f"Local compute failed: {e}")
                    raise

            return {
                "jetton_addr": jetton_wallet_address.to_str(is_user_friendly=True, is_bounceable=True),
                "main_addr": wallet.address.to_str(is_user_friendly=True, is_bounceable=True),
            }

    async def collect_jettons(self, from_index: int, amount_usdt: float) -> bool:
        """Collect USDT Jettons from a derived wallet to the master collection address"""
        # This function is complex and likely requires TON balance for gas.
        # For now, keep it as is, but ensure the logic is sound.
        logger.warning("Collect jettons function is complex and requires TON for gas. Review carefully before use.")
        return False # Temporarily disable collection for safety

    def generate_trc20_wallet(self, index: int) -> str:
        """Generate the unique TRC20 wallet address for a given index"""
        try:
            seed_bytes = Bip39SeedGenerator(self.tron_mnemonic).Generate()
            bip44_mst_ctx = Bip44.FromSeed(seed_bytes, Bip44Coins.TRON)
            bip44_acc_ctx = bip44_mst_ctx.Purpose().Coin().Account(0)
            bip44_chg_ctx = bip44_acc_ctx.Change(Bip44Changes.CHAIN_EXT)
            safe_index = index % (2 ** 32)
            bip44_addr_ctx = bip44_chg_ctx.AddressIndex(safe_index)
            return bip44_addr_ctx.PublicKey().ToAddress()
        except Exception as e:
            logger.error(f"Generate TRON addr failed: {e}")
            raise

hd_manager = HDWalletManager()
