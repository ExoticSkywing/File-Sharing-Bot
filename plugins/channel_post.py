# 小芽空投机 —— 频道帖子处理（管理员直接发文件时自动转存 + 生成链接）

import asyncio
from pyrogram import filters, Client
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait

from bot import Bot
from config import ADMINS, CHANNEL_ID, DISABLE_CHANNEL_BUTTON
from helper_func import encode

# 排除所有已注册命令，避免和 /store 等命令冲突
@Bot.on_message(filters.private & filters.user(ADMINS) & ~filters.command(['start','users','broadcast','store','stats']))
async def channel_post(client: Client, message: Message):
    # 只在存储 Session 期间由 store_session.py 处理
    # 非 Session 状态下不自动存储，避免产生无组织的垃圾资源
    from plugins.store_session import active_sessions
    if message.from_user.id not in active_sessions:
        return
    # Session 期间由 store_session.py 的 handler 接管，此处不做处理
    return

@Bot.on_message(filters.channel & filters.incoming & filters.chat(CHANNEL_ID))
async def new_post(client: Client, message: Message):

    if DISABLE_CHANNEL_BUTTON:
        return

    converted_id = message.id * abs(client.db_channel.id)
    string = f"get-{converted_id}"
    base64_string = await encode(string)
    link = f"https://t.me/{client.username}?start={base64_string}"
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔁 分享链接", url=f'https://telegram.me/share/url?url={link}')]])
    try:
        await message.edit_reply_markup(reply_markup)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await message.edit_reply_markup(reply_markup)
    except Exception:
        pass
