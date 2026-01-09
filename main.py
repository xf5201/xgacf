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
from services.okpay import okpay_service  # ✅ 导入 okpay_service 单例
from core.config import config
from core.database import db
from web_app import app as web_app  # ✅ 确保你的文件名是 web_app.py

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main():
    # 1. 初始化 Bot
    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage())
    
    # 2. 初始化数据库
    await db.init()
    
    # 3. 注册路由
    # 只注册 User Router
    dp.include_router(router)
    
    # 4. 初始化后台服务
    monitor = init_monitor(bot)
    cleaner = init_cleaner(bot)
    
    # ✅ 关键步骤：将 bot 实例注入给 okpay_service
    # 这样 OkPay 回调发货成功后，才能调用 bot.send_message 通知用户
    okpay_service.bot = bot
    
    # 5. 启动后台任务
    # 注意：create_task 是 "即发即弃"，在简单脚本中可行
    # 如果需要更健壮的任务管理，可以考虑使用 TaskGroup 或 asyncio.gather
    asyncio.create_task(monitor.start())
    asyncio.create_task(cleaner.start())
    
    # 6. 启动 Web 服务器
    # OkPay 的回调接口 /okpay/notify 已经在 web_app 中定义好了
    runner = web.AppRunner(web_app)
    await runner.setup()
    
    # 监听 0.0.0.0:8080
    # 如果你使用 Ngrok，确保 Ngrok 转发到了 8080 端口
    site = web.TCPSite(runner, '0.0.0.0', 8080)
    await site.start()
    logger.info("🌍 Web 服务器已启动 (端口 8080)")

    logger.info("🚀 Bot 已启动，开始轮询...")
    try:
        await dp.start_polling(bot)
    finally:
        logger.info("🛑 Bot 停止轮询")
        # 清理资源
        await runner.cleanup()
        if hd_manager.client:
            await hd_manager.client.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 手动停止程序")
