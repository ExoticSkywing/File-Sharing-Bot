# 数据库层 —— MySQL 适配（替换原 MongoDB 实现）

import pymysql
from config import MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE

def _get_conn():
    """获取 MySQL 连接（短连接模式，简单稳定）"""
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset='utf8mb4',
        autocommit=True
    )

def _ensure_tables():
    """确保所有表存在（首次启动自动建表）"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            # 用户表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fileshare_users (
                    user_id BIGINT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            # 资源包表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS resource_packs (
                    pack_id    VARCHAR(16) PRIMARY KEY,
                    admin_id   BIGINT NOT NULL,
                    item_count INT DEFAULT 0,
                    status     ENUM('active','done') DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            # 兼容旧表：如果 status 列不存在则添加，并将旧记录标记为已完成
            try:
                cur.execute("""
                    ALTER TABLE resource_packs
                    ADD COLUMN status ENUM('active','done') DEFAULT 'active'
                """)
                # 旧包在加列前就已完成，全部标记为 done
                cur.execute("UPDATE resource_packs SET status = 'done' WHERE status = 'active'")
            except Exception:
                pass  # 列已存在
            # 资源包明细表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pack_items (
                    id         INT AUTO_INCREMENT PRIMARY KEY,
                    pack_id    VARCHAR(16) NOT NULL,
                    message_id BIGINT NOT NULL,
                    media_group_id VARCHAR(64) DEFAULT NULL,
                    sort_order INT NOT NULL,
                    INDEX idx_pack (pack_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
    finally:
        conn.close()

# 启动时建表
_ensure_tables()

# ==================== 用户管理 ====================

async def present_user(user_id: int):
    """检查用户是否已存在"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM fileshare_users WHERE user_id = %s", (user_id,))
            return cur.fetchone() is not None
    finally:
        conn.close()

async def add_user(user_id: int):
    """新增用户"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO fileshare_users (user_id) VALUES (%s)",
                (user_id,)
            )
    finally:
        conn.close()

async def full_userbase():
    """获取所有用户 ID"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id FROM fileshare_users")
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()

async def del_user(user_id: int):
    """删除用户"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM fileshare_users WHERE user_id = %s", (user_id,))
    finally:
        conn.close()

# ==================== 资源包管理 ====================

def create_pack(pack_id: str, admin_id: int):
    """创建资源包"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO resource_packs (pack_id, admin_id) VALUES (%s, %s)",
                (pack_id, admin_id)
            )
    finally:
        conn.close()

def add_pack_item(pack_id: str, message_id: int, sort_order: int, media_group_id: str = None):
    """添加资源包明细条目"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pack_items (pack_id, message_id, media_group_id, sort_order) VALUES (%s, %s, %s, %s)",
                (pack_id, message_id, media_group_id, sort_order)
            )
    finally:
        conn.close()

def update_pack_count(pack_id: str, count: int):
    """更新资源包条目数"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE resource_packs SET item_count = %s WHERE pack_id = %s",
                (count, pack_id)
            )
    finally:
        conn.close()

def get_pack_items(pack_id: str):
    """获取资源包所有条目（按 sort_order 排序）"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT message_id, media_group_id, sort_order FROM pack_items WHERE pack_id = %s ORDER BY sort_order",
                (pack_id,)
            )
            return cur.fetchall()
    finally:
        conn.close()

def pack_exists(pack_id: str) -> bool:
    """检查资源包是否存在且已完成"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM resource_packs WHERE pack_id = %s AND status = 'done'", (pack_id,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def finish_pack(pack_id: str, item_count: int):
    """将资源包标记为完成"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE resource_packs SET status = 'done', item_count = %s WHERE pack_id = %s",
                (item_count, pack_id)
            )
    finally:
        conn.close()


def delete_pack(pack_id: str):
    """删除空资源包（取消时调用）"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM pack_items WHERE pack_id = %s", (pack_id,))
            cur.execute("DELETE FROM resource_packs WHERE pack_id = %s", (pack_id,))
    finally:
        conn.close()


def get_active_packs():
    """获取所有未完成的资源包（Bot 重启恢复用）"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pack_id, admin_id, item_count FROM resource_packs WHERE status = 'active'"
            )
            return cur.fetchall()
    finally:
        conn.close()


def get_pack_item_count(pack_id: str) -> int:
    """获取资源包当前条目数"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM pack_items WHERE pack_id = %s", (pack_id,))
            row = cur.fetchone()
            return row[0] if row else 0
    finally:
        conn.close()
