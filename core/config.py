import os
import logging
from dotenv import load_dotenv

load_dotenv()

class Config:
    def __init__(self):
        self.TESTNET = os.getenv("TESTNET", "false").lower() == "true"
        
        # Bot Config
        self.BOT_TOKEN = os.getenv("BOT_TOKEN")
        self.ADMIN_ID = int(os.getenv("ADMIN_ID", 0))

        # TON Config
        self.TON_MNEMONIC = os.getenv("TON_MNEMONIC")
        self.TON_API_KEY = os.getenv("TONCENTER_API_KEY")
        
        # TRON Config
        self.TRON_MNEMONIC = os.getenv("TRON_MNEMONIC")
        self.TRONGRID_API_KEY = os.getenv("TRONGRID_API_KEY")
        self.TRC20_USDT_CONTRACT = "TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t" # USDT TRC20 Mainnet
        self.TRON_NETWORK = os.getenv("TRON_NETWORK", "mainnet")
        
        # TON RPC Endpoint
        self.TON_LINK = "https://testnet.toncenter.com/api/v2/jsonRPC" if self.TESTNET else "https://toncenter.com/api/v2/jsonRPC"

        # Price & Order Config
        self.CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 15))
        self.TON_PRICE_USDT = float(os.getenv("TON_PRICE_USDT", 6.5))
        self.PRICE_MARKUP = float(os.getenv("PRICE_MARKUP", 1.18))

        self.PRICE_3_MONTHS_USDT = float(os.getenv("PRICE_3_MONTHS_USDT", 28.0))
        self.PRICE_6_MONTHS_USDT = float(os.getenv("PRICE_6_MONTHS_USDT", 52.0))
        self.PRICE_12_MONTHS_USDT = float(os.getenv("PRICE_12_MONTHS_USDT", 96.0))

        # Fragment Config
        self.FRAGMENT_COOKIE = os.getenv("FRAGMENT_COOKIE")
        self.FRAGMENT_HASH = os.getenv("FRAGMENT_HASH", "")

        self.ORDER_TIMEOUT_MINUTES = int(os.getenv("ORDER_TIMEOUT_MINUTES", 30))
        self.COLLECTION_THRESHOLD = float(os.getenv("COLLECTION_THRESHOLD", 50.0))

        # OkPay Config
        self.OKPAY_ID = os.getenv("OKPAY_ID")
        self.OKPAY_SECRET = os.getenv("OKPAY_SECRET")
        self.OKPAY_ALLOWED_IPS = [ip.strip() for ip in os.getenv("OKPAY_ALLOWED_IPS", "").split(',') if ip.strip()]

        # Server Config
        self.SERVER_DOMAIN = os.getenv("SERVER_DOMAIN", "")
        self.TON_USDT_MASTER = os.getenv("TON_USDT_MASTER", "EQCxE6vUtKJK-C2W1RZ7pNbqU_cxxYinb6K9vcfTlFsfhf3t")
        
config = Config()
