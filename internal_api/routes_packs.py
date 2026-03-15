"""
资源包 CRUD + 口令管理（7 个端点）
从 api_gateway/routers/airdrop.py 迁移，DB 操作保持不变。
"""

import base64
import logging
import httpx
import pymysql
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel
from config import (
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE,
    TG_BOT_TOKEN, INTERNAL_API_KEY,
)
from auth import verify_sign

logger = logging.getLogger(__name__)
router = APIRouter()

# ─── 缓存 Bot username ───
_bot_username_cache: Optional[str] = None


async def _get_bot_username() -> str:
    global _bot_username_cache
    if _bot_username_cache:
        return _bot_username_cache
    if not TG_BOT_TOKEN:
        raise HTTPException(status_code=500, detail="TG_BOT_TOKEN 未配置")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getMe"
            )
            data = resp.json()
            if data.get("ok"):
                _bot_username_cache = data["result"]["username"]
                logger.info(f"Bot username 已缓存: @{_bot_username_cache}")
                return _bot_username_cache
            else:
                raise HTTPException(status_code=500, detail=f"TG API 错误: {data}")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=500, detail=f"获取 Bot 信息失败: {e}")


def _get_conn():
    return pymysql.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def _encode_pack_link(pack_id: str) -> str:
    raw = f"pack-{pack_id}"
    b64 = base64.urlsafe_b64encode(raw.encode("ascii")).decode("ascii").rstrip("=")
    return b64


# ─── Pydantic 模型 ───

class PackUpdateBody(BaseModel):
    tg_uid: int
    is_super: bool = False
    name: Optional[str] = None
    tags: Optional[str] = None


class PackDeleteParams(BaseModel):
    tg_uid: int
    is_super: bool = False


class CodeCreateBody(BaseModel):
    tg_uid: int
    is_super: bool = False
    code: str
    max_uses: int = 0
    expires_at: Optional[str] = None


class CodeUpdateBody(BaseModel):
    tg_uid: int
    is_super: bool = False
    is_active: Optional[bool] = None


# ═══════════════════════════════════════════════════
# 端点
# ═══════════════════════════════════════════════════

