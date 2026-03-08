import asyncio
import re
import random
import string
from datetime import datetime, timedelta
from typing import Dict, List, Optional
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.errors import FloodWait

from bot import Bot
from config import ADMINS
from database.database import create_pack, add_pack_item, update_pack_count
from helper_func import encode

STORE_SESSION_TIMEOUT = 600  # 10分钟超时
STORE_ALBUM_WAIT = 1.5       # 相册消息等待时间

# URL 解析正则
URL_PATTERNS = [
    r'https://t\.me/([^/]+)/(\d+)-(\d+)',      # 范围公有链接
    r'https://t\.me/c/(\d+)/(\d+)-(\d+)',      # 范围私有链接
    r'https://t\.me/([^/]+)/(\d+)',            # 单条公有链接
    r'https://t\.me/c/(\d+)/(\d+)',            # 单条私有链接
    r'tg://openmessage\?.*chat_id=(\d+).*message_id=(\d+)', # tg 内部跳转协议
]

class PackItem:
    def __init__(self, item_type: str, channel_id: Optional[int] = None, message_id: Optional[int] = None, media_group_id: Optional[str] = None):
        self.item_type = item_type
        self.channel_id = channel_id
        self.message_id = message_id
        self.media_group_id = media_group_id

class StoreSession:
    def __init__(self, admin_id: int):
        self.admin_id = admin_id
        self.pack_id = ''.join(random.choices(string.ascii_letters + string.digits, k=10))
        self.items: List[PackItem] = []
        self.pending_album: Dict[str, Dict] = {}  # media_group_id -> {"timer": Task, "messages": []}
        self.created_at = datetime.now()
        self.last_active = datetime.now()
        self.progress_msg: Optional[Message] = None

active_sessions: Dict[int, StoreSession] = {}

def get_session(user_id: int) -> Optional[StoreSession]:
    session = active_sessions.get(user_id)
    if session:
        if datetime.now() - session.last_active > timedelta(seconds=STORE_SESSION_TIMEOUT):
            del active_sessions[user_id]
            return None
        session.last_active = datetime.now()
        return session
    return None

async def update_progress(client: Client, session: StoreSession):
    text = (
        f"**📦 当前存储会话已收录 {len(session.items)} 条资源**\n\n"
        "你可以继续：\n"
        "• 发送文件、图文或相册\n"
        "• 转发频道消息\n"
        "• 发送含有 Telegram 链接的文本（支持单条、范围或混合）\n"
        "• 编辑刚刚发送的消息追加链接\n\n"
        "完成后点击下方按钮："
    )
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("✅ 结束并生成资源包", callback_data=f"finish_store_{session.pack_id}")]])
    
    if session.progress_msg:
        try:
            await session.progress_msg.edit_text(text, reply_markup=markup)
            return
        except Exception:
            pass
    
    session.progress_msg = await client.send_message(session.admin_id, text, reply_markup=markup)

def extract_links_from_text(text: str) -> List[PackItem]:
    items = []
    lines = text.strip().split('\n')
    for line in lines:
        matched = False
        # 解析正则匹配
        for idx, pattern in enumerate(URL_PATTERNS):
            matches = re.finditer(pattern, line)
            for match in matches:
                matched = True
                if idx == 0 or idx == 1:
                    # 范围链接: group 1 = channel, group 2 = start_id, group 3 = end_id
                    channel_str = match.group(1)
                    start_id = int(match.group(2))
                    end_id = int(match.group(3))
                    
                    channel_id = None
                    if channel_str.isdigit():
                        channel_id = int(f"-100{channel_str}")
                    
                    for msg_id in range(start_id, end_id + 1):
                        items.append(PackItem(item_type="link", channel_id=channel_id, message_id=msg_id))
                elif idx == 2 or idx == 3:
                    # 单条链接
                    channel_str = match.group(1)
                    msg_id = int(match.group(2))
                    channel_id = None
                    if channel_str.isdigit():
                        channel_id = int(f"-100{channel_str}")
                    items.append(PackItem(item_type="link", channel_id=channel_id, message_id=msg_id))
                elif idx == 4:
                    # tg openmessage 协议
                    channel_str = match.group(1)
                    msg_id = int(match.group(2))
                    items.append(PackItem(item_type="link", channel_id=int(f"-100{channel_str}"), message_id=msg_id))
                
                if matched:
                    break
    return items

@Bot.on_message(filters.command('store') & filters.private & filters.user(ADMINS))
async def cmd_store(client: Client, message: Message):
    user_id = message.from_user.id
    active_sessions[user_id] = StoreSession(admin_id=user_id)
    await update_progress(client, active_sessions[user_id])

