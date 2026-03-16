# 小芽空投机 —— /start 命令处理（资源投递 + 用户欢迎 + 管理工具）

import os
import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAudio
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated

from bot import Bot
from config import (
    ADMINS, FORCE_MSG, START_MSG, CUSTOM_CAPTION, DISABLE_CHANNEL_BUTTON,
    PROTECT_CONTENT, START_PIC, AUTO_DELETE_TIME, AUTO_DELETE_MSG,
    JOIN_REQUEST_ENABLE, FORCE_SUB_CHANNEL, CHANNEL_ID
)
from helper_func import subscribed, decode, get_messages, delete_file
from database.database import (
    add_user, del_user, full_userbase, present_user,
    get_pack_items, pack_exists, get_pack_protect_content,
    get_pack_max_claims, get_pack_auto_delete,
    count_user_claims, record_claim, get_setting,
)

logger = logging.getLogger(__name__)


def _apply_caption(msg, custom_caption):
    """应用 CUSTOM_CAPTION（追加模式）"""
    if custom_caption and msg.document:
        original = "" if not msg.caption else msg.caption.html
        return f"{original}\n\n{custom_caption}" if original else custom_caption
    return "" if not msg.caption else msg.caption.html


async def _deliver_pack(client: Client, message: Message, pack_id: str):
    """投递资源包到用户"""
    user_id = message.from_user.id

    # 领取次数检查
    max_claims = get_pack_max_claims(pack_id)
    if max_claims > 0:
        used = count_user_claims(pack_id, user_id)
        if used >= max_claims:
            await message.reply_text(
                f"⚠️ 你已领取过该资源包（{used}/{max_claims}次），无法再次领取。"
            )
            return

    items = get_pack_items(pack_id)
    if not items:
        await message.reply_text("❌ 资源包不存在或已过期")
        return

    # 包级优先 > 全局默认
    pack_protect = get_pack_protect_content(pack_id)
    pack_auto_delete = get_pack_auto_delete(pack_id)

    total = len(items)
    temp_msg = await message.reply(f"✨ 资源正在空投中，共 {total} 条，请稍候...")

    track_msgs = []
    sent = 0

    # 按 media_group_id 分组处理
    # items: [(message_id, media_group_id, sort_order), ...]
    groups = {}  # media_group_id -> [message_id, ...]
    ordered_keys = []  # 保持顺序
    singles = []  # 无分组的单条消息 [(sort_order, message_id), ...]

    for msg_id, group_id, sort_order in items:
        if group_id:
            if group_id not in groups:
                groups[group_id] = []
                ordered_keys.append(('group', group_id, sort_order))
            groups[group_id].append(msg_id)
        else:
            ordered_keys.append(('single', msg_id, sort_order))

    # 按 sort_order 排序
    ordered_keys.sort(key=lambda x: x[2])

    for key_type, key_id, _ in ordered_keys:
        if key_type == 'group':
            # 相册：按媒体大类拆分批次推送
            # Telegram 规则：photo/video 可混合，document/audio 可混合，但两大类不可互混
            album_msg_ids = groups[key_id]
            try:
                album_msgs = await get_messages(client, album_msg_ids)

                # 按大类分批：visual（photo/video）和 file（document/audio）
                visual_batch = []
                file_batch = []

                for amsg in album_msgs:
                    caption = _apply_caption(amsg, CUSTOM_CAPTION) if amsg == album_msgs[-1] else ("" if not amsg.caption else amsg.caption.html)
                    if amsg.photo:
                        visual_batch.append(InputMediaPhoto(
                            amsg.photo.file_id, caption=caption, parse_mode=ParseMode.HTML
                        ))
                    elif amsg.video:
                        visual_batch.append(InputMediaVideo(
                            amsg.video.file_id, caption=caption, parse_mode=ParseMode.HTML
                        ))
                    elif amsg.document:
                        file_batch.append(InputMediaDocument(
                            amsg.document.file_id, caption=caption, parse_mode=ParseMode.HTML
                        ))
                    elif amsg.audio:
                        file_batch.append(InputMediaAudio(
                            amsg.audio.file_id, caption=caption, parse_mode=ParseMode.HTML
                        ))
                    else:
                        # 非媒体消息（文本等）→ 降级为单条发送
                        try:
                            copied = await amsg.copy(
                                chat_id=message.from_user.id,
                                protect_content=pack_protect
                            )
                            if copied and pack_auto_delete > 0:
                                track_msgs.append(copied)
                            sent += 1
                        except Exception as e:
                            logger.warning(f"组内非媒体消息发送失败: {e}")

                for batch in [visual_batch, file_batch]:
                    if not batch:
                        continue
                    sent_group = await client.send_media_group(
                        chat_id=message.from_user.id,
                        media=batch,
                        protect_content=pack_protect
                    )
                    track_msgs.extend(sent_group)
                    sent += len(sent_group)

                if total > 3:
                    try:
                        await temp_msg.edit_text(f"📥 正在空投 ({sent}/{total})...")
                    except Exception:
                        pass

            except Exception as e:
                logger.warning(f"相册推送失败，降级为逐条推送: {e}")
                for mid in album_msg_ids:
                    await _send_single(client, message, mid, track_msgs, pack_protect)
                    sent += 1

        else:
            # 单条消息
            await _send_single(client, message, key_id, track_msgs, pack_protect)
            sent += 1
            if total > 3 and sent % 3 == 0:
                try:
                    await temp_msg.edit_text(f"📥 正在空投 ({sent}/{total})...")
                except Exception:
                    pass

    await temp_msg.delete()

    # 记录领取
    record_claim(pack_id, user_id)

    # 自动销毁（包级优先）
    if track_msgs and pack_auto_delete > 0:
        delete_data = await client.send_message(
            chat_id=message.from_user.id,
            text=AUTO_DELETE_MSG.format(time=pack_auto_delete)
        )
        asyncio.create_task(delete_file(track_msgs, client, delete_data, pack_auto_delete))


