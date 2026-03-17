# 小芽空投机 —— 会话式资源收集器（/store Session）

import re
import asyncio
import secrets
import logging
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, InputMediaPhoto, InputMediaVideo
)
from pyrogram.errors import FloodWait

from bot import Bot
from config import ADMINS, CHANNEL_ID, STORE_SESSION_TIMEOUT, STORE_ALBUM_WAIT
from helper_func import encode
from database.database import create_pack, add_pack_item, update_pack_count, finish_pack, delete_pack, get_active_packs, get_pack_item_count, create_pack_code, check_tg_bindstatus, update_pack_meta

logger = logging.getLogger(__name__)

# ==================== 数据模型 ====================

@dataclass
class PackItem:
    """空投包中的一个条目"""
    message_id: int           # DB 频道中的消息 ID
    media_group_id: str = None  # 相册分组 ID（可选）

@dataclass
class StoreSession:
    """管理员的存储 Session"""
    admin_id: int
    pack_id: str
    client: Client = None  # 缓存 Pyrogram Client 引用
    items: List[PackItem] = field(default_factory=list)
    # 相册缓冲区：media_group_id -> list of messages
    album_buffer: Dict[str, List[Message]] = field(default_factory=dict)
    album_timers: Dict[str, asyncio.Task] = field(default_factory=dict)
    # 方案B：所有文本消息延迟到完成打包时才处理
    # pending_texts: msg_id -> 原始消息文本
    pending_texts: Dict[int, str] = field(default_factory=dict)
    pending_text_count: int = 0  # 待处理文本消息数（用于显示）
    # 文本消息的回复消息记录，编辑时删除旧回复避免堆叠
    text_reply_msgs: Dict[int, Message] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    last_activity: datetime = field(default_factory=datetime.now)
    status_message: Optional[Message] = None  # Bot 的状态提示消息
    timeout_task: Optional[asyncio.Task] = None

    def touch(self):
        """更新最后活动时间"""
        self.last_activity = datetime.now()

# 全局活跃 Session 字典
active_sessions: Dict[int, StoreSession] = {}

# ==================== 打包后元数据采集 ====================

POST_PACK_TIMEOUT = 60  # 每步超时秒数

@dataclass
class PostPackState:
    """打包完成后的元数据采集状态"""
    admin_id: int
    pack_id: str
    client: Client = None
    phase: str = 'tags'       # 'tags' | 'notes'
    link: str = ''
    code: Optional[str] = None
    item_count: int = 0
    is_bound: bool = False
    tags_text: Optional[str] = None   # 已采集的标签
    status_message: Optional[Message] = None
    timeout_task: Optional[asyncio.Task] = None

# 全局打包后状态字典
post_pack_states: Dict[int, PostPackState] = {}

def get_post_pack_state(admin_id: int) -> Optional[PostPackState]:
    """获取管理员当前的打包后状态"""
    return post_pack_states.get(admin_id)

def clear_post_pack_state(admin_id: int):
    """清理打包后状态"""
    state = post_pack_states.pop(admin_id, None)
    if state and state.timeout_task and not state.timeout_task.done():
        state.timeout_task.cancel()

# ==================== TG 链接解析器 ====================

# 支持的 TG 链接模式
TG_LINK_PATTERNS = [
    # 公开频道范围链接：https://t.me/channel/100-110
    re.compile(r'https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,})/((\d+)-(\d+))'),
    # 私有频道范围链接：https://t.me/c/12345/100-110
    re.compile(r'https?://t\.me/c/(\d+)/((\d+)-(\d+))'),
    # 公开频道单条：https://t.me/channel/123
    re.compile(r'https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,})/(\d+)'),
    # 私有频道单条：https://t.me/c/12345/123
    re.compile(r'https?://t\.me/c/(\d+)/(\d+)'),
    # tg:// 协议链接
    re.compile(r'tg://openmessage\?.*?chat_id=(\d+).*?message_id=(\d+)'),
]

def parse_tg_links(text: str) -> list:
    """
    解析文本中的所有 TG 链接，返回列表
    每个元素为 (channel_identifier, [msg_id, ...], is_range)
    - is_range=True: 范围链接，多个 id 自然成组
    - is_range=False: 单条链接
    """
    results = []
    seen = set()  # 去重

    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue

        # 尝试匹配范围链接（优先级高）
        # 公开频道范围
        m = re.search(r'https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,})/(\d+)-(\d+)', line)
        if m:
            channel = m.group(1)
            start_id = int(m.group(2))
            end_id = int(m.group(3))
            ids = []
            for mid in range(start_id, end_id + 1):
                key = (channel, mid)
                if key not in seen:
                    seen.add(key)
                    ids.append(mid)
            if ids:
                results.append((channel, ids, True))
            continue

        # 私有频道范围
        m = re.search(r'https?://t\.me/c/(\d+)/(\d+)-(\d+)', line)
        if m:
            channel = int(f"-100{m.group(1)}")
            start_id = int(m.group(2))
            end_id = int(m.group(3))
            ids = []
            for mid in range(start_id, end_id + 1):
                key = (channel, mid)
                if key not in seen:
                    seen.add(key)
                    ids.append(mid)
            if ids:
                results.append((channel, ids, True))
            continue

        # 公开频道单条
        m = re.search(r'https?://t\.me/([A-Za-z][A-Za-z0-9_]{3,})/(\d+)', line)
        if m:
            channel = m.group(1)
            mid = int(m.group(2))
            key = (channel, mid)
            if key not in seen:
                seen.add(key)
                results.append((channel, [mid], False))
            continue

        # 私有频道单条
        m = re.search(r'https?://t\.me/c/(\d+)/(\d+)', line)
        if m:
            channel = int(f"-100{m.group(1)}")
            mid = int(m.group(2))
            key = (channel, mid)
            if key not in seen:
                seen.add(key)
                results.append((channel, [mid], False))
            continue

        # tg:// 协议
        m = re.search(r'tg://openmessage\?.*?chat_id=(\d+).*?message_id=(\d+)', line)
        if m:
            channel = int(f"-100{m.group(1)}")
            mid = int(m.group(2))
            key = (channel, mid)
            if key not in seen:
                seen.add(key)
                results.append((channel, [mid], False))
            continue

    return results


