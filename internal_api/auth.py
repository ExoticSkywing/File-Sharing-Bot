"""
签名认证模块 — 复用精灵模式
签名算法: md5(str(tg_uid) + INTERNAL_API_KEY)
"""

import hashlib
from fastapi import HTTPException, Header


def verify_sign(tg_uid: int, sign: str, api_key: str):
    """验证请求签名（与精灵 check-bind 一致的协议）"""
    expected = hashlib.md5((str(tg_uid) + api_key).encode()).hexdigest()
    if sign != expected:
        raise HTTPException(status_code=401, detail="Invalid signature")


def make_sign(tg_uid: int, api_key: str) -> str:
    """生成签名（供测试用）"""
    return hashlib.md5((str(tg_uid) + api_key).encode()).hexdigest()