@router.get("/api/packs")
async def list_packs(
    tg_uid: int = Query(...),
    is_super: bool = Query(False),
    search: str = Query(""),
    tag: str = Query(""),
    group_id: int = Query(0),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    x_sign: str = Header(..., alias="X-Sign"),
):
    verify_sign(tg_uid, x_sign, INTERNAL_API_KEY)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            conditions = ["rp.status = 'done'"]
            params = []

            if not is_super:
                conditions.append("rp.admin_id = %s")
                params.append(tg_uid)

            if tag.strip():
                if tag.strip() == "__untagged__":
                    conditions.append("(rp.tags IS NULL OR rp.tags = '')")
                else:
                    conditions.append("FIND_IN_SET(%s, rp.tags)")
                    params.append(tag.strip())

            if group_id > 0 and not tag.strip():
                cur.execute(
                    "SELECT tag_name FROM tag_group_members WHERE group_id = %s",
                    (group_id,),
                )
                group_tags = [r["tag_name"] for r in cur.fetchall()]
                if group_tags:
                    tag_conditions = " OR ".join(["FIND_IN_SET(%s, rp.tags)"] * len(group_tags))
                    conditions.append(f"({tag_conditions})")
                    params.extend(group_tags)
                else:
                    conditions.append("0")

            if search.strip():
                search_term = f"%{search.strip()}%"
                conditions.append(
                    "(rp.name LIKE %s OR rp.tags LIKE %s OR rp.pack_id LIKE %s "
                    "OR EXISTS (SELECT 1 FROM pack_codes pc WHERE pc.pack_id = rp.pack_id AND pc.code LIKE %s))"
                )
                params.extend([search_term, search_term, search_term, search_term])

            where_clause = " AND ".join(conditions)

            cur.execute(
                f"SELECT COUNT(*) as total FROM resource_packs rp WHERE {where_clause}",
                params,
            )
            total = cur.fetchone()["total"]

            offset = (page - 1) * page_size
            cur.execute(
                f"""
                SELECT rp.pack_id, rp.admin_id, rp.item_count, rp.name, rp.tags,
                       rp.created_at, rp.updated_at
                FROM resource_packs rp
                WHERE {where_clause}
                ORDER BY rp.created_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [page_size, offset],
            )
            packs = cur.fetchall()

            if packs:
                pack_ids = [p["pack_id"] for p in packs]
                placeholders = ",".join(["%s"] * len(pack_ids))
                cur.execute(
                    f"SELECT pack_id, code, code_type, is_active, use_count, max_uses "
                    f"FROM pack_codes WHERE pack_id IN ({placeholders})",
                    pack_ids,
                )
                codes_rows = cur.fetchall()
                codes_map = {}
                for row in codes_rows:
                    codes_map.setdefault(row["pack_id"], []).append(row)
            else:
                codes_map = {}

            bot_username = await _get_bot_username()

            results = []
            for p in packs:
                pid = p["pack_id"]
                b64 = _encode_pack_link(pid)
                codes = codes_map.get(pid, [])
                auto_code = next((c["code"] for c in codes if c["code_type"] == "auto"), None)

                results.append({
                    "pack_id": pid,
                    "admin_id": p["admin_id"],
                    "item_count": p["item_count"],
                    "name": p["name"],
                    "tags": p["tags"],
                    "created_at": str(p["created_at"]) if p["created_at"] else None,
                    "updated_at": str(p["updated_at"]) if p["updated_at"] else None,
                    "share_link": f"https://t.me/{bot_username}?start={b64}",
                    "auto_code": auto_code,
                    "codes": codes,
                })

            return {
                "code": 0,
                "data": {
                    "items": results,
                    "total": total,
                    "page": page,
                    "page_size": page_size,
                },
                "message": "ok",
            }
    finally:
        conn.close()


@router.get("/api/packs/{pack_id}")
async def get_pack_detail(
    pack_id: str,
    tg_uid: int = Query(...),
    is_super: bool = Query(False),
    x_sign: str = Header(..., alias="X-Sign"),
):
    verify_sign(tg_uid, x_sign, INTERNAL_API_KEY)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM resource_packs WHERE pack_id = %s AND status = 'done'",
                (pack_id,),
            )
            pack = cur.fetchone()
            if not pack:
                raise HTTPException(status_code=404, detail="空投包不存在")
            if not is_super and pack["admin_id"] != tg_uid:
                raise HTTPException(status_code=403, detail="无权访问")

            cur.execute("SELECT * FROM pack_codes WHERE pack_id = %s", (pack_id,))
            codes = cur.fetchall()

            cur.execute(
                "SELECT COUNT(*) as cnt FROM pack_items WHERE pack_id = %s",
                (pack_id,),
            )
            item_count = cur.fetchone()["cnt"]

            bot_username = await _get_bot_username()
            b64 = _encode_pack_link(pack_id)

            return {
                "code": 0,
                "data": {
                    "pack_id": pack["pack_id"],
                    "admin_id": pack["admin_id"],
                    "item_count": item_count,
                    "name": pack["name"],
                    "tags": pack["tags"],
                    "created_at": str(pack["created_at"]) if pack["created_at"] else None,
                    "updated_at": str(pack["updated_at"]) if pack["updated_at"] else None,
                    "share_link": f"https://t.me/{bot_username}?start={b64}",
                    "codes": [
                        {
                            "id": c["id"],
                            "code": c["code"],
                            "code_type": c["code_type"],
                            "is_active": c["is_active"],
                            "use_count": c["use_count"],
                            "max_uses": c["max_uses"],
                            "expires_at": str(c["expires_at"]) if c["expires_at"] else None,
                            "created_at": str(c["created_at"]) if c["created_at"] else None,
                        }
                        for c in codes
                    ],
                },
                "message": "ok",
            }
    finally:
        conn.close()


@router.put("/api/packs/{pack_id}")
async def update_pack(
    pack_id: str,
    body: PackUpdateBody,
    x_sign: str = Header(..., alias="X-Sign"),
):
    verify_sign(body.tg_uid, x_sign, INTERNAL_API_KEY)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT admin_id FROM resource_packs WHERE pack_id = %s", (pack_id,))
            pack = cur.fetchone()
            if not pack:
                raise HTTPException(status_code=404, detail="空投包不存在")
            if not body.is_super and pack["admin_id"] != body.tg_uid:
                raise HTTPException(status_code=403, detail="无权编辑")

            updates = []
            params = []
            if body.name is not None:
                updates.append("name = %s")
                params.append(body.name)
            if body.tags is not None:
                updates.append("tags = %s")
                params.append(body.tags)

            if not updates:
                return {"code": 0, "message": "无需更新"}

            params.append(pack_id)
            cur.execute(
                f"UPDATE resource_packs SET {', '.join(updates)} WHERE pack_id = %s",
                params,
            )

            return {"code": 0, "message": "更新成功"}
    finally:
        conn.close()


@router.delete("/api/packs/{pack_id}")
async def delete_pack(
    pack_id: str,
    tg_uid: int = Query(...),
    is_super: bool = Query(False),
    x_sign: str = Header(..., alias="X-Sign"),
):
    verify_sign(tg_uid, x_sign, INTERNAL_API_KEY)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT admin_id FROM resource_packs WHERE pack_id = %s", (pack_id,))
            pack = cur.fetchone()
            if not pack:
                raise HTTPException(status_code=404, detail="空投包不存在")
            if not is_super and pack["admin_id"] != tg_uid:
                raise HTTPException(status_code=403, detail="无权删除")

            cur.execute("DELETE FROM pack_codes WHERE pack_id = %s", (pack_id,))
            cur.execute("DELETE FROM pack_items WHERE pack_id = %s", (pack_id,))
            cur.execute("DELETE FROM resource_packs WHERE pack_id = %s", (pack_id,))

            return {"code": 0, "message": "删除成功"}
    finally:
        conn.close()


@router.get("/api/packs/{pack_id}/link")
async def get_pack_link(
    pack_id: str,
    tg_uid: int = Query(...),
    is_super: bool = Query(False),
    x_sign: str = Header(..., alias="X-Sign"),
):
    verify_sign(tg_uid, x_sign, INTERNAL_API_KEY)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT admin_id FROM resource_packs WHERE pack_id = %s AND status = 'done'",
                (pack_id,),
            )
            pack = cur.fetchone()
            if not pack:
                raise HTTPException(status_code=404, detail="空投包不存在")
            if not is_super and pack["admin_id"] != tg_uid:
                raise HTTPException(status_code=403, detail="无权访问")

        bot_username = await _get_bot_username()
        b64 = _encode_pack_link(pack_id)

        return {
            "code": 0,
            "data": {
                "share_link": f"https://t.me/{bot_username}?start={b64}",
                "pack_id": pack_id,
            },
            "message": "ok",
        }
    finally:
        conn.close()


@router.post("/api/packs/{pack_id}/codes")
async def create_code(
    pack_id: str,
    body: CodeCreateBody,
    x_sign: str = Header(..., alias="X-Sign"),
):
    verify_sign(body.tg_uid, x_sign, INTERNAL_API_KEY)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT admin_id FROM resource_packs WHERE pack_id = %s", (pack_id,))
            pack = cur.fetchone()
            if not pack:
                raise HTTPException(status_code=404, detail="空投包不存在")
            if not body.is_super and pack["admin_id"] != body.tg_uid:
                raise HTTPException(status_code=403, detail="无权操作")

            cur.execute("SELECT 1 FROM pack_codes WHERE code = %s", (body.code,))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail="口令已存在")

            cur.execute(
                "INSERT INTO pack_codes (pack_id, code, code_type, max_uses, expires_at) "
                "VALUES (%s, %s, 'custom', %s, %s)",
                (pack_id, body.code, body.max_uses, body.expires_at),
            )

            return {"code": 0, "message": "口令创建成功"}
    finally:
        conn.close()


@router.put("/api/codes/{code_id}")
async def update_code(
    code_id: int,
    body: CodeUpdateBody,
    x_sign: str = Header(..., alias="X-Sign"),
):
    verify_sign(body.tg_uid, x_sign, INTERNAL_API_KEY)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pc.pack_id, rp.admin_id FROM pack_codes pc "
                "JOIN resource_packs rp ON pc.pack_id = rp.pack_id "
                "WHERE pc.id = %s",
                (code_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="口令不存在")
            if not body.is_super and row["admin_id"] != body.tg_uid:
                raise HTTPException(status_code=403, detail="无权操作")

            if body.is_active is not None:
                cur.execute(
                    "UPDATE pack_codes SET is_active = %s WHERE id = %s",
                    (1 if body.is_active else 0, code_id),
                )

            return {"code": 0, "message": "更新成功"}
    finally:
        conn.close()