def count_tg_links(links: list) -> int:
    """统计 TG 链接总条数"""
    return sum(len(ids) for _, ids, _ in links)

# ==================== Session 管理 ====================

def generate_pack_id() -> str:
    """生成 12 位随机 pack_id"""
    return secrets.token_urlsafe(9)  # 12 字符


def generate_code() -> str:
    """生成提货口令：XY- + 6位大写字母数字"""
    import string
    chars = string.ascii_uppercase + string.digits
    suffix = ''.join(secrets.choice(chars) for _ in range(6))
    return f"XY-{suffix}"

async def start_session(admin_id: int, client: Client = None) -> StoreSession:
    """创建一个新的存储 Session"""
    # 如果已有活跃 Session，先关闭
    if admin_id in active_sessions:
        await close_session(admin_id, cancelled=True)

    pack_id = generate_pack_id()
    session = StoreSession(admin_id=admin_id, pack_id=pack_id, client=client)

    # 数据库创建空投包记录
    create_pack(pack_id, admin_id)

    active_sessions[admin_id] = session

    # 启动超时定时器
    session.timeout_task = asyncio.create_task(session_timeout_watcher(admin_id))

    return session

async def session_timeout_watcher(admin_id: int):
    """监控 Session 超时"""
    try:
        while admin_id in active_sessions:
            await asyncio.sleep(min(STORE_SESSION_TIMEOUT / 3, 10))  # 检查间隔：超时值的 1/3 或 10 秒取较小
            session = active_sessions.get(admin_id)
            if not session:
                break
            elapsed = (datetime.now() - session.last_activity).total_seconds()
            if elapsed >= STORE_SESSION_TIMEOUT:
                has_content = len(session.items) > 0 or session.pending_text_count > 0
                if has_content:
                    # 有内容 → 走完整的打包流程（和"完成打包"按钮一样）
                    try:
                        if session.client:
                            await _finalize_session(session.client, session, session.status_message)
                        else:
                            # 无法获取 client，降级为简单完成
                            await close_session(admin_id, cancelled=False, reason="超时自动完成")
                            if session.status_message:
                                try:
                                    await session.status_message.edit_text(
                                        f"⏱ 会话超时，已自动完成打包\n"
                                        f"📦 共 {len(session.items)} 项资源已保存"
                                    )
                                except Exception:
                                    pass
                    except Exception as e:
                        logger.warning(f"超时自动完成异常: {e}")
                        await close_session(admin_id, cancelled=False, reason="超时自动完成")
                else:
                    # 无内容 → 取消
                    await close_session(admin_id, cancelled=True, reason="超时")
                    if session.status_message:
                        try:
                            await session.status_message.edit_text(
                                "⏱ 会话已超时关闭（未发送任何资源）"
                            )
                        except Exception:
                            pass
                break
    except asyncio.CancelledError:
        pass

def persist_item(session: StoreSession, item: PackItem):
    """将条目实时写入 DB 并追加到内存列表"""
    session.items.append(item)
    sort_order = len(session.items) - 1
    add_pack_item(session.pack_id, item.message_id, sort_order, item.media_group_id)


async def close_session(admin_id: int, cancelled: bool = False, reason: str = None):
    """关闭存储 Session"""
    session = active_sessions.pop(admin_id, None)
    if not session:
        return None

    # 取消超时定时器
    if session.timeout_task and not session.timeout_task.done():
        session.timeout_task.cancel()

    # 取消所有相册合并定时器
    for timer in session.album_timers.values():
        if not timer.done():
            timer.cancel()

    if not cancelled:
        # 正常完成：标记为 done（items 已实时写入）
        finish_pack(session.pack_id, len(session.items))
    else:
        # 取消：删除空包记录
        delete_pack(session.pack_id)

    return session

def get_session(admin_id: int) -> Optional[StoreSession]:
    """获取管理员当前的 Session"""
    return active_sessions.get(admin_id)

# ==================== 收录处理函数 ====================

async def process_media_message(client: Client, message: Message, session: StoreSession):
    """处理直接发送/转发的媒体消息"""
    session.touch()

    # 判断是否来自 DB 频道的转发
    is_from_db = (
        message.forward_from_chat and
        message.forward_from_chat.id == CHANNEL_ID
    )

    if is_from_db:
        # 来自 DB 频道：直接记录原始 message_id，不 copy
        db_msg_id = message.forward_from_message_id
        persist_item(session, PackItem(message_id=db_msg_id))
        total = len(session.items) + session.pending_text_count
        rep = await message.reply_text(
            f"✅ 已存入（来自存储频道），本包已有 <b>{total}</b> 项",
            quote=True
        )
    else:
        # 新内容：copy 到 DB 频道
        media_type = _get_media_type_label(message)
        try:
            post = await message.copy(chat_id=CHANNEL_ID, disable_notification=True)
            persist_item(session, PackItem(message_id=post.id))
            total = len(session.items) + session.pending_text_count
            rep = await message.reply_text(
                f"✅ 已存入 1 {media_type}，本包已有 <b>{total}</b> 项",
                quote=True
            )
        except FloodWait as e:
            await asyncio.sleep(e.value)
            post = await message.copy(chat_id=CHANNEL_ID, disable_notification=True)
            persist_item(session, PackItem(message_id=post.id))
            total = len(session.items) + session.pending_text_count
            rep = await message.reply_text(
                f"✅ 已存入 1 {media_type}，本包已有 <b>{total}</b> 项",
                quote=True
            )
        except Exception as e:
            logger.error(f"存入失败: {e}")
            await message.reply_text("❌ 存入失败，请重试", quote=True)
            return

    await _refresh_status_message(client, rep, session)

