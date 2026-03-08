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
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
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
