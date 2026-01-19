import logging
import asyncio
from aiogram import Router, F
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest

from core.database import db
from services.hd_wallet import hd_manager
from services.okpay import okpay_service
from services.fragment import fragment_service
from core.config import config

logger = logging.getLogger(__name__)

router = Router()

# --- Keyboard Helpers ---

async def get_main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Buy for Myself", callback_data="buy_self")],
        [InlineKeyboardButton(text="Buy for Others", callback_data="buy_other")],
        [InlineKeyboardButton(text="Help", callback_data="help")]
    ])

async def get_months_keyboard(username: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="3 Months (approx 30 USDT)", callback_data=f"select_months:{username}:3")],
        [InlineKeyboardButton(text="6 Months (approx 50 USDT)", callback_data=f"select_months:{username}:6")],
        [InlineKeyboardButton(text="12 Months (approx 90 USDT)", callback_data=f"select_months:{username}:12")],
        [InlineKeyboardButton(text="Back to Menu", callback_data="back_to_menu")]
    ])

async def get_currency_keyboard(username: str, months: int, price: float):
    amount_ton = round(price / config.TON_PRICE_USDT, 2)
    
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="TON (USDT-Jetton)", callback_data=f"pay:{months}:{username}:ton:{amount_ton}:{price}")],
        [InlineKeyboardButton(text="TRC20 (USDT)", callback_data=f"pay:{months}:{username}:trc20:{price}")],
        [InlineKeyboardButton(text="OkPay", callback_data=f"pay:{months}:{username}:okpay:{price}")],
        [InlineKeyboardButton(text="Reselect Months", callback_data=f"reselect:{username}")]
    ])