async def process_album_message(client: Client, message: Message, session: StoreSession):
    """处理相册中的单条消息（缓冲后合并处理）"""
    session.touch()
    group_id = message.media_group_id

    if group_id not in session.album_buffer:
        session.album_buffer[group_id] = []

    session.album_buffer[group_id].append(message)

    # 取消旧的定时器（如果有），重新设置
    if group_id in session.album_timers:
        timer = session.album_timers[group_id]
        if not timer.done():
            timer.cancel()

    # 设置合并定时器
    session.album_timers[group_id] = asyncio.create_task(
        _flush_album(client, session, group_id)
    )

async def _flush_album(client: Client, session: StoreSession, group_id: str):
    """等待相册消息收集完毕后统一处理"""
    await asyncio.sleep(STORE_ALBUM_WAIT)

    messages = session.album_buffer.pop(group_id, [])
    session.album_timers.pop(group_id, None)

    if not messages:
        return

    # 按消息 ID 排序
    messages.sort(key=lambda m: m.id)
    first_msg = messages[0]

    # 判断是否来自 DB 频道
    is_from_db = (
        first_msg.forward_from_chat and
        first_msg.forward_from_chat.id == CHANNEL_ID
    )

    count = len(messages)
    media_type, album_suffix = _get_album_label(messages)

    # 中间反馈：让用户立刻知道相册已收到，正在存入
    hint_msg = await first_msg.reply_text(
        f"⏳ 收到 {count} {media_type}{album_suffix}，正在存入...",
        quote=True
    )

    if is_from_db:
        # 来自 DB 频道的相册：直接记录原始 message_id
        for msg in messages:
            persist_item(session, PackItem(
                message_id=msg.forward_from_message_id,
                media_group_id=group_id
            ))
    else:
        # 新内容：用 copy_media_group 整体复制保持相册格式
        # 不传 captions，让 Pyrogram 自动复制原始 caption + entities，保留所有格式
        try:
            posted_msgs = await client.copy_media_group(
                chat_id=CHANNEL_ID,
                from_chat_id=first_msg.chat.id,
                message_id=first_msg.id,
                disable_notification=True
            )
            for pm in posted_msgs:
                persist_item(session, PackItem(
                    message_id=pm.id,
                    media_group_id=group_id
                ))
            count = len(posted_msgs)
        except FloodWait as e:
            await asyncio.sleep(e.value)
            try:
                posted_msgs = await client.copy_media_group(
                    chat_id=CHANNEL_ID,
                    from_chat_id=first_msg.chat.id,
                    message_id=first_msg.id,
                    disable_notification=True
                )
                for pm in posted_msgs:
                    persist_item(session, PackItem(
                        message_id=pm.id,
                        media_group_id=group_id
                    ))
                count = len(posted_msgs)
            except Exception as e:
                logger.error(f"相册存入失败（FloodWait重试后）: {e}")
                await first_msg.reply_text("❌ 相册存入失败，请重试", quote=True)
                return
        except Exception as e:
            logger.error(f"相册存入失败: {e}")
            await first_msg.reply_text("❌ 相册存入失败，请重试", quote=True)
            return

    total = len(session.items) + session.pending_text_count
    # 删除中间状态消息
    try:
        await hint_msg.delete()
    except Exception:
        pass
    rep = await first_msg.reply_text(
        f"✅ 已存入 {count} {media_type}{album_suffix}，本包已有 <b>{total}</b> 项",
        quote=True
    )
    await _refresh_status_message(client, rep, session)

async def process_text_message(client: Client, message: Message, session: StoreSession):
    """处理文本消息（方案B：暂存原始文本，完成时才解析+处理）"""
    session.touch()
    text = message.text or ""

    # 暂存原始文本
    session.pending_texts[message.id] = text
    session.pending_text_count = len(session.pending_texts)

    # 分析预览信息
    tg_links = parse_tg_links(text)
    link_count = count_tg_links(tg_links)

    media_count = len(session.items)
    total_display = media_count + session.pending_text_count

    if link_count > 0:
        preview = f"🔗 已记录 {link_count} 个 TG 链接（完成打包时统一处理）"
    else:
        preview = "📝 已记录 1 条文本（完成打包时统一处理）"

    preview += f"\n📊 本包已有 <b>{total_display}</b> 项（{media_count} 媒体 + {session.pending_text_count} 文本）"

    rep = await message.reply_text(preview, quote=True)
    session.text_reply_msgs[message.id] = rep
    await _refresh_status_message(client, rep, session)

async def process_edited_message(client: Client, message: Message, session: StoreSession):
    """处理编辑消息（方案B：更新内存中的暂存文本）"""
    session.touch()
    text = message.text or ""
    old_text = session.pending_texts.get(message.id)

    # 删除旧回复避免堆叠
    old_reply = session.text_reply_msgs.get(message.id)
    if old_reply:
        try:
            await old_reply.delete()
        except Exception:
            pass

    if old_text is None:
        # 新消息（不在暂存中），按新消息处理
        await process_text_message(client, message, session)
        return

    if text == old_text:
        await message.reply_text("ℹ️ 内容未变化", quote=True)
        return

    # 更新暂存
    session.pending_texts[message.id] = text

    # 分析变化
    old_links = parse_tg_links(old_text)
    new_links = parse_tg_links(text)

    change_parts = []
    if count_tg_links(new_links) != count_tg_links(old_links):
        diff = count_tg_links(new_links) - count_tg_links(old_links)
        if diff > 0:
            change_parts.append(f"新增 {diff} 个链接")
        else:
            change_parts.append(f"移除 {-diff} 个链接")
    if not change_parts:
        change_parts.append("文本已更新")
    change_text = "，".join(change_parts)

    media_count = len(session.items)
    total_display = media_count + session.pending_text_count
    rep = await message.reply_text(
        f"✅ {change_text}\n"
        f"📊 本包已有 <b>{total_display}</b> 项（{media_count} 媒体 + {session.pending_text_count} 文本）",
        quote=True
    )
    session.text_reply_msgs[message.id] = rep
    await _refresh_status_message(client, rep, session)

