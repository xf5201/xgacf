import logging
import hashlib
import asyncio
import os

# ✅ 屏蔽 pytoniq 库的详细日志 (防止刷屏)
logging.getLogger("LiteClient").setLevel(logging.WARNING)
logging.getLogger("pytoniq").setLevel(logging.WARNING)

try:
    from bip_utils import Bip39SeedGenerator, Bip44, Bip44Coins, Bip44Changes
except ImportError:
    print("错误: 缺少 bip_utils 库，请在终端运行: pip install bip-utils")
    exit(1)

from pytoniq import LiteClient, Address
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
        
        # 核心资源：持有 TON 网络连接
        self.client = None
        # 全局锁：保护所有链上操作
        self.lock = asyncio.Lock() 
        # 缓存：避免重复获取合约代码
        self._jetton_wallet_code_cache = None
        
        self._init_keys()

    def _init_keys(self):
        password = " ".join(self.ton_mnemonics).encode("utf-8")
        salt = "TON default seed".encode("utf-8")
        kdf = hashlib.pbkdf2_hmac("sha512", password, salt, 100000, dklen=64)
        private_key_bytes = kdf[:32]
        self.ton_base_priv_key_hex = private_key_bytes.hex()

        priv_key_obj = ed25519.Ed25519PrivateKey.from_private_bytes(private_key_bytes)
        self.ton_base_pub_key_bytes = priv_key_obj.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw
        )

    async def ensure_connected(self):
        """
        确保连接有效 (静默连接，仅在失败时报错)
        """
        # 1. 如果 client 存在，直接返回
        if self.client:
            return self.client

        # 2. 如果 client 不存在，开始连接流程（静默）
        proxy = os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY") or None

        try:
            if config.TESTNET:
                self.client = LiteClient.from_testnet_config(ls_i=0, trust_level=1)
            else:
                self.client = LiteClient.from_mainnet_config(ls_i=0, trust_level=2)

            if proxy:
                self.client.set_proxy(proxy)

            await self.client.connect()
            
        except Exception as e:
            # 保留错误日志，只在连接失败时提醒
            logger.error(f"❌ TON 网络连接异常: {e}")
            self.client = None
            raise ConnectionError(f"无法连接 TON 网络: {e}")
        
        return self.client

    async def _get_wallet_obj(self, index: int):
        safe_index = index % (2 ** 32)
        return await WalletV4R2.from_data(
            provider=self.client,
            public_key=self.ton_base_pub_key_bytes,
            wallet_id=safe_index,
            wc=0,
        )

    async def _load_jetton_wallet_code_once(self, master_address: Address) -> Cell:
        """从 Jetton Master 合约读取 jetton_wallet_code（只读一次，后续复用）"""
        if self._jetton_wallet_code_cache is not None:
            return self._jetton_wallet_code_cache

        if not self.client:
            raise RuntimeError("LiteClient 未连接")

        logger.info("📦 首次使用，正在从 USDT Master 合约读取 jetton_wallet_code ...")
        result = await self.client.run_get_method(
            address=master_address,
            method="get_jetton_data",
            stack=[],
        )

        if not result or len(result) < 4:
            raise ValueError("get_jetton_data 返回结果长度不足，无法获取 jetton_wallet_code")

        code_cell = result[3]
        if not isinstance(code_cell, Cell):
            raise ValueError("jetton_wallet_code 不是 Cell 类型")

        self._jetton_wallet_code_cache = code_cell
        logger.info("✅ jetton_wallet_code 已缓存，之后将直接使用本地计算")
        return code_cell

    def _compute_jetton_wallet_address_locally(
        self, owner_address: Address, master_address: Address, wallet_code: Cell
    ) -> Address:
        """
        严格按照 TEP-74 标准和 JettonWallet.fc 的 init_data 构造
        data = (coins(0), status(0), owner_address, master_address, wallet_code(ref))
        """
        data_cell = (
            begin_cell()
            .store_coins(0)  # 1. 初始余额: 0 nanotons (var_uint)
            .store_uint(0, 1)  # 2. 状态: 0 (未激活)
            .store_address(owner_address)  # 3. 所有者
            .store_address(master_address)  # 4. 代币主合约
            .store_ref(wallet_code)  # 5. 钱包代码 (放在 ref 里)
            .end_cell()
        )

        state_init = StateInit(code=wallet_code, data=data_cell)
        jetton_wallet_address = state_init.address
        return jetton_wallet_address

    def _get_safe_usdt_master_address(self) -> Address:
        """获取 USDT Master 地址，增加兜底逻辑"""
        addr_str = config.TON_USDT_MASTER
        if not addr_str or addr_str == "":
            logger.debug("Config 中 TON_USDT_MASTER 为空，已自动回退到默认主网地址")
            addr_str = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"

        try:
            return Address(addr_str)
        except Exception as e:
            logger.debug(f"解析 Config 地址失败 ({e})，使用默认地址")
            return Address("EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs")

    async def generate_usdt_jetton_wallet(self, index: int):
        """
        生成 USDT Jetton 钱包
        逻辑：链上查询 -> 失败则本地计算
        """
        async with self.lock:
            client = await self.ensure_connected()
            
            try:
                wallet = await self._get_wallet_obj(index)
                USDT_MASTER = self._get_safe_usdt_master_address()

                owner_cell = begin_cell().store_address(wallet.address).end_cell()
                jetton_wallet_address = None
                method_worked = False

                # 1. 尝试链上查询
                try:
                    result = await client.run_get_method(
                        address=USDT_MASTER,
                        method="get_wallet_address",
                        stack=[owner_cell.begin_parse()],
                    )

                    if result and len(result) > 0:
                        val = result[0]
                        if isinstance(val, Slice):
                            jetton_wallet_address = val.load_address()
                        else:
                            jetton_wallet_address = val

                        method_worked = True
                        logger.info(f"✅ 链上获取地址成功")
                except Exception as e:
                    logger.warning(f"⚠️ 链上方法调用失败 ({e})，切换为本地计算以保证服务可用性")

                # 2. 失败回退：本地计算
                if not jetton_wallet_address:
                    try:
                        wallet_code = await self._load_jetton_wallet_code_once(USDT_MASTER)
                        jetton_wallet_address = self._compute_jetton_wallet_address_locally(
                            wallet.address, USDT_MASTER, wallet_code
                        )
                        logger.warning(f"⚠️ 使用本地兜底生成地址 (算法正确，建议小额测试)")
                    except Exception as e:
                        logger.error(f"❌ 链上和本地计算均失败: {e}")
                        raise

                return {
                    "jetton_addr": jetton_wallet_address.to_str(is_user_friendly=True, is_bounceable=True),
                    "main_addr": wallet.address.to_str(is_user_friendly=True, is_bounceable=True),
                }
            
            except Exception as e:
                logger.error(f"❌ 生成 USDT-Jetton 钱包失败: {e}")
                raise

    async def collect_jettons(self, from_index: int, amount_usdt: float):
        """
        从指定子钱包归集 USDT 到母钱包
        """
        async with self.lock:
            client = await self.ensure_connected()

            # 1. 确定归集目标地址 (母钱包)
            if config.COLLECTION_ADDRESS:
                to_address = Address(config.COLLECTION_ADDRESS)
                logger.info(f"🎯 归集目标: 配置文件指定地址")
            else:
                master_wallet = await self._get_wallet_obj(0)
                to_address = master_wallet.address
                logger.info(f"🎯 归集目标: 助记词生成的母钱包")

            # 2. 获取发送方钱包对象
            from_wallet = await self._get_wallet_obj(from_index)
            USDT_MASTER = self._get_safe_usdt_master_address()
            owner_cell = begin_cell().store_address(from_wallet.address).end_cell()

            try:
                result = await client.run_get_method(
                    address=USDT_MASTER,
                    method="get_wallet_address",
                    stack=[owner_cell.begin_parse()],
                )
            except Exception as e:
                logger.error(f"❌ 获取 Jetton 钱包地址失败: {e}")
                return False

            if not result or len(result) == 0:
                logger.error("❌ 无法获取 Jetton 钱包地址")
                return False

            jetton_wallet_address = result[0]
            if isinstance(jetton_wallet_address, Slice):
                jetton_wallet_address = jetton_wallet_address.load_address()

            # 4. 构建 Jetton Transfer Payload (TEP-74 标准)
            amount_nano = int(amount_usdt * 1_000_000)  # 6 位小数

            transfer_payload = (
                begin_cell()
                .store_uint(0xf8a7ea5, 32)  # op: Transfer
                .store_uint(0, 64)  # query_id
                .store_coins(amount_nano)  # amount (jettons)
                .store_address(to_address)  # destination (母钱包)
                .store_address(from_wallet.address)  # response_destination
                .store_dict(None)  # custom_payload
                .store_coins(10000000)  # forward_ton_amount (0.01 TON)
                .store_bit_bool(False)  # forward_payload is slice? No
                .end_cell()
            )

            # 5. 创建并发送交易
            gas_amount = int(0.06 * 1_000_000_000)
            seqno = await from_wallet.seqno()

            try:
                balance_info = await client.get_address_balance(from_wallet.address)
                if balance_info < gas_amount:
                    logger.error(
                        f"❌ 子钱包 {from_index} 余额不足，需: {gas_amount / 1e9} TON，当前: {balance_info / 1e9} TON"
                    )
                    return False
            except Exception as e:
                logger.warning(f"⚠️ 无法检查子钱包 TON 余额，尝试发送: {e}")

            transfer_msg = from_wallet.create_transfer_message(
                dest=jetton_wallet_address,
                amount=gas_amount,
                seqno=seqno,
                payload=transfer_payload,
            )

            try:
                await client.send_message(transfer_msg)
                logger.info(f"✅ 归集交易已发送! Hash: {transfer_msg.hash().hex()}")
                return True
            except Exception as e:
                logger.error(f"❌ 归集交易发送失败: {e}")
                return False

    def generate_trc20_wallet(self, index: int):
        try:
            seed_bytes = Bip39SeedGenerator(self.tron_mnemonic).Generate()
            bip44_mst_ctx = Bip44.FromSeed(seed_bytes, Bip44Coins.TRON)
            bip44_acc_ctx = bip44_mst_ctx.Purpose().Coin().Account(0)
            bip44_chg_ctx = bip44_acc_ctx.Change(Bip44Changes.CHAIN_EXT)
            safe_index = index % (2 ** 32)
            bip44_addr_ctx = bip44_chg_ctx.AddressIndex(safe_index)
            return bip44_addr_ctx.PublicKey().ToAddress()
        except Exception as e:
            logger.error(f"❌ 生成 TRON 地址失败: {e}")
            raise

    def get_master_ton_key(self):
        return self.ton_base_priv_key_hex

hd_manager = HDWalletManager()
