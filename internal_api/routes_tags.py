"""
标签统计 + 分组管理（5 个端点）
从 api_gateway/routers/airdrop.py 迁移，DB 操作保持不变。
"""

import logging
import pymysql
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel
from config import (
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE,
    INTERNAL_API_KEY,
)
from auth import verify_sign

logger = logging.getLogger(__name__)
router = APIRouter()


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


# ─── Pydantic 模型 ───

class TagGroupCreateBody(BaseModel):
    tg_uid: int
    group_name: str


class TagGroupUpdateBody(BaseModel):
    tg_uid: int
    group_name: Optional[str] = None
    sort_order: Optional[int] = None


class TagGroupMembersBody(BaseModel):
    tg_uid: int
    tags: List[str]


# ═══════════════════════════════════════════════════
# 端点
# ═══════════════════════════════════════════════════

@router.get("/api/tags")
async def get_tags(
    tg_uid: int = Query(...),
    is_super: bool = Query(False),
    x_sign: str = Header(..., alias="X-Sign"),
):
    verify_sign(tg_uid, x_sign, INTERNAL_API_KEY)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            owner_cond = ""
            owner_params = []
            if not is_super:
                owner_cond = "AND rp.admin_id = %s"
                owner_params = [tg_uid]

            # 1. 获取所有标签及计数
            cur.execute(
                f"""
                SELECT TRIM(SUBSTRING_INDEX(SUBSTRING_INDEX(rp.tags, ',', numbers.n), ',', -1)) AS tag_name,
                       COUNT(*) AS cnt
                FROM resource_packs rp
                JOIN (
                    SELECT 1 AS n UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
                    UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8
                    UNION ALL SELECT 9 UNION ALL SELECT 10
                ) numbers
                ON CHAR_LENGTH(rp.tags) - CHAR_LENGTH(REPLACE(rp.tags, ',', '')) >= numbers.n - 1
                WHERE rp.status = 'done'
                  AND rp.tags IS NOT NULL AND rp.tags != ''
                  {owner_cond}
                GROUP BY tag_name
                HAVING tag_name != ''
                ORDER BY cnt DESC
                """,
                owner_params,
            )
            all_tags = {row["tag_name"]: row["cnt"] for row in cur.fetchall()}

            # 2. 未分类数量
            cur.execute(
                f"""
                SELECT COUNT(*) AS cnt FROM resource_packs rp
                WHERE rp.status = 'done' AND (rp.tags IS NULL OR rp.tags = '')
                {owner_cond}
                """,
                owner_params,
            )
            untagged_count = cur.fetchone()["cnt"]

            # 3. 总数
            cur.execute(
                f"SELECT COUNT(*) AS cnt FROM resource_packs rp WHERE rp.status = 'done' {owner_cond}",
                owner_params,
            )
            total = cur.fetchone()["cnt"]

            # 4. 获取用户的标签分组
            cur.execute(
                "SELECT id, group_name, sort_order FROM tag_groups WHERE owner_tg_id = %s ORDER BY sort_order, id",
                (tg_uid,),
            )
            groups_rows = cur.fetchall()

            # 5. 获取所有分组成员
            group_ids = [g["id"] for g in groups_rows]
            grouped_tag_names = set()
            groups_result = []

            if group_ids:
                placeholders = ",".join(["%s"] * len(group_ids))
                cur.execute(
                    f"SELECT group_id, tag_name FROM tag_group_members WHERE group_id IN ({placeholders})",
                    group_ids,
                )
                members_rows = cur.fetchall()

                # 清理计数为0的标签（自动从分组中移除）
                tags_to_remove = []
                for m in members_rows:
                    if m["tag_name"] not in all_tags:
                        tags_to_remove.append((m["group_id"], m["tag_name"]))

                if tags_to_remove:
                    for gid, tag_name in tags_to_remove:
                        cur.execute(
                            "DELETE FROM tag_group_members WHERE group_id = %s AND tag_name = %s",
                            (gid, tag_name),
                        )

                # 构建分组结果（只包含计数>0的标签）
                members_map = {}
                for m in members_rows:
                    if m["tag_name"] in all_tags:
                        members_map.setdefault(m["group_id"], []).append(m["tag_name"])
                        grouped_tag_names.add(m["tag_name"])

                for g in groups_rows:
                    g_tags = members_map.get(g["id"], [])
                    groups_result.append({
                        "id": g["id"],
                        "name": g["group_name"],
                        "sort_order": g["sort_order"],
                        "tags": [
                            {"tag": t, "count": all_tags[t]}
                            for t in g_tags
                        ],
                    })
            else:
                for g in groups_rows:
                    groups_result.append({
                        "id": g["id"],
                        "name": g["group_name"],
                        "sort_order": g["sort_order"],
                        "tags": [],
                    })

            # 6. 未分组标签
            ungrouped_tags = [
                {"tag": t, "count": c}
                for t, c in all_tags.items()
                if t not in grouped_tag_names
            ]
            ungrouped_tags.sort(key=lambda x: x["count"], reverse=True)

            return {
                "code": 0,
                "data": {
                    "groups": groups_result,
                    "ungrouped_tags": ungrouped_tags,
                    "untagged_count": untagged_count,
                    "total": total,
                },
                "message": "ok",
            }
    finally:
        conn.close()


