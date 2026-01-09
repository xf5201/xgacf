# web_app.py
import logging
from aiohttp import web
import json

from services.okpay import okpay_service
from core.config import config

logger = logging.getLogger(__name__)

async def handle_okpay_notify(request: web.Request):
    """
    处理 OkPay 支付回调
    文档说明：数据格式 JSON，无 sign 字段
    """
    try:
        method = request.method
        client_ip = request.remote
        user_agent = request.headers.get('User-Agent', '')

        logger.info(f"📥 收到 {method} 请求 | IP: {client_ip} | UA: {user_agent}")
        logger.info(f"📥 完整URL: {request.url}")
        logger.info(f"📥 查询参数: {dict(request.query)}")
        logger.info(f"🔧 当前 SERVER_DOMAIN: {config.SERVER_DOMAIN}")
        logger.info(f"🔧 当前 TESTNET: {config.TESTNET}")

        # 处理 GET 请求（浏览器跳转）
        if method == "GET":
            logger.info("🌐 浏览器访问支付成功页面")

            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>Payment Successful</title>
                <meta charset="utf-8">
                <style>
                    body { 
                        font-family: Arial, sans-serif; 
                        text-align: center; 
                        padding: 50px; 
                        background: #f5f5f5;
                    }
                    .container {
                        background: white;
                        padding: 40px;
                        border-radius: 10px;
                        box-shadow: 0 2px 10px rgba(0,0,0,0.1);
                        max-width: 500px;
                        margin: 0 auto;
                    }
                    .success { 
                        color: #4CAF50; 
                        font-size: 48px; 
                        margin-bottom: 20px;
                    }
                    h1 {
                        color: #333;
                        margin-bottom: 20px;
                    }
                    p {
                        color: #666;
                        line-height: 1.6;
                        margin-bottom: 10px;
                    }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="success">✅</div>
                    <h1>Payment Successful!</h1>
                    <p>Thank you for your payment.</p>
                    <p>Your Telegram Premium is being activated.</p>
                    <p>You can close this window and return to Telegram.</p>
                </div>
            </body>
            </html>
            """
            return web.Response(text=html, content_type='text/html')

        # 处理 POST 请求（OkPay 服务器回调）
        elif method == "POST":
            logger.info("🔄 OkPay 服务器回调 (疑似)")

            # 1. 先读原始 body 长度，方便 debug
            raw_body = await request.read()
            logger.info(f"📦 原始请求体长度: {len(raw_body)} bytes")
            if len(raw_body) > 0:
                try:
                    logger.info(f"📦 原始内容: {raw_body.decode('utf-8')}")
                except:
                    pass

            # 2. 按文档，回调是 JSON 格式
            try:
                data = await request.json()
                logger.info(f"📥 解析为 JSON: {data}")
            except:
                logger.error("❌ 解析 JSON 失败，不符合 OkPay 文档格式")
                return web.json_response({
                    "status": "error",
                    "message": "Invalid JSON"
                }, status=400)

            # 3. 基本校验：必须包含 data 字段，且 data 里至少有 order_id
            if not isinstance(data, dict):
                logger.error("❌ 回调根对象不是 dict")
                return web.json_response({
                    "status": "error",
                    "message": "Invalid format"
                }, status=400)

            callback_data = data.get("data")
            if not isinstance(callback_data, dict):
                logger.error("❌ 回调中缺少 data 字段或 data 不是对象")
                return web.json_response({
                    "status": "error",
                    "message": "Missing data"
                }, status=400)

            order_id = callback_data.get("order_id")
            unique_id = callback_data.get("unique_id")
            status = callback_data.get("status")
            pay_type = callback_data.get("type")  # deposit / withdraw

            logger.info(
                f"📌 OkPay 回调字段: order_id={order_id} unique_id={unique_id} "
                f"status={status} type={pay_type}"
            )

            # 4. 只处理「充值」且「已支付」的回调
            if pay_type != "deposit":
                logger.info(f"ℹ️ 非充值类型回调 type={pay_type}，忽略")
                return web.json_response({
                    "status": "ok",
                    "message": "Ignored non-deposit callback"
                })

            if status != 1:
                logger.info(f"ℹ️ 订单状态不是已支付 status={status}，忽略")
                return web.json_response({
                    "status": "ok",
                    "message": "Payment not completed yet"
                })

            # 5. 调用 okpay_service 处理支付成功逻辑（更新 DB + 发货）
            success = await okpay_service.handle_notification(callback_data)
            if success:
                logger.info("✅ 回调处理成功")
                return web.json_response({
                    "status": "success",
                    "message": "Notification processed"
                })
            else:
                logger.error("❌ 回调处理失败")
                return web.json_response({
                    "status": "error",
                    "message": "Failed to process notification"
                }, status=400)

        else:
            return web.json_response({
                "status": "error",
                "message": f"Method {method} not allowed"
            }, status=405)

    except Exception as e:
        logger.error(f"🚨 处理回调异常: {e}", exc_info=True)
        return web.json_response({
            "status": "error",
            "message": "Internal server error"
        }, status=500)


async def health_check(request: web.Request):
    return web.json_response({
        "status": "ok",
        "service": "premium_bot_web",
        "testnet": config.TESTNET,
        "server_domain": config.SERVER_DOMAIN
    })


app = web.Application()
app.router.add_get("/okpay/notify", handle_okpay_notify)
app.router.add_post("/okpay/notify", handle_okpay_notify)
app.router.add_get("/health", health_check)
