# 小芽空投机 —— 口令匹配 Handler
# 用户发送 XY-XXXXXX 格式的口令，Bot 匹配后投递对应资源包

import re
import logging
from pyrogram import Client, filters
from pyrogram.types import Message

from bot import Bot
from config import FORCE_SUB_CHANNEL, ADMINS
from helper_func import subscribed
from database.database import lookup_code, increment_code_use, pack_exists, present_user, add_user

logger = logging.getLogger(__name__)

# 口令正则：XY- 前缀 + 6位大写字母数字，严格匹配整条消息
CODE_PATTERN = re.compile(r'^XY-[A-Z0-9]{6}$')


def _is_code_format(text: str) -> bool:
    """检查文本是否符合口令格式"""
    return bool(CODE_PATTERN.match(text.strip()))


@Bot.on_message(filters.text & filters.private & subscribed, group=5)
async def code_handler(client: Client, message: Message):
    """监听私聊文本消息，匹配口令格式后投递资源包"""
    text = (message.text or "").strip()

    # 前置过滤：不符合口令格式的直接忽略（零响应）
    if not _is_code_format(text):
        return

    # 检查是否在 store session 中（管理员存储模式下不触发口令）
    from plugins.store_session import get_session
    if message.from_user.id in ADMINS and get_session(message.from_user.id):
        return

    # 注册用户
    user_id = message.from_user.id
    if not await present_user(user_id):
        try:
            await add_user(user_id)
        except Exception:
            pass

    # 查找口令
    code = text
    pack_id = lookup_code(code)

    if not pack_id:
        # 口令无效或已过期 → 静默，不暴露信息
        return

    if not pack_exists(pack_id):
        # 资源包不存在 → 静默
        return

    # 口令有效，投递资源包
    increment_code_use(code)

    from plugins.start import _deliver_pack
    await _deliver_pack(client, message, pack_id)

    # 阻止后续 handler 处理
    message.stop_propagation()