# ==================== 辅助函数 ====================

def _get_media_type_label(message: Message) -> str:
    """获取单条消息的媒体类型中文标签"""
    if message.photo:
        return "张图片"
    elif message.video:
        return "个视频"
    elif message.document:
        return "个文件"
    elif message.audio:
        return "个音频"
    elif message.voice:
        return "条语音"
    elif message.animation:
        return "个动图"
    else:
        return "条消息"


def _get_album_label(messages: list) -> tuple:
    """获取相册的标签和后缀，返回 (type_label, suffix)"""
    types = set()
    for msg in messages:
        if msg.photo:
            types.add('photo')
        elif msg.video:
            types.add('video')
        elif msg.document:
            types.add('document')
        elif msg.audio:
            types.add('audio')
        else:
            types.add('other')

    if len(types) == 1:
        t = types.pop()
        if t == 'document':
            return (_get_media_type_label(messages[0]), "（文件组）")
        else:
            return (_get_media_type_label(messages[0]), "（相册）")
    else:
        return ("项媒体", "（混合相册）")

def _extract_non_link_text(text: str) -> str:
    """从文本中去掉所有 TG 链接，保留剩余文字作为 caption"""
    result = text
    # 用所有 TG 链接模式匹配并移除
    for pattern in TG_LINK_PATTERNS:
        result = pattern.sub('', result)
    # 清理多余空白行
    lines = [line.strip() for line in result.strip().split('\n')]
    lines = [line for line in lines if line]
    return '\n'.join(lines)

def _has_media(message: Message) -> bool:
    """检查消息是否包含媒体文件"""
    return bool(
        message.photo or message.video or message.document or
        message.audio or message.voice or message.animation or
        message.sticker
    )