async def _send_single(client, message, msg_id, track_msgs, protect_content=None):
    """发送单条消息（从 DB 频道 copy 到用户）"""
    if protect_content is None:
        protect_content = PROTECT_CONTENT
    try:
        msgs = await get_messages(client, [msg_id])
        if not msgs:
            return
        msg = msgs[0]

        caption = _apply_caption(msg, CUSTOM_CAPTION)
        reply_markup = msg.reply_markup if DISABLE_CHANNEL_BUTTON else None

        copied = await msg.copy(
            chat_id=message.from_user.id,
            caption=caption,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            protect_content=protect_content
        )
        if copied:
            track_msgs.append(copied)
    except FloodWait as e:
        await asyncio.sleep(e.value)
        await _send_single(client, message, msg_id, track_msgs, protect_content)
    except Exception as e:
        logger.warning(f"发送消息 {msg_id} 失败: {e}")


async def _deliver_legacy(client: Client, message: Message, ids):
    """投递旧格式（get-xxx）的资源"""
    temp_msg = await message.reply("✨ 资源正在空投中，请稍候...")
    try:
        messages = await get_messages(client, ids)
    except:
        await message.reply_text("❌ 糟糕，资源提取失败了，请稍后重试~")
        return
    await temp_msg.delete()

    track_msgs = []
    total = len(messages)
    sent = 0

    for msg in messages:
        caption = _apply_caption(msg, CUSTOM_CAPTION)
        reply_markup = msg.reply_markup if DISABLE_CHANNEL_BUTTON else None

        if AUTO_DELETE_TIME and AUTO_DELETE_TIME > 0:
            try:
                copied = await msg.copy(
                    chat_id=message.from_user.id, caption=caption,
                    parse_mode=ParseMode.HTML, reply_markup=reply_markup,
                    protect_content=PROTECT_CONTENT
                )
                if copied:
                    track_msgs.append(copied)
            except FloodWait as e:
                await asyncio.sleep(e.value)
                copied = await msg.copy(
                    chat_id=message.from_user.id, caption=caption,
                    parse_mode=ParseMode.HTML, reply_markup=reply_markup,
                    protect_content=PROTECT_CONTENT
                )
                if copied:
                    track_msgs.append(copied)
            except Exception as e:
                logger.warning(f"复制消息失败: {e}")
        else:
            try:
                await msg.copy(
                    chat_id=message.from_user.id, caption=caption,
                    parse_mode=ParseMode.HTML, reply_markup=reply_markup,
                    protect_content=PROTECT_CONTENT
                )
                await asyncio.sleep(0.5)
            except FloodWait as e:
                await asyncio.sleep(e.value)
                await msg.copy(
                    chat_id=message.from_user.id, caption=caption,
                    parse_mode=ParseMode.HTML, reply_markup=reply_markup,
                    protect_content=PROTECT_CONTENT
                )
            except:
                pass

        sent += 1
        # 进度反馈
        if total > 3 and sent % 3 == 0 and sent < total:
            pass  # 旧格式不做额外进度，保持原有体验

    if track_msgs:
        delete_data = await client.send_message(
            chat_id=message.from_user.id,
            text=AUTO_DELETE_MSG.format(time=AUTO_DELETE_TIME)
        )
        asyncio.create_task(delete_file(track_msgs, client, delete_data))


