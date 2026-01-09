import logging
import asyncio
from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery, Message, InlineKeyboardMarkup, InlineKeyboardButton, ForceReply
from aiogram.exceptions import TelegramBadRequest

from core.database import db
from services.hd_wallet import hd_manager
from services.okpay import okpay_service
from core.config import config

logger = logging.getLogger(__name__)

router = Router()

# --- 键盘构造辅助函数 ---

async def get_main_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 为自己购买", callback_data="buy_self")],
        [InlineKeyboardButton(text="👥 为他人购买", callback_data="buy_other")],
        [InlineKeyboardButton(text="❓ 帮助说明", callback_data="help")]
    ])
    return kb

async def get_months_keyboard(username: str):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="3 个月 (约 30 USDT)", callback_data=f"select_months:{username}:3")],
        [InlineKeyboardButton(text="6 个月 (约 50 USDT)", callback_data=f"select_months:{username}:6")],
        [InlineKeyboardButton(text="12 个月 (约 90 USDT)", callback_data=f"select_months:{username}:12")],
        [InlineKeyboardButton(text="🔙 返回主菜单", callback_data="back_to_menu")]
    ])
    return kb

async def get_currency_keyboard(username: str, months: int, price: float):
    amount_ton = round(price / config.TON_PRICE_USDT, 2)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💎 TON (USDT-Jetton)", callback_data=f"pay:{months}:{username}:ton:{amount_ton}:{price}")],
        [InlineKeyboardButton(text="🟡 TRC20 (USDT)", callback_data=f"pay:{months}:{username}:trc20:{price}")],
        [InlineKeyboardButton(text="🔴 OkPay (快捷支付)", callback_data=f"pay:{months}:{username}:okpay:{price}")],
        [InlineKeyboardButton(text="🔙 重新选择月份", callback_data=f"reselect:{username}")]
    ])
    return kb

async def get_payment_keyboard(order_id: str, payment_method: str = "ton", payment_url: str = None):
    """
    根据支付方式生成按钮组
    payment_url: 仅用于 OkPay，生成跳转支付按钮
    """
    buttons = []

    # 1. OkPay 专属：前往支付按钮 (如果提供了 URL)
    # 即使刷新后，只要数据库里有这个链接，按钮就会保留
    if payment_method == "okpay" and payment_url:
        buttons.append([InlineKeyboardButton(text="🔗 前往 OkPay 支付", url=payment_url)])

    # 2. 我已完成支付按钮 (所有支付方式通用)
    # 加密货币点击后触发监控，OkPay 点击后仅提示收到
    buttons.append([InlineKeyboardButton(text="✅ 我已完成支付", callback_data=f"check:{order_id}")])

    # 3. 通用功能按钮
    buttons.append([InlineKeyboardButton(text="❌ 取消订单", callback_data=f"cancel:{order_id}")])
    buttons.append([InlineKeyboardButton(text="🔙 返回主菜单", callback_data="back_to_menu")])
    
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# --- 命令处理 ---

@router.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 欢迎使用 Premium 充值机器人！\n\n"
        "请选择您的购买方式：",
        reply_markup=await get_main_keyboard()
    )

# --- 回调处理 ---

@router.callback_query(F.data == "buy_self")
async def process_buy_self(callback: CallbackQuery):
    """处理为自己购买"""
    try:
        await callback.answer()
    except:
        pass
    
    user = callback.from_user
    if not user.username:
        await callback.message.edit_text("❌ 您的账号未设置用户名。\n\n请前往 Telegram 设置 -> 用户名 设置后再试。")
        return
    
    username = user.username
    
    try:
        await callback.message.edit_text(
            f"📅 正在为 **@{username}** 购买会员。\n\n"
            "请选择充值时长：",
            parse_mode="Markdown",
            reply_markup=await get_months_keyboard(username)
        )
    except TelegramBadRequest as e:
        logger.error(f"Edit message error: {e}")

@router.callback_query(F.data == "buy_other")
async def process_buy_other(callback: CallbackQuery):
    """处理为他人购买：弹出强制回复"""
    try:
        await callback.answer()
    except:
        pass
    
    try:
        await callback.message.edit_text(
            "👥 请输入要充值的 Telegram 用户名 (不带 @)。\n\n"
            "👇 请直接输入：",
            reply_markup=None
        )
    except TelegramBadRequest:
        pass

    await callback.message.answer(
        "👇 在此输入用户名:",
        reply_markup=ForceReply(selective=True)
    )

@router.message(F.text)
async def handle_manual_username_input(message: Message):
    """捕获手动输入的用户名"""
    text = message.text.strip().lstrip('@')
    
    if text.startswith('/'):
        return

    if not text:
        return

    username = text
    
    await message.answer(
        f"📅 正在为 **@{username}** 购买会员。\n\n"
        "请选择充值时长：",
        parse_mode="Markdown",
        reply_markup=await get_months_keyboard(username)
    )

