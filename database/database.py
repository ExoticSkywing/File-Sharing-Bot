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

def _ensure_table():
    """确保 fileshare_users 表存在（首次启动自动建表）"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS fileshare_users (
                    user_id BIGINT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS resource_packs (
                    pack_id    VARCHAR(64) PRIMARY KEY,
                    admin_id   BIGINT NOT NULL,
                    item_count INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pack_items (
                    id         INT AUTO_INCREMENT PRIMARY KEY,
                    pack_id    VARCHAR(64) NOT NULL,
                    item_type  ENUM('message','link') NOT NULL,
                    channel_id BIGINT,
                    message_id BIGINT,
                    media_group_id VARCHAR(64),
                    sort_order INT NOT NULL,
                    INDEX idx_pack (pack_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
            """)
    finally:
        conn.close()

# 启动时建表
_ensure_table()

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

async def create_pack(pack_id: str, admin_id: int):
    """创建资源包记录"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT IGNORE INTO resource_packs (pack_id, admin_id) VALUES (%s, %s)",
                (pack_id, admin_id)
            )
    finally:
        conn.close()

async def add_pack_item(pack_id: str, item_type: str, sort_order: int, channel_id=None, message_id=None, media_group_id=None):
    """添加资源包明细"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pack_items (pack_id, item_type, channel_id, message_id, media_group_id, sort_order) VALUES (%s, %s, %s, %s, %s, %s)",
                (pack_id, item_type, channel_id, message_id, media_group_id, sort_order)
            )
    finally:
        conn.close()

async def get_pack_items(pack_id: str):
    """获取资源包的所有明细，按 sort_order 升序"""
    conn = _get_conn()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cur:
            cur.execute(
                "SELECT * FROM pack_items WHERE pack_id = %s ORDER BY sort_order ASC",
                (pack_id,)
            )
            return cur.fetchall()
    finally:
        conn.close()

async def update_pack_count(pack_id: str, count: int):
    """更新资源包的条目总数"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE resource_packs SET item_count = %s WHERE pack_id = %s",
                (count, pack_id)
            )
    finally:
        conn.close()
