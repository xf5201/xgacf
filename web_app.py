import logging
from aiohttp import web
import json
import os

from services.okpay import okpay_service
from core.config import config

logger = logging.getLogger(__name__)

async def handle_okpay_notify(request: web.Request):
    try:
        method = request.method
        client_ip = request.remote

        # IP Whitelist Check
        okpay_allowed_ips = config.OKPAY_ALLOWED_IPS
        if okpay_allowed_ips and client_ip not in okpay_allowed_ips:
            logger.error(f"Unauthorized IP callback: {client_ip}")
            return web.json_response({
                "status": "error",
                "message": "Unauthorized IP"
            }, status=403)

        logger.info(f"Notify request: {method} | IP: {client_ip} | URL: {request.url}")

        if method == "GET":
            # Payment success redirect page
            try:
                with open(os.path.join(os.path.dirname(__file__), "payment_success.html"), "r", encoding="utf-8") as f:
                    html = f.read()
            except FileNotFoundError:
                html = "<h1>Payment Success</h1>"
            
            return web.Response(text=html, content_type='text/html')

        elif method == "POST":
            logger.info("OkPay server callback received")
            
            try:
                data = await request.json()
            except Exception:
                logger.error("Invalid JSON format from OkPay")
                return web.json_response({
                    "status": "error",
                    "message": "Invalid JSON"
                }, status=400)
            
            # Signature Verification
            if not okpay_service.verify_sign(data):
                logger.error("OkPay signature verification failed")
                return web.json_response({
                    "status": "error",
                    "message": "Invalid signature"
                }, status=403)

            callback_data = data.get("data")
            if not isinstance(callback_data, dict):
                logger.error("Missing 'data' field in callback")
                return web.json_response({
                    "status": "error",
                    "message": "Missing data"
                }, status=400)

            status = callback_data.get("status")
            pay_type = callback_data.get("type")

            if pay_type != "deposit":
                logger.info(f"Ignoring non-deposit callback: {pay_type}")
                return web.json_response({
                    "status": "success",
                    "message": "Ignored"
                })

            if status != 1:
                logger.info(f"Payment not completed: status={status}")
                return web.json_response({
                    "status": "success",
                    "message": "Pending"
                })

            success = await okpay_service.handle_notification(callback_data)
            if success:
                logger.info("Callback processed successfully")
                return web.json_response({
                    "status": "success",
                    "message": "OK"
                })
            else:
                logger.error("Failed to process notification")
                return web.json_response({
                    "status": "error",
                    "message": "Process failed"
                }, status=400)

        else:
            return web.json_response({
                "status": "error",
                "message": "Method not allowed"
            }, status=405)

    except Exception as e:
        logger.error(f"Callback error: {e}", exc_info=True)
        return web.json_response({
            "status": "error",
            "message": "Internal error"
        }, status=500)

async def health_check(request: web.Request):
    return web.json_response({
        "status": "ok",
        "testnet": config.TESTNET
    })

app = web.Application()
app.router.add_get("/okpay/notify", handle_okpay_notify)
app.router.add_post("/okpay/notify", handle_okpay_notify)
app.router.add_get("/health", health_check)