@router.callback_query(F.data.startswith("select_months:"))
async def process_select_months(callback: CallbackQuery):
    """选择月份"""
    try:
        await callback.answer()
    except:
        pass
    
    try:
        _, username, months_str = callback.data.split(":")
        months = int(months_str)
        
        from services.fragment import fragment_service
        try:
            price_usdt = await fragment_service.get_price(username, months)
        except Exception:
            if months == 3: price_usdt = config.PRICE_3_MONTHS_USDT
            elif months == 6: price_usdt = config.PRICE_6_MONTHS_USDT
            elif months == 12: price_usdt = config.PRICE_12_MONTHS_USDT
            else: price_usdt = 28.0

        text = (
            f"📦 **订单确认**\n\n"
            f"用户: @{username}\n"
            f"时长: {months} 个月\n"
            f"价格: **{price_usdt} USDT**\n\n"
            f"请选择支付方式："
        )
        
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=await get_currency_keyboard(username, months, price_usdt))
    except Exception as e:
        logger.error(f"Select months error: {e}")

@router.callback_query(F.data.startswith("reselect:"))
async def reselect_months(callback: CallbackQuery):
    """重新选择月份"""
    username = callback.data.split(":")[1]
    try:
        await callback.message.edit_text(
            f"📅 正在为 **@{username}** 购买。\n\n"
            "请选择充值时长：",
            parse_mode="Markdown",
            reply_markup=await get_months_keyboard(username)
        )
    except:
        pass

@router.callback_query(F.data.startswith("pay:"))
async def process_currency(callback: CallbackQuery):
    """处理支付方式选择"""
    try:
        await callback.answer("⏳ 正在生成支付地址...")
    except:
        pass

    try:
        data_parts = callback.data.split(":")
        months = int(data_parts[1])
        username = data_parts[2]
        currency = data_parts[3]
        
        amount_ton = 0
        price_usdt = 0
        
        if currency == "ton":
            amount_ton = float(data_parts[4])
            price_usdt = float(data_parts[5])
        else:
            price_usdt = float(data_parts[4])

        user_id = callback.from_user.id
        order_id = f"ORD{int(asyncio.get_event_loop().time())}{user_id}"
        
        import hashlib
        wallet_index = abs(hash(f"{user_id}_{username}_{months}")) % 1000000

        # 1. 创建订单，初始状态设为 pending
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
                logger.error(f"TON Error: {e}")
                await callback.message.edit_text("❌ TON 钱包生成失败，请重试。")
                return
            
            text = (
                f"💰 **订单已创建**\n\n"
                f"📅 ID: `{order_id}`\n"
                f"👤 对象: @{username} ( {months} 月 )\n"
                f"💵 金额: **{price_usdt} USDT**\n"
                f"⚖️ 汇率: {amount_ton} TON\n\n"
                f"👛 **请转账 USDT-Jetton 至:**\n"
                f"`{ton_addr}`"
            )

        elif currency == "trc20":
            try:
                trc20_addr = hd_manager.generate_trc20_wallet(wallet_index)
            except Exception as e:
                logger.error(f"TRON Error: {e}")
                await callback.message.edit_text("❌ TRON 钱包生成失败，请重试。")
                return

            text = (
                f"💰 **订单已创建**\n\n"
                f"📅 ID: `{order_id}`\n"
                f"👤 对象: @{username} ( {months} 月 )\n"
                f"💵 金额: **{price_usdt} USDT** (TRC20)\n\n"
                f"👛 **请转账 USDT-TRC20 至:**\n"
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
                logger.error(f"OkPay Error: {e}")
                await callback.message.edit_text("❌ 支付网关失败，请重试。")
                return

            text = (
                f"💰 **订单已创建**\n\n"
                f"📅 ID: `{order_id}`\n"
                f"👤 对象: @{username} ( {months} 月 )\n"
                f"💵 金额: **{price_usdt} USDT**\n\n"
                f"请点击下方按钮跳转完成支付。"
            )

        # 2. 更新数据库中的钱包地址和支付链接
        # ⚠️ 注意：这里我们将 okpay_url 传给了 update_order_wallet
        # 请确保你的 db.update_order_wallet 方法使用了 **kwargs，否则需要手动修改数据库方法
        await db.update_order_wallet(order_id, ton_addr=ton_addr, trc20_addr=trc20_addr, okpay_url=okpay_url)
        
        # 3. 显示支付确认界面
        kb = await get_payment_keyboard(order_id, currency, okpay_url)
        try:
            await callback.message.edit_text(text, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=kb)
        except Exception as e:
            logger.error(f"Pay message edit error: {e}")

    except Exception as e:
        logger.error(f"Process currency error: {e}", exc_info=True)
        try:
            await callback.message.edit_text("❌ 订单处理错误。")
        except:
            pass

@router.callback_query(F.data.startswith("check:"))
async def check_payment_status(callback: CallbackQuery):
    """用户点击“我已完成支付”后的处理"""
    order_id = callback.data.split(":")[1]
    
    try:
        # 这里对 OkPay 给出的反馈稍微不同
        order = await db.get_order(order_id)
        if order and order.get('payment_method') == 'okpay' and order.get('status') == 'pending':
             await callback.answer("⏳ 收到通知，正在确认支付状态...")
        else:
             await callback.answer("⏳ 正在查询订单状态...")
    except:
        pass
    
    try:
        # 获取最新订单状态
        order = await db.get_order(order_id)
        
        if not order:
            await callback.message.edit_text("❌ 订单不存在")
            return

        method = order.get('payment_method')
        status = order.get('status')
        current_okpay_url = order.get('okpay_url') # 从数据库获取支付链接

        # --- 场景 1: OkPay 支付 ---
        if method == "okpay":
            if status in ('paid', 'completed'):
                msg = "✅ 支付成功！会员已到账。"
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 返回主菜单", callback_data="back_to_menu")]
                ])
                await callback.message.edit_text(msg, reply_markup=kb)
                return
            
            if status == 'pending':
                msg = (
                    "⏳ 支付网关正在处理您的订单。\n"
                    "支付成功后，系统将自动发货，请留意通知。\n"
                    "若支付完成但未到账，请点击按钮刷新。"
                )
                # 生成键盘，传入 current_okpay_url 保证刷新后按钮还在
                kb = await get_payment_keyboard(order_id, method, current_okpay_url)
                await callback.message.edit_text(msg, parse_mode="Markdown", reply_markup=kb)
                return

        # --- 场景 2: TON/TRC20 (链上监控) ---
        else: 
            if status == 'checking':
                msg = "⏳ 系统正在核实区块链交易，请稍候..."
                await callback.message.edit_text(msg, parse_mode="Markdown")
                return

            if status in ('paid', 'completed'):
                msg = "✅ 充值成功！会员已到账。"
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 返回主菜单", callback_data="back_to_menu")]
                ])
                await callback.message.edit_text(msg, reply_markup=kb)
                return

            if status == 'pending':
                # 只有加密货币支付才变更为 checking，触发 monitor 服务
                await db.update_order_status(order_id, "checking")
                logger.info(f"用户 {callback.from_user.id} 确认支付订单 {order_id}，状态变更为 checking")
                
                msg = (
                    "✅ 已收到您的支付确认通知。\n"
                    "⏳ 系统正在核实链上交易数据，通常需要 1-3 分钟。\n"
                    "请勿取消订单..."
                )
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🔙 返回主菜单", callback_data="back_to_menu")]
                ])
                await callback.message.edit_text(msg, parse_mode="Markdown", reply_markup=kb)

    except Exception as e:
        logger.error(f"Check payment error: {e}")

