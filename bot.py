# 小芽空投机 —— Telegram 文件分享机器人

from aiohttp import web
from plugins import web_server

import pyromod.listen
from pyrogram import Client
from pyrogram.enums import ParseMode
from pyrogram.types import BotCommand
import sys
from datetime import datetime

from config import API_HASH, APP_ID, LOGGER, TG_BOT_TOKEN, TG_BOT_WORKERS, FORCE_SUB_CHANNEL, CHANNEL_ID, PORT


ascii_art = """
░█████╗░░█████╗░██████╗░███████╗██╗░░██╗██████╗░░█████╗░████████╗███████╗
██╔══██╗██╔══██╗██╔══██╗██╔════╝╚██╗██╔╝██╔══██╗██╔══██╗╚══██╔══╝╚════██║
██║░░╚═╝██║░░██║██║░░██║█████╗░░░╚███╔╝░██████╦╝██║░░██║░░░██║░░░░░███╔═╝
██║░░██╗██║░░██║██║░░██║██╔══╝░░░██╔██╗░██╔══██╗██║░░██║░░░██║░░░██╔══╝░░
╚█████╔╝╚█████╔╝██████╔╝███████╗██╔╝╚██╗██████╦╝╚█████╔╝░░░██║░░░███████╗
░╚════╝░░╚════╝░╚═════╝░╚══════╝╚═╝░░╚═╝╚═════╝░░╚════╝░░░░╚═╝░░░╚══════╝
"""

class Bot(Client):
    def __init__(self):
        super().__init__(
            name="Bot",
            api_hash=API_HASH,
            api_id=APP_ID,
            plugins={
                "root": "plugins"
            },
            workers=TG_BOT_WORKERS,
            bot_token=TG_BOT_TOKEN
        )
        self.LOGGER = LOGGER

    async def start(self):
        await super().start()
        usr_bot_me = await self.get_me()
        self.uptime = datetime.now()

        # ====== 刷新 TG 命令菜单（覆盖旧 Bot 残留的命令） ======
        try:
            await self.set_bot_commands([
                BotCommand("start", "🤖 启动机器人"),
                BotCommand("store", "🎒 开始存储内容"),
                BotCommand("stats", "📊 查看运行状态"),
            ])
            self.LOGGER(__name__).info("✅ 命令菜单已刷新")
        except Exception as e:
            self.LOGGER(__name__).warning(f"⚠️ 命令菜单刷新失败: {e}")

        # ====== 强制关注频道校验（失败不退出，降级为无限制模式） ======
        if FORCE_SUB_CHANNEL:
            try:
                link = (await self.get_chat(FORCE_SUB_CHANNEL)).invite_link
                if not link:
                    await self.export_chat_invite_link(FORCE_SUB_CHANNEL)
                    link = (await self.get_chat(FORCE_SUB_CHANNEL)).invite_link
                self.invitelink = link
                self.LOGGER(__name__).info(f"✅ 强制关注频道已绑定: {link}")
            except Exception as a:
                self.LOGGER(__name__).warning(f"⚠️ 强制关注频道配置异常: {a}")
                self.LOGGER(__name__).warning(f"⚠️ 请检查：1) FORCE_SUB_CHANNEL 值是否正确  2) Bot 是否为该频道管理员  3) 是否拥有'通过链接邀请用户'权限")
                self.LOGGER(__name__).warning(f"⚠️ 当前值: {FORCE_SUB_CHANNEL}，已降级为无限制模式继续运行")
                self.invitelink = None
                # 不退出，降级运行

        # ====== 数据库频道校验 ======
        try:
            db_channel = await self.get_chat(CHANNEL_ID)
            self.db_channel = db_channel
            test = await self.send_message(chat_id=db_channel.id, text="🤖 小芽空投机启动检测...")
            await test.delete()
            self.LOGGER(__name__).info(f"✅ 存储频道已连接: {db_channel.title} ({CHANNEL_ID})")
        except Exception as e:
            self.LOGGER(__name__).warning(f"❌ 存储频道连接失败: {e}")
            self.LOGGER(__name__).warning(f"❌ 请检查：1) CHANNEL_ID 值是否正确  2) Bot 是否为该频道管理员")
            self.LOGGER(__name__).warning(f"❌ 当前值: {CHANNEL_ID}，无法继续运行")
            sys.exit()

        self.set_parse_mode(ParseMode.HTML)
        self.username = usr_bot_me.username
        self.LOGGER(__name__).info(f"🚀 小芽空投机已启动！Bot: @{self.username}")

        # Web 健康检查服务
        app = web.AppRunner(await web_server())
        await app.setup()
        bind_address = "0.0.0.0"
        await web.TCPSite(app, bind_address, PORT).start()

    async def stop(self, *args):
        await super().stop()
        self.LOGGER(__name__).info("🛑 小芽空投机已停止")
