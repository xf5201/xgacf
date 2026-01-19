import asyncio
import logging
import aiosqlite
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

class Database:
    def __init__(self, db_path="orders.db"):
        self.db_path = db_path
        self.connection: Optional[aiosqlite.Connection] = None
        self.cursor: Optional[aiosqlite.Cursor] = None

    async def connect(self):
        max_retries = 5
        retry_delay = 2
        
        for attempt in range(max_retries):
            try:
                if not self.connection:
                    self.connection = await aiosqlite.connect(self.db_path)
                    self.connection.row_factory = aiosqlite.Row
                    self.cursor = await self.connection.cursor()
                    
                    await self._optimize_connection()
                    await self.create_tables()
                    await self._migrate_database()
                    
                    logger.info("Database connected")
                    return
            except aiosqlite.OperationalError as e:
                if "database is locked" in str(e):
                    logger.warning(f"DB locked, retrying ({attempt + 1}/{max_retries})...")
                    if self.connection:
                        try: await self.connection.close()
                        except Exception: pass
                        self.connection = None
                    await asyncio.sleep(retry_delay)
                else:
                    raise e
            except Exception as e:
                logger.error(f"DB connection error: {e}")
                if self.connection:
                    await self.connection.close()
                raise e

        raise Exception("Failed to connect to database after multiple retries")

    async def _optimize_connection(self):
        # Performance optimization for SQLite
        try:
            await self.cursor.execute("PRAGMA journal_mode=WAL;")
            await self.cursor.execute("PRAGMA busy_timeout=5000;")
            await self.cursor.execute("PRAGMA synchronous=NORMAL;")
            await self.cursor.execute("PRAGMA cache_size=-64000;")
        except Exception as e:
            logger.warning(f"DB optimization failed: {e}")

    async def init(self):
        await self.connect()

    async def close(self):
        if self.connection:
            await self.connection.close()

    async def _migrate_database(self):
        # Simple migration: add missing columns
        try:
            await self.cursor.execute("SELECT okpay_url FROM orders LIMIT 1")
        except aiosqlite.OperationalError:
            try:
                await self.connection.execute("ALTER TABLE orders ADD COLUMN okpay_url TEXT")
                await self.connection.commit()
                logger.info("DB migrated: added okpay_url column")
            except Exception as e:
                logger.error(f"DB migration failed: {e}")

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
        
        # Indexes for common queries
        await self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);")
        await self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at);")
        await self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);")
        await self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_order_id ON orders(order_id);")
        await self.cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_ton_addr ON orders(ton_addr);")
        
        await self.connection.commit()

    async def create_order(self, data: Dict[str, Any]) -> bool:
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
            logger.info(f"Order created: {data.get('order_id')}")
            return True
        except Exception as e:
            logger.error(f"Failed to create order: {e}")
            return False

    async def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        await self.cursor.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,))
        row = await self.cursor.fetchone()
        return dict(row) if row else None

    async def get_all_checking_orders(self) -> List[Dict[str, Any]]:
        await self.cursor.execute(
            "SELECT * FROM orders WHERE status IN ('checking', 'paid') ORDER BY created_at DESC"
        )
        rows = await self.cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_expired_pending_orders(self, minutes: int) -> List[Dict[str, Any]]:
        await self.cursor.execute(
            "SELECT * FROM orders WHERE status = ? AND datetime(created_at) < datetime('now', '-' || ? || ' minutes')",
            ("pending", minutes)
        )
        rows = await self.cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_expired_checking_orders(self, minutes: int) -> List[Dict[str, Any]]:
        await self.cursor.execute(
            "SELECT * FROM orders WHERE status IN ('checking', 'paid') AND datetime(created_at) < datetime('now', '-' || ? || ' minutes')",
            (minutes)
        )
        rows = await self.cursor.fetchall()
        return [dict(row) for row in rows]

    async def mark_order_expired(self, order_id: str):
        await self.update_order_status(order_id, "expired")
        logger.info(f"Order {order_id} expired")

    async def update_order_wallet(self, order_id: str, ton_addr: str = None, trc20_addr: str = None, okpay_url: str = None):
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
        try:
            await self.cursor.execute("DELETE FROM orders WHERE order_id = ?", (order_id,))
            await self.connection.commit()
            logger.info(f"Order {order_id} deleted")
        except Exception as e:
            logger.error(f"Failed to delete order: {e}")

db = Database()
