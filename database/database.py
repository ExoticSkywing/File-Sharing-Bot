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
            # 口令映射表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pack_codes (
                    id         INT AUTO_INCREMENT PRIMARY KEY,
                    pack_id    VARCHAR(64) NOT NULL,
                    code       VARCHAR(64) NOT NULL UNIQUE,
                    code_type  ENUM('auto','custom') DEFAULT 'auto',
                    is_active  TINYINT(1) DEFAULT 1,
                    use_count  INT DEFAULT 0,
                    max_uses   INT DEFAULT 0,
                    expires_at TIMESTAMP NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_pack (pack_id),
                    INDEX idx_active_code (is_active, code)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            # 全局配置表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    setting_key   VARCHAR(64) PRIMARY KEY,
                    setting_value VARCHAR(255) NOT NULL,
                    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            # 领取记录表
            cur.execute("""
                CREATE TABLE IF NOT EXISTS pack_claims (
                    id         INT AUTO_INCREMENT PRIMARY KEY,
                    pack_id    VARCHAR(64) NOT NULL,
                    user_id    BIGINT NOT NULL,
                    claimed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_pack_user (pack_id, user_id)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """)
            # 初始化默认全局设置
            for setting_sql in [
                "INSERT IGNORE INTO bot_settings (setting_key, setting_value) VALUES ('protect_content', 'true')",
                "INSERT IGNORE INTO bot_settings (setting_key, setting_value) VALUES ('max_claims_per_user', '0')",
                "INSERT IGNORE INTO bot_settings (setting_key, setting_value) VALUES ('auto_delete_time', '0')",
            ]:
                cur.execute(setting_sql)
            # 兼容：resource_packs 新增列（忽略已存在错误）
            for col_sql in [
                "ALTER TABLE resource_packs ADD COLUMN name VARCHAR(255) DEFAULT NULL",
                "ALTER TABLE resource_packs ADD COLUMN tags VARCHAR(512) DEFAULT NULL",
                "ALTER TABLE resource_packs ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
                "ALTER TABLE resource_packs ADD COLUMN deleted_at DATETIME DEFAULT NULL",
                "ALTER TABLE resource_packs ADD INDEX idx_deleted (deleted_at)",
                "ALTER TABLE resource_packs ADD COLUMN protect_content BOOLEAN DEFAULT NULL",
                "ALTER TABLE resource_packs ADD COLUMN max_claims_per_user INT DEFAULT NULL",
                "ALTER TABLE resource_packs ADD COLUMN auto_delete_seconds INT DEFAULT NULL",
            ]:
                try:
                    cur.execute(col_sql)
                except Exception:
                    pass
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
    """检查资源包是否存在且已完成（排除已删除）"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM resource_packs WHERE pack_id = %s AND status = 'done' AND deleted_at IS NULL", (pack_id,))
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


# ==================== 口令管理 ====================

def create_pack_code(pack_id: str, code: str, code_type: str = 'auto'):
    """创建口令"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pack_codes (pack_id, code, code_type) VALUES (%s, %s, %s)",
                (pack_id, code, code_type)
            )
    finally:
        conn.close()


def lookup_code(code: str):
    """查找口令对应的 pack_id（仅查有效且未过期的，排除已删除包）"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pc.pack_id, pc.max_uses, pc.use_count FROM pack_codes pc "
                "JOIN resource_packs rp ON pc.pack_id = rp.pack_id "
                "WHERE pc.code = %s AND pc.is_active = 1 "
                "AND (pc.expires_at IS NULL OR pc.expires_at > NOW()) "
                "AND rp.deleted_at IS NULL",
                (code,)
            )
            row = cur.fetchone()
            if not row:
                return None
            pack_id, max_uses, use_count = row
            if max_uses > 0 and use_count >= max_uses:
                return None
            return pack_id
    finally:
        conn.close()


def increment_code_use(code: str):
    """口令使用次数 +1"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE pack_codes SET use_count = use_count + 1 WHERE code = %s",
                (code,)
            )
    finally:
        conn.close()


# ==================== 全局配置 ====================

def get_setting(key: str, default: str = '') -> str:
    """获取全局配置值"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT setting_value FROM bot_settings WHERE setting_key = %s", (key,))
            row = cur.fetchone()
            return row[0] if row else default
    finally:
        conn.close()


def set_setting(key: str, value: str):
    """设置全局配置值"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO bot_settings (setting_key, setting_value) VALUES (%s, %s) "
                "ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)",
                (key, value)
            )
    finally:
        conn.close()


def get_pack_protect_content(pack_id: str) -> bool:
    """获取空投包最终生效的 protect_content 值（包级优先 > 全局默认）"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT protect_content FROM resource_packs WHERE pack_id = %s",
                (pack_id,)
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                return bool(row[0])
            # 包级未设置，读取全局
            return get_setting('protect_content', 'true').lower() == 'true'
    finally:
        conn.close()


def get_pack_max_claims(pack_id: str) -> int:
    """获取空投包最终生效的单用户领取上限（包级优先 > 全局默认，0=不限）"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT max_claims_per_user FROM resource_packs WHERE pack_id = %s",
                (pack_id,)
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                return int(row[0])
            return int(get_setting('max_claims_per_user', '0'))
    finally:
        conn.close()


def get_pack_auto_delete(pack_id: str) -> int:
    """获取空投包最终生效的自动删除秒数（包级优先 > 全局默认，0=不删除）"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT auto_delete_seconds FROM resource_packs WHERE pack_id = %s",
                (pack_id,)
            )
            row = cur.fetchone()
            if row and row[0] is not None:
                return int(row[0])
            return int(get_setting('auto_delete_time', '0'))
    finally:
        conn.close()


def count_user_claims(pack_id: str, user_id: int) -> int:
    """查询用户对某包的领取次数"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM pack_claims WHERE pack_id = %s AND user_id = %s",
                (pack_id, user_id)
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def record_claim(pack_id: str, user_id: int):
    """记录一次领取"""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pack_claims (pack_id, user_id) VALUES (%s, %s)",
                (pack_id, user_id)
            )
    finally:
        conn.close()


# ==================== 身份绑定检查（通过精灵内部 API） ====================

def check_tg_bindstatus(tg_user_id: int) -> bool:
    """检查 TG 用户是否已绑定站点账号（调用精灵 /api/check-bind 端点）"""
    import hashlib
    import requests
    from config import VERIFY_API_BASE, VERIFY_API_KEY

    if not VERIFY_API_KEY:
        return False
    try:
        tg_uid_str = str(tg_user_id)
        sign = hashlib.md5((tg_uid_str + VERIFY_API_KEY).encode()).hexdigest()
        resp = requests.get(
            f"{VERIFY_API_BASE}/api/check-bind",
            params={"tg_uid": tg_uid_str, "sign": sign},
            timeout=5,
        )
        if resp.status_code == 200:
            return resp.json().get("bound", False)
        return False
    except Exception:
        return False
