import asyncio
import logging
import aiosqlite

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path="orders.db"):
        self.db_path = db_path
        self.connection = None
        self.cursor = None

    async def connect(self):
        """
        连接数据库 (带有自动重试机制)
        """
        max_retries = 5  # 最多重试 5 次
        retry_delay = 2  # 每次间隔 2 秒
        
        for attempt in range(max_retries):
            try:
                # 尝试连接
                if not self.connection:
                    self.connection = await aiosqlite.connect(self.db_path)
                    self.connection.row_factory = aiosqlite.Row
                    self.cursor = await self.connection.cursor()
                    
                    # 优化连接
                    await self._optimize_connection()
                    
                    # 创建表
                    await self.create_tables()
                    
                    # 自动迁移
                    await self._migrate_database()
                    
                    logger.info("✅ 数据库连接成功")
                    return # 成功则退出循环

            except aiosqlite.OperationalError as e:
                if "database is locked" in str(e):
                    logger.warning(f"⚠️ 数据库被锁定，正在等待重试 ({attempt + 1}/{max_retries})...")
                    # 关闭可能存在的僵尸连接
                    if self.connection:
                        try:
                            await self.connection.close()
                        except Exception:
                            pass
                        self.connection = None
                    
                    # 等待后重试
                    await asyncio.sleep(retry_delay)
                else:
                    # 如果不是锁的问题，直接报错
                    raise e
            
            except Exception as e:
                # 其他未知错误
                logger.error(f"❌ 数据库连接未知错误: {e}")
                if self.connection:
                    await self.connection.close()
                raise e

        raise Exception(f"❌ 数据库连接失败：达到最大重试次数 ({max_retries})，请检查是否有其他进程正在占用数据库。")

    async def _optimize_connection(self):
        """优化 SQLite 配置"""
        try:
            # 开启 WAL 模式
            await self.cursor.execute("PRAGMA journal_mode=WAL;")
            # 设置忙等待超时为 5 秒
            await self.cursor.execute("PRAGMA busy_timeout=5000;")
            # 设置同步模式
            await self.cursor.execute("PRAGMA synchronous=NORMAL;")
            # 缓存大小
            await self.cursor.execute("PRAGMA cache_size=-64000;")
            
            logger.info("🚀 数据库连接优化完成 (WAL模式)")
        except Exception as e:
            # 忽略优化失败，继续启动
            logger.warning(f"⚠️ 数据库优化失败: {e}")

    async def init(self):
        """别名方法，兼容 main.py"""
        await self.connect()

    async def close(self):
        if self.connection:
            await self.connection.close()

    async def _migrate_database(self):
        """自动迁移数据库结构"""
        try:
            # 检查 okpay_url 列是否存在
            await self.cursor.execute("SELECT okpay_url FROM orders LIMIT 1")
        except aiosqlite.OperationalError:
            try:
                await self.connection.execute("ALTER TABLE orders ADD COLUMN okpay_url TEXT")
                await self.connection.commit()
                logger.info("🔄 数据库迁移完成：已添加 okpay_url 列")
            except Exception as e:
                logger.error(f"❌ 数据库迁移失败: {e}")

    async def create_tables(self):
        await self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT UNIQUE NOT NULL,
                user_id INTEGER,
                username TEXT,
                target TEXT,
                months INTEGER,
                amount_usdt REAL,
                amount_ton REAL,
                price_ton REAL,
                wallet_index INTEGER,
                payment_method TEXT,
                ton_addr TEXT,
                trc20_addr TEXT,
                okpay_url TEXT,
                status TEXT DEFAULT 'created',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # 创建索引 (如果不存在)
        await self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);")
        await self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);")
        
        await self.connection.commit()

    async def create_order(self, data: dict):
        """创建订单 (兼容 user.py)"""
        try:
            await self.cursor.execute(
                """
                INSERT OR REPLACE INTO orders 
                (order_id, user_id, username, target, months, amount_usdt, amount_ton, price_ton, wallet_index, payment_method, ton_addr, trc20_addr, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data.get("order_id"),
                    data.get("user_id"),
                    data.get("username"),
                    data.get("target"),
                    data.get("months"),
                    data.get("amount_usdt"),
                    data.get("amount_ton"),
                    data.get("price_ton"),
                    data.get("wallet_index"),
                    data.get("payment_method"),
                    data.get("ton_addr"),
                    data.get("trc20_addr"),
                    data.get("status", "created")
                )
            )
            await self.connection.commit()
            logger.info(f"📝 订单创建成功: {data.get('order_id')}")
            return True
        except Exception as e:
            logger.error(f"❌ 创建订单失败: {e}")
            return False

    async def get_order(self, order_id: str):
        await self.cursor.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
        row = await self.cursor.fetchone()
        return dict(row) if row else None

    async def get_order_by_id(self, order_id: str):
        return await self.get_order(order_id)

    async def get_all_checking_orders(self):
        """获取需要检查的订单 (checking)"""
        await self.cursor.execute(
            "SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC",
            ("checking",)
        )
        rows = await self.cursor.fetchall()
        return [dict(row) for row in rows]

    # ==========================
    # 新增：Cleaner 支持方法
    # ==========================
    async def get_expired_pending_orders(self, minutes: int):
        """获取超时的 'pending' 订单"""
        await self.cursor.execute(
            "SELECT * FROM orders WHERE status = ? AND datetime(created_at) < datetime('now', '-' || ? || ' minutes')",
            ("pending", minutes)
        )
        rows = await self.cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_expired_checking_orders(self, minutes: int):
        """获取超时的 'checking' 订单"""
        await self.cursor.execute(
            "SELECT * FROM orders WHERE status = ? AND datetime(created_at) < datetime('now', '-' || ? || ' minutes')",
            ("checking", minutes)
        )
        rows = await self.cursor.fetchall()
        return [dict(row) for row in rows]

    async def mark_order_expired(self, order_id: str):
        """标记订单为过期"""
        await self.update_order_status(order_id, "expired")
        logger.info(f"🕒 订单 {order_id} 标记为过期")

    # ==========================
    # 基础更新方法
    # ==========================
    async def update_order_wallet(self, order_id: str, ton_addr: str = None, trc20_addr: str = None, okpay_url: str = None):
        """更新钱包地址和支付链接"""
        updates = []
        params = []
        if ton_addr is not None:
            updates.append("ton_addr = ?")
            params.append(ton_addr)
        if trc20_addr is not None:
            updates.append("trc20_addr = ?")
            params.append(trc20_addr)
        if okpay_url is not None:
            updates.append("okpay_url = ?")
            params.append(okpay_url)
        
        if updates:
            params.append(order_id)
            query = f"UPDATE orders SET {', '.join(updates)} WHERE order_id = ?"
            await self.cursor.execute(query, tuple(params))
            await self.connection.commit()

    async def update_order_status(self, order_id: str, status: str):
        await self.cursor.execute(
            "UPDATE orders SET status = ? WHERE order_id = ?",
            (status, order_id)
        )
        await self.connection.commit()

    async def delete_order(self, order_id: str):
        """物理删除订单"""
        try:
            await self.cursor.execute("DELETE FROM orders WHERE order_id = ?", (order_id,))
            await self.connection.commit()
            logger.info(f"🗑️ 订单 {order_id} 已删除")
        except Exception as e:
            logger.error(f"❌ 删除订单失败: {e}")

db = Database()
