# 小芽空投机 —— 回调按钮处理

from pyrogram import __version__, filters
from bot import Bot
from config import OWNER_ID
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

@Bot.on_callback_query(filters.regex(r'^(about|close)$'))
async def cb_handler(client: Bot, query: CallbackQuery):
    data = query.data
    if data == "about":
        await query.message.edit_text(
            text = f"<b>📦 小芽空投机\n\n○ 用途：资源闪电空投\n○ 支持：图片 / 视频 / 文档 / 音频等全类型\n○ 技术栈：Python3 + Pyrogram {__version__}\n○ 特性：提货口令 / 自动销毁 / 防盗转</b>",
            disable_web_page_preview = True,
            reply_markup = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("❌ 关闭", callback_data = "close")
                    ]
                ]
            )
        )
    elif data == "close":
        await query.message.delete()
        try:
            await query.message.reply_to_message.delete()
        except:
            pass