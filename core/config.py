import os
import logging
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

class Config:
    def __init__(self):
        self.TESTNET = os.getenv("TESTNET", "false").lower() == "true"
        
        # Bot 配置
        self.BOT_TOKEN = os.getenv("BOT_TOKEN")
        self.ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

        # TON 钱包配置
        self.TON_MNEMONIC = os.getenv("TON_MNEMONIC")
        self.TON_API_KEY = os.getenv("TONCENTER_API_KEY")
        
        # TRON 钱包配置
        self.TRON_MNEMONIC = os.getenv("TRON_MNEMONIC")
        self.TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY")
        # TRC20 USDT 合约地址 (主网)
        self.TRC20_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t"
        
        # 网络配置
        self.TRON_NETWORK = os.getenv("TRON_NETWORK", "mainnet") # mainnet or nile
        
        # TON 节点配置 (默认主网节点)
        if self.TESTNET:
            self.TON_LINK = "https://testnet.toncenter.com/api/v2/jsonRPC"
        else:
            self.TON_LINK = "https://toncenter.com/api/v2/jsonRPC"

        # API 配置
        self.CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 15))
        self.TON_PRICE_USDT = float(os.getenv("TON_PRICE_USDT", 6.5))
        self.PRICE_MARKUP = float(os.getenv("PRICE_MARKUP", 1.18))

        # 兜底价格
        self.PRICE_3_MONTHS_USDT = float(os.getenv("PRICE_3_MONTHS_USDT", 28.0))
        self.PRICE_6_MONTHS_USDT = float(os.getenv("PRICE_6_MONTHS_USDT", 52.0))
        self.PRICE_12_MONTHS_USDT = float(os.getenv("PRICE_12_MONTHS_USDT", 96.0))

        # Fragment 配置
        self.FRAGMENT_CONTRACT = os.getenv("FRAGMENT_CONTRACT")
        self.FRAGMENT_COOKIE = os.getenv("FRAGMENT_COOKIE")
        # 新增 FRAGMENT_HASH 配置
        self.FRAGMENT_HASH = os.getenv("FRAGMENT_HASH", "")

        # 订单配置
        self.ORDER_TIMEOUT_MINUTES = int(os.getenv("ORDER_TIMEOUT_MINUTES", 30))

        # 归集配置
        self.COLLECTION_THRESHOLD = float(os.getenv("COLLECTION_THRESHOLD", 50.0))
        # 现有的归集主钱包
        TON_MNEMONIC = os.getenv("TON_MNEMONIC", "")
        # 支付钱包
        PAYMENT_MNEMONIC = os.getenv("PAYMENT_MNEMONIC", "")


        # ==================== OkPay 配置 ====================
        # ✅ 统一使用 OKPAY_SECRET
        self.OKPAY_ID = os.getenv("OKPAY_ID")
        self.OKPAY_SECRET = os.getenv("OKPAY_SECRET")

        # 服务器部署
        self.SERVER_DOMAIN = os.getenv("SERVER_DOMAIN", "")
        # TON USDT 代币合约地址
        self.TON_USDT_MASTER = os.getenv("TON_USDT_MASTER", "EQCxE6vUtKJK-C2W1RZ7pNbqU_cxxYinb6K9vcfTlFsfhf3t")
        
config = Config()