@router.callback_query(F.data.startswith("cancel:"))
async def cancel_order_handler(callback: CallbackQuery):
    """处理取消订单逻辑"""
    order_id = callback.data.split(":")[1]
    
    try:
        await callback.answer()
    except:
        pass

    try:
        order = await db.get_order(order_id)
        
        if not order:
            await callback.message.edit_text("❌ 订单不存在。", reply_markup=await get_main_keyboard())
            return

        status = order.get('status')

        if status in ('paid', 'completed', 'checking'):
            if status == 'checking':
                await callback.answer("⚠️ 订单正在核实中，无法直接取消，请联系客服。", show_alert=True)
            else:
                await callback.answer("⚠️ 订单已支付或正在处理，无法取消！", show_alert=True)
            return

        if status == 'pending':
            await db.delete_order(order_id)
            logger.info(f"用户 {callback.from_user.id} 取消了订单 {order_id}")
            
            await callback.message.edit_text(
                "❌ 订单已取消。", 
                reply_markup=await get_main_keyboard()
            )
        else:
            await callback.message.edit_text("⚠️ 当前订单状态无法取消。")

    except Exception as e:
        logger.error(f"取消订单失败: {e}")
        try:
            await callback.message.edit_text("❌ 取消失败，请重试。")
        except:
            pass

@router.callback_query(F.data == "back_to_menu")
async def back_to_menu(callback: CallbackQuery):
    """返回主菜单"""
    try:
        await callback.answer()
    except:
        pass
    
    await callback.message.edit_text(
        "🏠 返回主菜单",
        reply_markup=await get_main_keyboard()
    )

@router.callback_query(F.data == "help")
async def show_help(callback: CallbackQuery):
    """显示帮助"""
    try:
        await callback.answer()
    except:
        pass
    await callback.message.edit_text(
        "❓ **帮助说明**\n\n"
        "1. 点击为自己/他人购买。\n"
        "2. 选择购买时长。\n"
        "3. 选择支付方式并完成转账。\n"
        "4. 加密货币支付请点击“我已完成支付”，OkPay 支付请在网页完成支付后点击“我已完成支付”查询状态。\n\n"
        "如有问题请联系管理员。",
        parse_mode="Markdown",
        reply_markup=await get_main_keyboard()
    )
