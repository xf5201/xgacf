import asyncio
import logging
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from handlers.user import router
from services.monitor import init_monitor
from services.cleaner import init_cleaner
from services.hd_wallet import hd_manager
from services.okpay import okpay_service  
from core.config import config
from core.database import db
from web_app import app as web_app  

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
    # Init Bot and Dispatcher
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    
    # Init DB and Router
    await db.init()
    dp.include_router(router)
    
    # Init background services
    monitor = init_monitor(bot)
    cleaner = init_cleaner(bot)
    okpay_service.bot = bot # Inject bot instance into OkPay service for notifications
    
    # Start background tasks
    asyncio.create_task(monitor.start())
    asyncio.create_task(cleaner.start())
    
    # Start Web Server for OkPay callback
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    
    logger.info("Bot and Web Server started.")
    
    try:
        # Start Bot polling
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down...")
        await runner.cleanup()
        if hd_manager.client:
            await hd_manager.client.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Program stopped manually")