@Bot.on_message(filters.private & filters.user(ADMINS) & ~filters.command(['start','users','broadcast','batch','genlink','stats','store']), group=1)
async def collect_messages(client: Client, message: Message):
    session = get_session(message.from_user.id)
    if not session:
        return
        
    # TG Links Parser
    if message.text:
        links_items = extract_links_from_text(message.text)
        if links_items:
            session.items.extend(links_items)
            await update_progress(client, session)
            # 保存原消息 ID 供后续使用编辑检测
            message.meta_store_index = len(session.items) - len(links_items)
            return

    # 直接转发自 DB 频道单条记录
    if message.forward_from_chat and message.forward_from_chat.id == client.db_channel.id:
        session.items.append(PackItem(item_type="message", channel_id=client.db_channel.id, message_id=message.forward_from_message_id, media_group_id=message.media_group_id))
        if not message.media_group_id:
            await update_progress(client, session)
        return

    # 如果是其他内容，copy 到 DB 频道
    async def _handle_copy():
        try:
            post_message = await message.copy(chat_id=client.db_channel.id, disable_notification=True)
            session.items.append(PackItem(item_type="message", channel_id=client.db_channel.id, message_id=post_message.id, media_group_id=post_message.media_group_id))
        except FloodWait as e:
            await asyncio.sleep(e.value)
            post_message = await message.copy(chat_id=client.db_channel.id, disable_notification=True)
            session.items.append(PackItem(item_type="message", channel_id=client.db_channel.id, message_id=post_message.id, media_group_id=post_message.media_group_id))
        except Exception as e:
            pass

    # 处理相册防抖和同步
    if message.media_group_id:
        group_id = message.media_group_id
        if group_id not in session.pending_album:
            session.pending_album[group_id] = {"messages": []}
            
            async def process_album():
                await asyncio.sleep(STORE_ALBUM_WAIT)
                album_data = session.pending_album.pop(group_id, None)
                if album_data:
                    for msg in album_data["messages"]:
                        # 我们不用在这里 copy, 让外部函数处理（此处可优化批量 copy，但按顺序遍历即可）
                        pass
                    await update_progress(client, session)
                    
            session.pending_album[group_id]["timer"] = asyncio.create_task(process_album())
        
        session.pending_album[group_id]["messages"].append(message)
        await _handle_copy()

    else:
        await _handle_copy()
        await update_progress(client, session)

@Bot.on_edited_message(filters.private & filters.text & filters.user(ADMINS))
async def check_edited_message(client: Client, message: Message):
    session = get_session(message.from_user.id)
    if not session:
        return
    
    # 获取原始消息长度来剔除旧的内容（在完整会话中简化做法，这里直接替换所有连接处理即可，但是由于是 append, 我们简单起见追加即可：提取最新的）
    # 或者将原有提取过的内容全量清理？为保证幂等，可以通过 message_id 持久化保存
    # TODO 追加更新（当前简化为仅提取新编辑的内容，或不做复杂差量）
    links_items = extract_links_from_text(message.text)
    if links_items:
        # 为了防重，清空相同来源渠道内容 (由于 session.items 并没有记录来源管理员消息_id，这里用简单全匹配排重)
        existing_signatures = {(i.channel_id, i.message_id) for i in session.items if i.item_type == 'link'}
        added_count = 0
        for item in links_items:
            if (item.channel_id, item.message_id) not in existing_signatures:
                session.items.append(item)
                added_count += 1
        
        if added_count > 0:
            await update_progress(client, session)

@Bot.on_callback_query(filters.regex(r"^finish_store_"))
async def finish_store(client: Client, query: CallbackQuery):
    pack_id = query.data.split("_")[2]
    user_id = query.from_user.id
    
    session = active_sessions.get(user_id)
    if not session or session.pack_id != pack_id:
        await query.answer("此存储会话已过期或已被处理，请发送 /store 重新开始", show_alert=True)
        try:
            await query.message.delete()
        except:
            pass
        return
        
    await query.message.edit_text("⏳ 正在打包汇整，请稍候...")
    
    # 持久化到数据库
    await create_pack(pack_id, user_id)
    
    for idx, item in enumerate(session.items):
        await add_pack_item(
            pack_id=pack_id,
            item_type=item.item_type,
            sort_order=idx + 1,
            channel_id=item.channel_id,
            message_id=item.message_id,
            media_group_id=item.media_group_id
        )
        
    await update_pack_count(pack_id, len(session.items))
    
    # 清理 Session
    del active_sessions[user_id]
    
    # 生成深链接
    string = f"pack-{pack_id}"
    base64_string = await encode(string)
    link = f"https://t.me/{client.username}?start={base64_string}"
    
    db_items_count = len(session.items)
    
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🔁 分享链接", url=f'https://telegram.me/share/url?url={link}')]])
    await query.message.edit_text(f"🎉 资源打包成功！\n\n📊 包含 {db_items_count} 个文件或消息记录。\n\n<b>🔗 这里是你的专属领取链接：</b>\n\n{link}", reply_markup=reply_markup, disable_web_page_preview=True)