@router.post("/api/tag-groups")
async def create_tag_group(
    body: TagGroupCreateBody,
    x_sign: str = Header(..., alias="X-Sign"),
):
    verify_sign(body.tg_uid, x_sign, INTERNAL_API_KEY)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO tag_groups (owner_tg_id, group_name) VALUES (%s, %s)",
                (body.tg_uid, body.group_name.strip()),
            )
            new_id = cur.lastrowid
            return {"code": 0, "data": {"id": new_id}, "message": "分组创建成功"}
    finally:
        conn.close()


@router.put("/api/tag-groups/{group_id}")
async def update_tag_group(
    group_id: int,
    body: TagGroupUpdateBody,
    x_sign: str = Header(..., alias="X-Sign"),
):
    verify_sign(body.tg_uid, x_sign, INTERNAL_API_KEY)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM tag_groups WHERE id = %s AND owner_tg_id = %s",
                (group_id, body.tg_uid),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="分组不存在")

            updates, params = [], []
            if body.group_name is not None:
                updates.append("group_name = %s")
                params.append(body.group_name.strip())
            if body.sort_order is not None:
                updates.append("sort_order = %s")
                params.append(body.sort_order)

            if not updates:
                return {"code": 0, "message": "无需更新"}

            params.append(group_id)
            cur.execute(
                f"UPDATE tag_groups SET {', '.join(updates)} WHERE id = %s",
                params,
            )
            return {"code": 0, "message": "分组更新成功"}
    finally:
        conn.close()


@router.delete("/api/tag-groups/{group_id}")
async def delete_tag_group(
    group_id: int,
    tg_uid: int = Query(...),
    x_sign: str = Header(..., alias="X-Sign"),
):
    verify_sign(tg_uid, x_sign, INTERNAL_API_KEY)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM tag_groups WHERE id = %s AND owner_tg_id = %s",
                (group_id, tg_uid),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="分组不存在")

            cur.execute("DELETE FROM tag_group_members WHERE group_id = %s", (group_id,))
            cur.execute("DELETE FROM tag_groups WHERE id = %s", (group_id,))

            return {"code": 0, "message": "分组已删除"}
    finally:
        conn.close()


@router.put("/api/tag-groups/{group_id}/members")
async def set_group_members(
    group_id: int,
    body: TagGroupMembersBody,
    x_sign: str = Header(..., alias="X-Sign"),
):
    verify_sign(body.tg_uid, x_sign, INTERNAL_API_KEY)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM tag_groups WHERE id = %s AND owner_tg_id = %s",
                (group_id, body.tg_uid),
            )
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="分组不存在")

            # 先从其他分组中移除这些标签（一个标签只属于一个组）
            if body.tags:
                tag_placeholders = ",".join(["%s"] * len(body.tags))
                cur.execute(
                    f"DELETE FROM tag_group_members WHERE tag_name IN ({tag_placeholders})",
                    body.tags,
                )

            # 清空当前分组
            cur.execute("DELETE FROM tag_group_members WHERE group_id = %s", (group_id,))

            # 重新插入
            if body.tags:
                values = [(group_id, t.strip()) for t in body.tags if t.strip()]
                if values:
                    cur.executemany(
                        "INSERT INTO tag_group_members (group_id, tag_name) VALUES (%s, %s)",
                        values,
                    )

            return {"code": 0, "message": "分组标签已更新"}
    finally:
        conn.close()