@Bot.on_message(filters.command('start') & filters.private & subscribed)
async def start_command(client: Client, message: Message):
    user_id = message.from_user.id
    if not await present_user(user_id):
        try:
            await add_user(user_id)
        except:
            pass

    text = message.text
    if len(text) > 7:
        try:
            base64_string = text.split(" ", 1)[1]
        except:
            return

        string = await decode(base64_string)

        # 新格式：资源包 pack-{pack_id}
        if string.startswith("pack-"):
            pack_id = string[5:]
            if pack_exists(pack_id):
                await _deliver_pack(client, message, pack_id)
            else:
                await message.reply_text("❌ 资源包不存在或已过期")
            return

        # 旧格式：get-xxx 或 get-xxx-yyy
        argument = string.split("-")
        if len(argument) == 3:
            try:
                start = int(int(argument[1]) / abs(client.db_channel.id))
                end = int(int(argument[2]) / abs(client.db_channel.id))
            except:
                return
            if start <= end:
                ids = range(start, end + 1)
            else:
                ids = []
                i = start
                while True:
                    ids.append(i)
                    i -= 1
                    if i < end:
                        break
        elif len(argument) == 2:
            try:
                ids = [int(int(argument[1]) / abs(client.db_channel.id))]
            except:
                return
        else:
            return

        await _deliver_legacy(client, message, ids)
        return
    else:
        reply_markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📦 了解小芽空投机", callback_data="about"),
                    InlineKeyboardButton("❌ 关闭", callback_data="close")
                ]
            ]
        )
        if START_PIC:
            await message.reply_photo(
                photo=START_PIC,
                caption=START_MSG.format(
                    first=message.from_user.first_name,
                    last=message.from_user.last_name,
                    username=None if not message.from_user.username else '@' + message.from_user.username,
                    mention=message.from_user.mention,
                    id=message.from_user.id
                ),
                reply_markup=reply_markup,
                quote=True
            )
        else:
            await message.reply_text(
                text=START_MSG.format(
                    first=message.from_user.first_name,
                    last=message.from_user.last_name,
                    username=None if not message.from_user.username else '@' + message.from_user.username,
                    mention=message.from_user.mention,
                    id=message.from_user.id
                ),
                reply_markup=reply_markup,
                disable_web_page_preview=True,
                quote=True
            )
        return


#=====================================================================================##

WAIT_MSG = """<b>✨ 正在处理中...</b>"""

REPLY_ERROR = """<code>请回复一条消息来使用此命令（不要带空格）</code>"""

#=====================================================================================##


@Bot.on_message(filters.command('start') & filters.private)
async def not_joined(client: Client, message: Message):

    if bool(JOIN_REQUEST_ENABLE):
        invite = await client.create_chat_invite_link(
            chat_id=FORCE_SUB_CHANNEL,
            creates_join_request=True
        )
        ButtonUrl = invite.invite_link
    else:
        ButtonUrl = client.invitelink

    buttons = [
        [
            InlineKeyboardButton(
                "📢 加入频道",
                url=ButtonUrl)
        ]
    ]

    try:
        buttons.append(
            [
                InlineKeyboardButton(
                    text='✅ 已加入，点我重试',
                    url=f"https://t.me/{client.username}?start={message.command[1]}"
                )
            ]
        )
    except IndexError:
        pass

    await message.reply(
        text=FORCE_MSG.format(
            first=message.from_user.first_name,
            last=message.from_user.last_name,
            username=None if not message.from_user.username else '@' + message.from_user.username,
            mention=message.from_user.mention,
            id=message.from_user.id
        ),
        reply_markup=InlineKeyboardMarkup(buttons),
        quote=True,
        disable_web_page_preview=True
    )

@Bot.on_message(filters.command('users') & filters.private & filters.user(ADMINS))
async def get_users(client: Bot, message: Message):
    msg = await client.send_message(chat_id=message.chat.id, text=WAIT_MSG)
    users = await full_userbase()
    await msg.edit(f"📊 当前共有 {len(users)} 位用户使用小芽空投机")

@Bot.on_message(filters.private & filters.command('broadcast') & filters.user(ADMINS))
async def send_text(client: Bot, message: Message):
    if message.reply_to_message:
        query = await full_userbase()
        broadcast_msg = message.reply_to_message
        total = 0
        successful = 0
        blocked = 0
        deleted = 0
        unsuccessful = 0

        pls_wait = await message.reply("<i>📣 正在群发消息，这可能需要一点时间...</i>")
        for chat_id in query:
            try:
                await broadcast_msg.copy(chat_id)
                successful += 1
            except FloodWait as e:
                await asyncio.sleep(e.x)
                await broadcast_msg.copy(chat_id)
                successful += 1
            except UserIsBlocked:
                await del_user(chat_id)
                blocked += 1
            except InputUserDeactivated:
                await del_user(chat_id)
                deleted += 1
            except:
                unsuccessful += 1
                pass
            total += 1

        status = f"""<b><u>📣 群发完成</u>

总用户数： <code>{total}</code>
成功发送： <code>{successful}</code>
已屏蔽 Bot： <code>{blocked}</code>
已注销账号： <code>{deleted}</code>
发送失败： <code>{unsuccessful}</code></b>"""

        return await pls_wait.edit(status)

    else:
        msg = await message.reply(REPLY_ERROR)
        await asyncio.sleep(8)
        await msg.delete()