async def _refresh_status_message(client: Client, reply_msg: Message, session: StoreSession):
    """把完成/取消按钮附到最新那条回复消息上，同时清除上一条的按钮"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 完成打包", callback_data=f"store_done_{session.pack_id}")]
    ])
    # 先清除上一条消息的按钮
    if session.status_message and session.status_message.id != reply_msg.id:
        try:
            await session.status_message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
    # 把按钮附到当前回复消息
    try:
        await reply_msg.edit_reply_markup(reply_markup=keyboard)
        session.status_message = reply_msg
    except Exception as e:
        logger.warning(f"附加按钮到回复消息失败: {e}")

# ==================== 命令处理器 ====================

@Bot.on_message(filters.command('store') & filters.private & filters.user(ADMINS))
async def store_command(client: Client, message: Message):
    """管理员发送 /store 进入存储模式"""
    session = await start_session(message.from_user.id, client=client)

    welcome_text = (
        "📦 <b>存储模式已开启</b>\n\n"
        "请发送资源，支持以下方式：\n"
        "• 直接发送 文件/图片/视频/相册\n"
        "• 转发消息/相册\n"
        "• 发送 TG 链接（支持范围如 100-110）\n"
        "• 一条消息可混合多条链接\n"
        "• 编辑已发消息可追加链接\n\n"
        f"⏱ {STORE_SESSION_TIMEOUT // 60} 分钟无操作自动关闭" if STORE_SESSION_TIMEOUT >= 60 else f"⏱ {STORE_SESSION_TIMEOUT} 秒无操作自动关闭"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ 取消", callback_data=f"store_cancel_{session.pack_id}")]
    ])

    status_msg = await message.reply_text(welcome_text, reply_markup=keyboard, quote=True)
    session.status_message = status_msg

@Bot.on_message(filters.private & filters.user(ADMINS) & ~filters.command(['start', 'store', 'users', 'broadcast', 'stats']) & filters.incoming, group=-1)
async def session_message_handler(client: Client, message: Message):
    """Session 期间拦截管理员发送的所有非命令消息"""
    # 优先检查打包后元数据采集状态
    post_state = get_post_pack_state(message.from_user.id)
    if post_state and message.text:
        await _handle_post_pack_message(client, message, post_state)
        message.stop_propagation()
        return

    session = get_session(message.from_user.id)
    if not session:
        return  # 没有活跃 Session，交给其他 handler

    # 有相册标识 → 走相册缓冲逻辑
    if message.media_group_id:
        await process_album_message(client, message, session)
        message.stop_propagation()

    # 有媒体文件（非相册的单条）
    if _has_media(message):
        await process_media_message(client, message, session)
        message.stop_propagation()

    # 纯文本消息（含/不含链接）→ 统一暂存
    if message.text:
        await process_text_message(client, message, session)
        message.stop_propagation()

@Bot.on_edited_message(filters.private & filters.user(ADMINS), group=-1)
async def session_edited_handler(client: Client, message: Message):
    """监听管理员编辑消息事件（追加链接）"""
    session = get_session(message.from_user.id)
    if not session:
        return

    if message.text:
        await process_edited_message(client, message, session)

# ==================== 核心打包流程 ====================

async def _finalize_session(client: Client, session: StoreSession, status_message: Message = None):
    """完整的打包流程：处理 pending_texts → close_session → 生成分享链接
    
    超时自动完成 和 完成按钮 都调用此函数，保证逻辑一致。
    """
    admin_id = session.admin_id

    # ===== 处理 pending_texts =====
    if session.pending_text_count > 0:
        total_pending = session.pending_text_count
        processed = 0

        for src_msg_id, text in session.pending_texts.items():
            processed += 1
            if status_message:
                try:
                    await status_message.edit_text(
                        f"⏳ <b>正在处理文本资源...</b>\n\n"
                        f"📊 进度：{processed}/{total_pending}\n"
                        f"💾 已入库：<b>{len(session.items)}</b> 项"
                    )
                except Exception:
                    pass

            tg_links = parse_tg_links(text)

            if not tg_links:
                # 纯文本（无 TG 链接）→ 直接发到 DB 频道
                try:
                    posted = await client.send_message(
                        chat_id=CHANNEL_ID, text=text,
                        disable_web_page_preview=False, disable_notification=True
                    )
                    persist_item(session, PackItem(message_id=posted.id))
                except Exception as e:
                    logger.warning(f"纯文本存入失败: {e}")
                continue

            # 提取非链接文字作为独立文本
            caption_text = _extract_non_link_text(text)

            # ===== 第一步：统一变成 DB message_id =====
            range_groups = []
            single_visual_ids = []
            single_other_ids = []

            def _is_db_channel(ch_id):
                return (isinstance(ch_id, int) and ch_id == CHANNEL_ID) or \
                       (isinstance(ch_id, str) and hasattr(client.db_channel, 'username') and ch_id == client.db_channel.username)

            for channel_id, msg_ids, is_range in tg_links:
                is_db = _is_db_channel(channel_id)

                if is_range:
                    range_group_id = f"range_{src_msg_id}_{msg_ids[0]}"

                    if is_db:
                        db_visual_no_cap = []
                        for mid in msg_ids:
                            try:
                                src = await client.get_messages(chat_id=CHANNEL_ID, message_ids=mid)
                                if not src or src.empty:
                                    continue
                                is_visual = bool(src.photo or src.video)
                                has_cap = bool(src.caption)
                                if is_visual and not has_cap:
                                    db_visual_no_cap.append(mid)
                                else:
                                    single_other_ids.append(mid)
                            except Exception as e:
                                logger.warning(f"DB 范围消息获取失败 {mid}: {e}")
                                single_other_ids.append(mid)
                        if len(db_visual_no_cap) >= 2:
                            range_groups.append((range_group_id, db_visual_no_cap))
                        elif len(db_visual_no_cap) == 1:
                            single_other_ids.append(db_visual_no_cap[0])
                    else:
                        source_msgs = []
                        for mid in msg_ids:
                            try:
                                src = await client.get_messages(chat_id=channel_id, message_ids=mid)
                                if src and not src.empty:
                                    source_msgs.append(src)
                            except FloodWait as e:
                                await asyncio.sleep(e.value)
                                try:
                                    src = await client.get_messages(chat_id=channel_id, message_ids=mid)
                                    if src and not src.empty:
                                        source_msgs.append(src)
                                except Exception:
                                    pass
                            except Exception as ex:
                                logger.warning(f"范围链接获取失败 {channel_id}/{mid}: {ex}")

                        if not source_msgs:
                            continue

                        visual_srcs = [s for s in source_msgs if s.photo or s.video]
                        other_srcs = [s for s in source_msgs if not (s.photo or s.video)]

                        for vi in range(0, len(visual_srcs), 8):
                            vbatch = visual_srcs[vi:vi+8]
                            sub_group_id = f"{range_group_id}_{vi//8}" if len(visual_srcs) > 8 else range_group_id
                            if len(vbatch) >= 2:
                                media_list = []
                                for vs in vbatch:
                                    if vs.photo:
                                        media_list.append(InputMediaPhoto(media=vs.photo.file_id, caption=vs.caption or ""))
                                    elif vs.video:
                                        media_list.append(InputMediaVideo(media=vs.video.file_id, caption=vs.caption or ""))
                                try:
                                    posted = await client.send_media_group(
                                        chat_id=CHANNEL_ID, media=media_list, disable_notification=True
                                    )
                                    range_groups.append((sub_group_id, [p.id for p in posted]))
                                except FloodWait as e:
                                    await asyncio.sleep(e.value)
                                    try:
                                        posted = await client.send_media_group(
                                            chat_id=CHANNEL_ID, media=media_list, disable_notification=True
                                        )
                                        range_groups.append((sub_group_id, [p.id for p in posted]))
                                    except Exception as ex:
                                        logger.warning(f"范围 send_media_group 失败: {ex}")
                                except Exception as e:
                                    logger.warning(f"范围 send_media_group 失败: {e}")
                            else:
                                try:
                                    post = await vbatch[0].copy(chat_id=CHANNEL_ID, disable_notification=True)
                                    range_groups.append((sub_group_id, [post.id]))
                                except Exception as e:
                                    logger.warning(f"范围单条 copy 失败: {e}")

                        for os_msg in other_srcs:
                            try:
                                post = await os_msg.copy(chat_id=CHANNEL_ID, disable_notification=True)
                                single_other_ids.append(post.id)
                            except Exception as e:
                                logger.warning(f"范围非视觉 copy 失败: {e}")
                else:
                    mid = msg_ids[0]
                    try:
                        if is_db:
                            src = await client.get_messages(chat_id=CHANNEL_ID, message_ids=mid)
                        else:
                            src = await client.get_messages(chat_id=channel_id, message_ids=mid)

                        if not src or src.empty:
                            continue

                        has_caption = bool(src.caption)
                        is_visual = bool(src.photo or src.video)

                        if is_visual and not has_caption:
                            single_visual_ids.append((src, channel_id, is_db))
                        else:
                            if is_db:
                                single_other_ids.append(mid)
                            else:
                                post = await src.copy(chat_id=CHANNEL_ID, disable_notification=True)
                                single_other_ids.append(post.id)
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                        try:
                            if is_db:
                                single_other_ids.append(mid)
                            else:
                                src = await client.get_messages(chat_id=channel_id, message_ids=mid)
                                if src and not src.empty:
                                    post = await src.copy(chat_id=CHANNEL_ID, disable_notification=True)
                                    single_other_ids.append(post.id)
                        except Exception:
                            pass
                    except Exception as ex:
                        logger.warning(f"单条链接处理失败 {channel_id}/{mid}: {ex}")

            # ===== 第二步：记录 PackItem =====
            for group_id, db_ids in range_groups:
                for db_id in db_ids:
                    persist_item(session, PackItem(message_id=db_id, media_group_id=group_id))

            db_visuals = [(s, c, d) for s, c, d in single_visual_ids if d]
            ext_visuals = [(s, c, d) for s, c, d in single_visual_ids if not d]

            if len(db_visuals) >= 2:
                album_group_id = f"dbsingles_{src_msg_id}"
                for src_msg, ch_id, _ in db_visuals:
                    persist_item(session, PackItem(message_id=src_msg.id, media_group_id=album_group_id))
            elif len(db_visuals) == 1:
                persist_item(session, PackItem(message_id=db_visuals[0][0].id))

            for i in range(0, len(ext_visuals), 8):
                batch = ext_visuals[i:i+8]
                if len(batch) >= 2:
                    media_list = []
                    for src_msg, ch_id, _ in batch:
                        if src_msg.photo:
                            media_list.append(InputMediaPhoto(media=src_msg.photo.file_id))
                        elif src_msg.video:
                            media_list.append(InputMediaVideo(media=src_msg.video.file_id))

                    album_group_id = f"singles_{src_msg_id}_{i//8}"
                    try:
                        posted_msgs = await client.send_media_group(
                            chat_id=CHANNEL_ID, media=media_list, disable_notification=True
                        )
                        for pm in posted_msgs:
                            persist_item(session, PackItem(message_id=pm.id, media_group_id=album_group_id))
                    except FloodWait as e:
                        await asyncio.sleep(e.value)
                        try:
                            posted_msgs = await client.send_media_group(
                                chat_id=CHANNEL_ID, media=media_list, disable_notification=True
                            )
                            for pm in posted_msgs:
                                persist_item(session, PackItem(message_id=pm.id, media_group_id=album_group_id))
                        except Exception as ex:
                            logger.warning(f"send_media_group 失败: {ex}")
                    except Exception as e:
                        logger.warning(f"send_media_group 失败: {e}")
                else:
                    src_msg, ch_id, _ = batch[0]
                    try:
                        post = await src_msg.copy(chat_id=CHANNEL_ID, disable_notification=True)
                        persist_item(session, PackItem(message_id=post.id))
                    except Exception as e:
                        logger.warning(f"visual copy 失败: {e}")

            for db_id in single_other_ids:
                persist_item(session, PackItem(message_id=db_id))

            if caption_text:
                try:
                    cap_msg = await client.send_message(
                        chat_id=CHANNEL_ID, text=caption_text,
                        disable_web_page_preview=False, disable_notification=True
                    )
                    persist_item(session, PackItem(message_id=cap_msg.id))
                except Exception as e:
                    logger.warning(f"caption 文本存入失败: {e}")

    # ===== 完成 Session =====
    completed_session = await close_session(admin_id, cancelled=False)
    item_count = len(completed_session.items)

    if item_count == 0:
        # 处理完发现没有有效内容
        if status_message:
            try:
                await status_message.edit_text("⚠️ 未找到有效资源，会话已关闭")
            except Exception:
                pass
        return

    # 生成分享链接
    base64_string = await encode(f"pack-{completed_session.pack_id}")
    link = f"https://t.me/{client.username}?start={base64_string}"

    # 自动生成提货口令
    code = generate_code()
    try:
        create_pack_code(completed_session.pack_id, code, 'auto')
    except Exception as e:
        logger.warning(f"口令生成失败，重试: {e}")
        code = generate_code()
        try:
            create_pack_code(completed_session.pack_id, code, 'auto')
        except Exception:
            code = None

    # 检查管理员是否已绑定站点账号
    is_bound = check_tg_bindstatus(admin_id)

    # 进入打包后元数据采集流程
    await _start_post_pack_tags(
        client, admin_id, completed_session.pack_id,
        link, code, item_count, is_bound, status_message
    )

# ==================== 打包后元数据采集流程 ====================

async def _start_post_pack_tags(
    client: Client, admin_id: int, pack_id: str,
    link: str, code: str, item_count: int, is_bound: bool,
    status_message: Message = None
):
    """第一步：提示输入标签"""
    # 清理可能存在的旧状态
    clear_post_pack_state(admin_id)

    state = PostPackState(
        admin_id=admin_id,
        pack_id=pack_id,
        client=client,
        phase='tags',
        link=link,
        code=code,
        item_count=item_count,
        is_bound=is_bound,
    )
    post_pack_states[admin_id] = state

    tag_prompt = (
        "🏷 <b>是否为这个空投包添加标签？</b>\n\n"
        "以空格分隔多个标签，例如：\n"
        "<code>游戏 和平精英 无畏契约 三角洲 博主A</code>\n\n"
        "标签可帮助你在后台快速分类和检索空投包。\n"
        f"⏱ {POST_PACK_TIMEOUT} 秒内无操作将自动跳过"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ 跳过，不添加标签", callback_data=f"postpack_skip_tags_{pack_id}")]
    ])

    try:
        if status_message:
            msg = await status_message.edit_text(tag_prompt, reply_markup=keyboard)
            state.status_message = status_message
        else:
            msg = await client.send_message(
                chat_id=admin_id, text=tag_prompt, reply_markup=keyboard
            )
            state.status_message = msg
    except Exception:
        msg = await client.send_message(
            chat_id=admin_id, text=tag_prompt, reply_markup=keyboard
        )
        state.status_message = msg

    # 启动超时定时器
    state.timeout_task = asyncio.create_task(_post_pack_timeout(admin_id, 'tags'))


async def _start_post_pack_notes(state: PostPackState):
    """第二步：提示输入备注"""
    state.phase = 'notes'

    # 取消旧超时（避免取消自身：超时回调也会调用此函数）
    current = asyncio.current_task()
    if state.timeout_task and state.timeout_task is not current and not state.timeout_task.done():
        state.timeout_task.cancel()

    note_prompt = (
        "📝 <b>是否为这个空投包添加备注？</b>\n\n"
        "直接输入备注文字即可，例如：\n"
        "<code>3月17日 XX频道合作资源</code>\n\n"
        f"⏱ {POST_PACK_TIMEOUT} 秒内无操作将自动跳过"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏭ 跳过，直接完成", callback_data=f"postpack_skip_notes_{state.pack_id}")]
    ])

    try:
        if state.status_message:
            await state.status_message.edit_text(note_prompt, reply_markup=keyboard)
        else:
            msg = await state.client.send_message(
                chat_id=state.admin_id, text=note_prompt, reply_markup=keyboard
            )
            state.status_message = msg
    except Exception:
        msg = await state.client.send_message(
            chat_id=state.admin_id, text=note_prompt, reply_markup=keyboard
        )
        state.status_message = msg

    # 启动新超时
    state.timeout_task = asyncio.create_task(_post_pack_timeout(state.admin_id, 'notes'))


async def _show_final_result(state: PostPackState):
    """显示最终打包结果"""
    # 安全清理：不取消当前正在执行的超时任务（可能就是调用者）
    admin_id = state.admin_id
    post_pack_states.pop(admin_id, None)
    current = asyncio.current_task()
    if state.timeout_task and state.timeout_task is not current and not state.timeout_task.done():
        state.timeout_task.cancel()

    if state.is_bound:
        manage_btn = InlineKeyboardButton(
            "◆ 管理我的空投包 ↗",
            url="https://center.manyuzo.com/#/airdrop/packs",
        )
    else:
        manage_btn = InlineKeyboardButton(
            "◆ 管理空投包",
            callback_data="bind_guide",
        )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📦 新建空投包", callback_data="store_new"),
            manage_btn,
        ],
    ])

    code_line = f"\n🔑 提货口令：<code>{state.code}</code>" if state.code else ""
    tags_line = f"\n🏷 标签：{state.tags_text}" if state.tags_text else ""
    # 从 DB 读取 name（备注）比较复杂，直接用内存中的状态
    # 备注信息在 _handle_post_pack_notes 中已写入 DB

    result_text = (
        f"🎉 <b>空投包已生成！</b>\n\n"
        f"📊 已存入 <b>{state.item_count}</b> 项资源\n"
        f"🔗 分享链接：\n<code>{state.link}</code>"
        f"{code_line}{tags_line}"
    )

    try:
        if state.status_message:
            await state.status_message.edit_text(result_text, reply_markup=keyboard)
        else:
            await state.client.send_message(
                chat_id=state.admin_id, text=result_text, reply_markup=keyboard
            )
    except Exception:
        try:
            await state.client.send_message(
                chat_id=state.admin_id, text=result_text, reply_markup=keyboard
            )
        except Exception:
            pass


async def _post_pack_timeout(admin_id: int, phase: str):
    """打包后元数据采集超时处理"""
    try:
        await asyncio.sleep(POST_PACK_TIMEOUT)
        state = get_post_pack_state(admin_id)
        if not state:
            return
        if phase == 'tags' and state.phase == 'tags':
            # 标签超时 → 跳到备注
            await _start_post_pack_notes(state)
        elif phase == 'notes' and state.phase == 'notes':
            # 备注超时 → 显示最终结果
            await _show_final_result(state)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"打包后超时处理异常: {e}")
        clear_post_pack_state(admin_id)


async def _handle_post_pack_message(client: Client, message: Message, state: PostPackState):
    """处理打包后元数据采集阶段收到的文本消息"""
    text = message.text.strip()

    if state.phase == 'tags':
        # 空格分割标签，转为逗号分割存入 DB
        tags = [t.strip() for t in text.split() if t.strip()]
        if tags:
            tags_csv = ','.join(tags)
            try:
                update_pack_meta(state.pack_id, tags=tags_csv)
                state.tags_text = ' '.join(tags)
                await message.reply_text(
                    f"✅ 已添加 {len(tags)} 个标签：{' '.join(tags)}",
                    quote=True
                )
            except Exception as e:
                logger.warning(f"标签写入失败: {e}")
                await message.reply_text("⚠️ 标签保存失败，已跳过", quote=True)
        else:
            await message.reply_text("ℹ️ 未检测到有效标签，已跳过", quote=True)

        # 进入备注阶段
        await _start_post_pack_notes(state)

    elif state.phase == 'notes':
        # 直接保存备注
        if text:
            try:
                update_pack_meta(state.pack_id, name=text)
                await message.reply_text(
                    f"✅ 已添加备注：{text}",
                    quote=True
                )
            except Exception as e:
                logger.warning(f"备注写入失败: {e}")
                await message.reply_text("⚠️ 备注保存失败，已跳过", quote=True)

        # 显示最终结果
        await _show_final_result(state)


# ==================== 回调按钮处理 ====================

@Bot.on_callback_query(filters.regex(r'^store_(done|cancel)_'))
async def store_callback(client: Client, query: CallbackQuery):
    """处理存储 Session 的完成/取消按钮"""
    data = query.data
    admin_id = query.from_user.id
    session = get_session(admin_id)

    if not session:
        await query.answer("⚠️ 没有进行中的存储任务", show_alert=True)
        return

    # 提取 action
    if data.startswith("store_done_"):
        pack_id = data[len("store_done_"):]
        if pack_id != session.pack_id:
            await query.answer("⚠️ Session 不匹配", show_alert=True)
            return

        if len(session.items) == 0 and session.pending_text_count == 0:
            await query.answer("⚠️ 还没有存入任何资源哦", show_alert=True)
            return

        await query.answer("⏳ 开始处理...", show_alert=False)
        await _finalize_session(client, session, query.message)

    elif data.startswith("store_cancel_"):
        pack_id = data[len("store_cancel_"):]
        if pack_id != session.pack_id:
            await query.answer("⚠️ Session 不匹配", show_alert=True)
            return

        # 只允许在没有存入任何资源的时候取消
        if len(session.items) > 0:
            await query.answer("⚠️ 已有资源存入，无法取消，请完成打包", show_alert=True)
            return

        await close_session(admin_id, cancelled=True)
        await query.message.edit_text("❌ 存储任务已取消")
        await query.answer("已取消")


@Bot.on_callback_query(filters.regex(r'^postpack_skip_(tags|notes)_') & filters.user(ADMINS))
async def postpack_skip_callback(client: Client, query: CallbackQuery):
    """处理打包后元数据采集的跳过按钮"""
    data = query.data
    admin_id = query.from_user.id
    state = get_post_pack_state(admin_id)

    if not state:
        await query.answer("⚠️ 采集已结束", show_alert=False)
        return

    if data.startswith("postpack_skip_tags_"):
        await query.answer("已跳过标签", show_alert=False)
        await _start_post_pack_notes(state)
    elif data.startswith("postpack_skip_notes_"):
        await query.answer("已跳过备注", show_alert=False)
        await _show_final_result(state)


@Bot.on_callback_query(filters.regex(r'^store_new$') & filters.user(ADMINS))
async def store_new_callback(client: Client, query: CallbackQuery):
    """点击「新建空投包」直接开启下一个 Session"""
    admin_id = query.from_user.id

    # 清理可能残留的打包后状态
    clear_post_pack_state(admin_id)

    # 如有旧 Session 先关闭
    if get_session(admin_id):
        await close_session(admin_id, cancelled=True)

    session = await start_session(admin_id, client=client)

    welcome_text = (
        "📦 <b>新空投包已开启</b>\n\n"
        "请发送资源，支持以下方式：\n"
        "• 直接发送 文件/图片/视频/相册\n"
        "• 转发消息/相册\n"
        "• 发送 TG 链接（支持范围如 100-110）\n"
        "• 一条消息可混合多条链接\n"
        "• 编辑已发消息可追加链接\n\n"
        f"⏱ {STORE_SESSION_TIMEOUT // 60} 分钟无操作自动关闭" if STORE_SESSION_TIMEOUT >= 60 else f"⏱ {STORE_SESSION_TIMEOUT} 秒无操作自动关闭"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ 取消", callback_data=f"store_cancel_{session.pack_id}")]
    ])

    # 把原完成消息的按钮清掉，避免「新建空投包」按钮悬空
    try:
        await query.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    status_msg = await client.send_message(
        chat_id=admin_id,
        text=welcome_text,
        reply_markup=keyboard
    )
    session.status_message = status_msg
    await query.answer("📦 新空投包已开启！")


@Bot.on_callback_query(filters.regex(r'^bind_guide$') & filters.user(ADMINS))
async def bind_guide_callback(client: Client, query: CallbackQuery):
    """未绑定时点「管理空投包」→ 弹窗解释 + 替换按钮为绑定链接"""
    await query.answer(
        "⚠️ 需要先绑定星小芽站点账号\n\n"
        "绑定后即可在网页端管理空投包、编辑口令、查看数据\n\n"
        "点击「确定」后，请点击下方「前往绑定」按钮 👇",
        show_alert=True,
    )
    # 弹窗关闭后，把当前按钮行替换为绑定链接
    try:
        old_kb = query.message.reply_markup.inline_keyboard
        new_rows = []
        for row in old_kb:
            new_row = []
            for btn in row:
                if btn.callback_data == "bind_guide":
                    new_row.append(InlineKeyboardButton(
                        "🔗 前往绑定 ↗",
                        url="https://t.me/xiaoya_id_bot?start=bind",
                    ))
                else:
                    new_row.append(btn)
            new_rows.append(new_row)
        await query.message.edit_reply_markup(InlineKeyboardMarkup(new_rows))
    except Exception:
        pass


@Bot.on_callback_query(filters.regex(r'^copy_(link|code)_') & filters.user(ADMINS))
async def copy_callback(client: Client, query: CallbackQuery):
    """复制链接/口令按钮 — 通过 answer 弹窗显示内容供用户复制"""
    data = query.data

    if data.startswith("copy_link_"):
        pack_id = data[len("copy_link_"):]
        base64_string = await encode(f"pack-{pack_id}")
        link = f"https://t.me/{client.username}?start={base64_string}"
        await query.answer(f"🔗 {link}", show_alert=True)

    elif data.startswith("copy_code_"):
        pack_id = data[len("copy_code_"):]
        from database.database import _get_conn
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT code FROM pack_codes WHERE pack_id = %s AND code_type = 'auto' LIMIT 1",
                    (pack_id,)
                )
                row = cur.fetchone()
        finally:
            conn.close()
        if row:
            await query.answer(f"🔑 {row[0]}", show_alert=True)
        else:
            await query.answer("⚠️ 该空投包暂无口令", show_alert=True)