async def get_payment_keyboard(order_id: str, payment_method: str = "ton", payment_url: str = None):
    buttons = []
    if payment_method == "okpay" and payment_url:
        buttons.append([InlineKeyboardButton(text="Go to OkPay", url=payment_url)])

    # "I have paid" button for all methods
    buttons.append([InlineKeyboardButton(text="I have paid", callback_data=f"check:{order_id}")])

    # Common buttons
    buttons.append([InlineKeyboardButton(text="Cancel Order", callback_data=f"cancel:{order_id}")])
    buttons.append([InlineKeyboardButton(text="Back to Menu", callback_data="back_to_menu")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- Command Handlers ---

@router.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "Welcome to the Premium Bot!\n\n"
        "Please select your purchase method:",
        reply_markup=await get_main_keyboard()
    )

# --- Callback Handlers ---

@router.callback_query(F.data == "buy_self")
async def process_buy_self(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        pass
    
    user = callback.from_user
    if not user.username:
        await callback.message.edit_text("❌ Your account has no username set. Please set one in Telegram Settings.")
        return
    
    username = user.username
    
    try:
        await callback.message.edit_text(
            f"Buying Premium for **@{username}**.\n\n"
            "Please select duration:",
            parse_mode="Markdown",
            reply_markup=await get_months_keyboard(username)
        )
    except TelegramBadRequest as e:
        logger.error(f"Edit message error: {e}")

@router.callback_query(F.data == "buy_other")
async def process_buy_other(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        pass
    
    try:
        await callback.message.edit_text(
            "Please enter the Telegram username (without @) to recharge:",
            reply_markup=None
        )
    except TelegramBadRequest:
        pass

@router.message(F.text)
async def handle_manual_username_input(message: Message):
    text = message.text.strip().lstrip('@')
    
    if text.startswith('/'):
        return

    if not text:
        return

    username = text
    
    await message.answer(
        f"Buying Premium for **@{username}**.\n\n"
        "Please select duration:",
        parse_mode="Markdown",
        reply_markup=await get_months_keyboard(username)
    )

@router.callback_query(F.data.startswith("select_months:"))
async def process_select_months(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        pass
    
    try:
        _, username, months_str = callback.data.split(":")
        months = int(months_str)
        
        # Get price from Fragment API or use fallback
        try:
            price_usdt, _ = await fragment_service.get_realtime_price(username, months)
            if price_usdt == 0.0:
                raise Exception("Realtime price failed")
        except Exception:
            if months == 3: price_usdt = config.PRICE_3_MONTHS_USDT
            elif months == 6: price_usdt = config.PRICE_6_MONTHS_USDT
            elif months == 12: price_usdt = config.PRICE_12_MONTHS_USDT
            else: price_usdt = 28.0 # Fallback default
            logger.warning(f"Using fallback price for {months}M: {price_usdt} USDT")

        text = (
            f"**Order Confirmation**\n\n"
            f"User: @{username}\n"
            f"Duration: {months} months\n"
            f"Price: **{price_usdt} USDT**\n\n"
            f"Please select payment method:"
        )
        
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=await get_currency_keyboard(username, months, price_usdt))
    except Exception as e:
        logger.error(f"Select months error: {e}")

@router.callback_query(F.data.startswith("reselect:"))
async def reselect_months(callback: CallbackQuery):
    username = callback.data.split(":")[1]
    try:
        await callback.message.edit_text(
            f"Buying for **@{username}**.\n\n"
            "Please select duration:",
            parse_mode="Markdown",
            reply_markup=await get_months_keyboard(username)
        )
    except Exception:
        pass

@router.callback_query(F.data.startswith("pay:"))
async def process_currency(callback: CallbackQuery):
    try:
        await callback.answer("Generating payment address...")
    except Exception:
        pass

    try:
        data_parts = callback.data.split(":")
        months = int(data_parts[1])
        username = data_parts[2]
        currency = data_parts[3]
        
        amount_ton = 0.0
        price_usdt = 0.0
        
        if currency == "ton":
            amount_ton = float(data_parts[4])
            price_usdt = float(data_parts[5])
        else:
            price_usdt = float(data_parts[4])

        user_id = callback.from_user.id
        order_id = f"ORD{int(asyncio.get_event_loop().time())}{user_id}"
        
        import hashlib
        # Simple hash to derive wallet index
        wallet_index = abs(hash(f"{user_id}_{username}_{months}")) % 1000000

        # 1. Create order
        await db.create_order({
            "order_id": order_id,
            "user_id": user_id,
            "username": callback.from_user.username,
            "target": username,
            "months": months,
            "amount_ton": amount_ton,
            "amount_usdt": price_usdt,
            "price_ton": config.TON_PRICE_USDT,
            "wallet_index": wallet_index,
            "payment_method": currency,
            "status": "pending"
        })

        ton_addr = ""
        trc20_addr = ""
        okpay_url = ""

        if currency == "ton":
            try:
                wallet_info = await hd_manager.generate_usdt_jetton_wallet(wallet_index)
                ton_addr = wallet_info["jetton_addr"]
            except Exception as e:
                logger.error(f"TON wallet generation failed: {e}")
                await callback.message.edit_text("TON wallet generation failed. Please try again or contact support.")
                return
            
            text = (
                f"**Order Created**\n\n"
                f"ID: `{order_id}`\n"
                f"Target: @{username} ({months} months)\n"
                f"Amount: **{price_usdt} USDT**\n"
                f"Rate: {amount_ton} TON\n\n"
                f"**Please transfer USDT-Jetton to:**\n"
                f"`{ton_addr}`"
            )

        elif currency == "trc20":
            try:
                trc20_addr = hd_manager.generate_trc20_wallet(wallet_index)
            except Exception as e:
                logger.error(f"TRON wallet generation failed: {e}")
                await callback.message.edit_text("TRON wallet generation failed. Please try again.")
                return

            text = (
                f"**Order Created**\n\n"
                f"ID: `{order_id}`\n"
                f"Target: @{username} ({months} months)\n"
                f"Amount: **{price_usdt} USDT** (TRC20)\n\n"
                f"**Please transfer USDT-TRC20 to:**\n"
                f"`{trc20_addr}`"
            )

        elif currency == "okpay":
            try:
                okpay_url = await okpay_service.create_order({
                    "order_id": order_id,
                    "amount_usdt": price_usdt,
                    "months": months
                })
            except Exception as e:
                logger.error(f"OkPay order creation failed: {e}")
                await callback.message.edit_text("Payment gateway failed. Please try again.")
                return

            text = (
                f"**Order Created**\n\n"
                f"ID: `{order_id}`\n"
                f"Target: @{username} ({months} months)\n"
                f"Amount: **{price_usdt} USDT**\n\n"
                f"Click the button below to complete payment."
            )
        
        await db.update_order_wallet(order_id, ton_addr=ton_addr, trc20_addr=trc20_addr, okpay_url=okpay_url)
        
        # 3. Show payment confirmation
        kb = await get_payment_keyboard(order_id, currency, okpay_url)
        try:
            await callback.message.edit_text(text, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=kb)
        except Exception as e:
            logger.error(f"Pay message edit error: {e}")

    except Exception as e:
        logger.error(f"Process currency error: {e}", exc_info=True)
        try:
            await callback.message.edit_text("❌ Order processing error.")
        except Exception:
            pass

@router.callback_query(F.data.startswith("check:"))
async def check_payment_status(callback: CallbackQuery):
    order_id = callback.data.split(":")[1]
    
    try:
        order = await db.get_order(order_id)
        if not order:
            await callback.answer("Order not found.")
            return

        payment_method = order.get('payment_method')
        status = order.get('status')

        if payment_method == 'okpay':
            if status == 'pending':
                 await callback.answer("Notification received, checking payment status...")
            elif status == 'checking':
                 await callback.answer("Payment is being monitored by the system.")
            elif status == 'completed':
                 await callback.answer("Payment already completed.")
            else:
                 await callback.answer("Order status: " + status)
        else:
             await callback.answer("Checking order status...")
             # For crypto, monitor.py handles the actual check, this is just a user feedback

    except Exception as e:
        logger.error(f"Check payment status error: {e}")
        await callback.answer("Error checking status.")

@router.callback_query(F.data.startswith("cancel:"))
async def cancel_order(callback: CallbackQuery):
    order_id = callback.data.split(":")[1]
    
    try:
        await db.delete_order(order_id)
        await callback.message.edit_text(f"Order `{order_id}` has been cancelled.")
    except Exception as e:
        logger.error(f"Cancel order error: {e}")
        await callback.message.edit_text(f"Error cancelling order `{order_id}`.")

@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    try:
        await callback.message.edit_text(
            "Please select your purchase method:",
            reply_markup=await get_main_keyboard()
        )
    except Exception:
        pass

@router.callback_query(F.data == "help")
async def show_help(callback: CallbackQuery):
    try:
        await callback.answer()
    except Exception:
        pass
    
    help_text = (
        "**Help Guide**\n\n"
        "1. **Buy for Myself**: The bot will use your current Telegram username as the target.\n"
        "2. **Buy for Others**: You will be prompted to enter the target's Telegram username.\n"
        "3. **Payment Methods**:\n"
        "   - **TON (USDT-Jetton)**: You will receive a unique wallet address for payment.\n"
        "   - **TRC20 (USDT)**: You will receive a unique wallet address for payment.\n"
        "   - **OkPay**: You will be redirected to the OkPay payment page.\n\n"
        "**Important**: For crypto payments, click 'I have paid' after sending the funds. The system will monitor the wallet."
    )
    
    try:
        await callback.message.edit_text(help_text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Back to Menu", callback_data="back_to_menu")]
        ]))
    except TelegramBadRequest:
        pass
