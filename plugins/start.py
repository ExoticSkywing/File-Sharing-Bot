#(©)CodeXBotz

import os
import asyncio
from pyrogram import Client, filters
from pyrogram.enums import ParseMode
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import FloodWait, UserIsBlocked, InputUserDeactivated

from bot import Bot
from config import ADMINS, FORCE_MSG, START_MSG, CUSTOM_CAPTION, DISABLE_CHANNEL_BUTTON, PROTECT_CONTENT, START_PIC, AUTO_DELETE_TIME, AUTO_DELETE_MSG, JOIN_REQUEST_ENABLE,FORCE_SUB_CHANNEL
from helper_func import subscribed,decode, get_messages, delete_file
from database.database import add_user, del_user, full_userbase, present_user, get_pack_items


@Bot.on_message(filters.command('start') & filters.private & subscribed)
async def start_command(client: Client, message: Message):
    id = message.from_user.id
    if not await present_user(id):
        try:
            await add_user(id)
        except:
            pass
    text = message.text
    if len(text)>7:
        try:
            base64_string = text.split(" ", 1)[1]
        except:
            return
        string = await decode(base64_string)
        argument = string.split("-")
        
        # ======== 新版资源包处理 ========
        if argument[0] == "pack" and len(argument) == 2:
            pack_id = argument[1]
            pack_items = await get_pack_items(pack_id)
            if not pack_items:
                await message.reply_text("❌ 资源包无效或为空~")
                return
            
            temp_msg = await message.reply("✨ 资源正在空投中，请稍候...")
            track_msgs = []
            
            # 使用从 DB 读出的列表，由于可能有相册，需要分组处理
            # 缓冲字典：{ media_group_id: [ message_ids ] }
            current_media_group = None
            media_group_messages = []
            
            # 处理并发送单条或成组消息的内部函数
            async def _process_and_send(msg_list):
                if not msg_list: return
                try:
                    # 我们知道这些都是在同一个 channel_id 中的，因为 DB 只记录本频道或者远程频道
                    channel_id = msg_list[0]['channel_id']
                    # 为确保安全性，我们只发本机器人所在的记录
                    if channel_id != client.db_channel.id:
                        # 对于外接频道只做普通转发 TODO: 支持非本频道
                        return
                    
                    ids_to_fetch = [m['message_id'] for m in msg_list]
                    msgs = await get_messages(client, ids_to_fetch)
                    
                    # 组装待发的 media list
                    if len(msgs) > 1 and current_media_group:
                        # 相册形式发送 (原版会丢失 caption 这里可以取最后一项填充)
                        # 为了避免 copy 丢失原版属性，我们如果使用 copyMessage 需要逐个。
                        # 这里直接逐个 copy, 等价于单发（若需复原相册形态应转换为 InputMedia）
                        # TODO: 暂时沿用原始逐个 copy 也可以，但我们优化先尝试保持单发逻辑不变，只要顺序对。
                        # 此处简化：只要能取到就逐个推送
                        pass
                    
                    for m in msgs:
                        if bool(CUSTOM_CAPTION) & bool(m.document):
                            if m.caption:
                                caption = f"{m.caption.html}\n\n{CUSTOM_CAPTION.format(previouscaption=m.caption.html, filename=m.document.file_name)}"
                            else:
                                caption = CUSTOM_CAPTION.format(previouscaption="", filename=m.document.file_name)
                        else:
                            caption = "" if not m.caption else m.caption.html

                        if DISABLE_CHANNEL_BUTTON:
                            reply_markup = m.reply_markup
                        else:
                            reply_markup = None
                            
                        try:
                            copied = await m.copy(chat_id=message.from_user.id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup, protect_content=PROTECT_CONTENT)
                            if copied and AUTO_DELETE_TIME > 0:
                                track_msgs.append(copied)
                            await asyncio.sleep(0.5)
                        except FloodWait as e:
                            await asyncio.sleep(e.value)
                            copied = await m.copy(chat_id=message.from_user.id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup, protect_content=PROTECT_CONTENT)
                            if copied and AUTO_DELETE_TIME > 0:
                                track_msgs.append(copied)
                        except Exception:
                            pass
                except Exception as e:
                    print(f"提取出错: {e}")

            # 遍历并处理：
            # (简化的相册处理：为了不打破 copy API 的局限，暂按原版逐个 copy，但保证顺序并统一反馈)
            pending_ids = []
            for item in pack_items:
                pending_ids.append(item)
                
            await _process_and_send(pending_ids)
            
            await temp_msg.delete()
            if track_msgs:
                delete_data = await client.send_message(
                    chat_id=message.from_user.id,
                    text=AUTO_DELETE_MSG.format(time=AUTO_DELETE_TIME)
                )
                asyncio.create_task(delete_file(track_msgs, client, delete_data))
                
                # 二次提醒
                if AUTO_DELETE_TIME > 10:
                    async def send_reminder():
                        await asyncio.sleep(AUTO_DELETE_TIME / 2)
                        await client.send_message(
                            chat_id=message.from_user.id,
                            text=f"⏳ 提醒：剩余约 {int(AUTO_DELETE_TIME/2)} 秒后文件将自动销毁，请尽快保存！"
                        )
                    asyncio.create_task(send_reminder())
            return

        # ======== 兼容旧式命令 get-xxxx ========
        elif argument[0] == "get" and len(argument) == 3:
            try:
                start = int(int(argument[1]) / abs(client.db_channel.id))
                end = int(int(argument[2]) / abs(client.db_channel.id))
            except:
                return
            if start <= end:
                ids = range(start,end+1)
            else:
                ids = []
                i = start
                while True:
                    ids.append(i)
                    i -= 1
                    if i < end:
                        break
        elif argument[0] == "get" and len(argument) == 2:
            try:
                ids = [int(int(argument[1]) / abs(client.db_channel.id))]
            except:
                return
        else:
            return

        temp_msg = await message.reply("✨ 资源正在空投中，请稍候...")
        try:
            messages = await get_messages(client, ids)
        except:
            await message.reply_text("❌ 糟糕，资源提取失败了，请稍后重试~")
            return
        await temp_msg.delete()

        track_msgs = []

        for msg in messages:

            if bool(CUSTOM_CAPTION) & bool(msg.document):
                if msg.caption:
                    caption = f"{msg.caption.html}\n\n{CUSTOM_CAPTION.format(previouscaption=msg.caption.html, filename=msg.document.file_name)}"
                else:
                    caption = CUSTOM_CAPTION.format(previouscaption="", filename=msg.document.file_name)
            else:
                caption = "" if not msg.caption else msg.caption.html

            if DISABLE_CHANNEL_BUTTON:
                reply_markup = msg.reply_markup
            else:
                reply_markup = None

            if AUTO_DELETE_TIME and AUTO_DELETE_TIME > 0:

                try:
                    copied_msg_for_deletion = await msg.copy(chat_id=message.from_user.id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup, protect_content=PROTECT_CONTENT)
                    if copied_msg_for_deletion:
                        track_msgs.append(copied_msg_for_deletion)
                    else:
                        print("Failed to copy message, skipping.")

                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    copied_msg_for_deletion = await msg.copy(chat_id=message.from_user.id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup, protect_content=PROTECT_CONTENT)
                    if copied_msg_for_deletion:
                        track_msgs.append(copied_msg_for_deletion)
                    else:
                        print("Failed to copy message after retry, skipping.")

                except Exception as e:
                    print(f"Error copying message: {e}")
                    pass

            else:
                try:
                    await msg.copy(chat_id=message.from_user.id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup, protect_content=PROTECT_CONTENT)
                    await asyncio.sleep(0.5)
                except FloodWait as e:
                    await asyncio.sleep(e.value)
                    await msg.copy(chat_id=message.from_user.id, caption=caption, parse_mode=ParseMode.HTML, reply_markup=reply_markup, protect_content=PROTECT_CONTENT)
                except:
                    pass

        if track_msgs:
            delete_data = await client.send_message(
                chat_id=message.from_user.id,
                text=AUTO_DELETE_MSG.format(time=AUTO_DELETE_TIME)
            )
            # Schedule the file deletion task after all messages have been copied
            asyncio.create_task(delete_file(track_msgs, client, delete_data))
            
            if AUTO_DELETE_TIME > 10:
                async def send_reminder():
                    await asyncio.sleep(AUTO_DELETE_TIME / 2)
                    await client.send_message(
                        chat_id=message.from_user.id,
                        text=f"⏳ 提醒：剩余约 {int(AUTO_DELETE_TIME/2)} 秒后文件将自动销毁，请尽快保存！"
                    )
                asyncio.create_task(send_reminder())
        else:
            print("No messages to track for deletion.")

        return
    else:
        reply_markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("📦 了解小芽空投机", callback_data = "about"),
                    InlineKeyboardButton("❌ 关闭", callback_data = "close")
                ]
            ]
        )
        if START_PIC:  # Check if START_PIC has a value
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
        else:  # If START_PIC is empty, send only the text
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

WAIT_MSG = """"<b>✨ 正在处理中...</b>"""

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
                url = ButtonUrl)
        ]
    ]

    try:
        buttons.append(
            [
                InlineKeyboardButton(
                    text = '✅ 已加入，点我重试',
                    url = f"https://t.me/{client.username}?start={message.command[1]}"
                )
            ]
        )
    except IndexError:
        pass

    await message.reply(
        text = FORCE_MSG.format(
                first = message.from_user.first_name,
                last = message.from_user.last_name,
                username = None if not message.from_user.username else '@' + message.from_user.username,
                mention = message.from_user.mention,
                id = message.from_user.id
            ),
        reply_markup = InlineKeyboardMarkup(buttons),
        quote = True,
        disable_web_page_preview = True
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

