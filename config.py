#(©)CodeXBotz

import os
import logging
from dotenv import load_dotenv
from logging.handlers import RotatingFileHandler

load_dotenv()

#Bot token @Botfather
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "8714950601:AAHJyeekNJ5EovgA7SEjm4XIbFf3iU3W2kU")

#Your API ID from my.telegram.org
APP_ID = int(os.environ.get("APP_ID", "22281474"))

#Your API Hash from my.telegram.org
API_HASH = os.environ.get("API_HASH", "5c1e92b92eaff11128f7be19abb64adb")

#Your db channel Id
CHANNEL_ID = int(os.environ.get("CHANNEL_ID", "-1002046956425"))

#OWNER ID
OWNER_ID = int(os.environ.get("OWNER_ID", "1861667385"))

#Port
PORT = os.environ.get("PORT", "18688")

#Database (MySQL)
MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
MYSQL_PORT = int(os.environ.get("MYSQL_PORT", "3306"))
MYSQL_USER = os.environ.get("MYSQL_USER", "xiaoyaairdrop")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "L3Ht7WJJmdAjDF6h")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "xiaoyaairdrop")

# 强制关注频道 ID
FORCE_SUB_CHANNEL = int(os.environ.get("FORCE_SUB_CHANNEL", "-1001684212282"))
JOIN_REQUEST_ENABLE = os.environ.get("JOIN_REQUEST_ENABLED", "True")

TG_BOT_WORKERS = int(os.environ.get("TG_BOT_WORKERS", "4"))

#start message
START_PIC = os.environ.get("START_PIC","https://api.minio.1yo.cc/nebuluxe/halosparkpix/IMG_0950.webp")
START_MSG = os.environ.get("START_MESSAGE", "✨ 嗨 {first}，欢迎使用【小芽空投机】！\n\n🎯 发送提货口令即可一键领取您的专属资源。\n📦 支持图片、视频、文档等全类型文件闪电到手。")
try:
    ADMINS=[]
    for x in (os.environ.get("ADMINS", "").split()):
        ADMINS.append(int(x))
except ValueError:
        raise Exception("管理员列表配置异常：包含非整数值，请检查 ADMINS 环境变量。")

#Force sub message 
FORCE_MSG = os.environ.get("FORCE_SUB_MESSAGE", "嗨 {first}\n\n<b>请先加入我们的频道才能使用小芽空投机哦~\n\n👇 点击下方按钮加入频道</b>")

# 自定义文件描述（设为 None 则不覆盖原描述）
CUSTOM_CAPTION = os.environ.get("CUSTOM_CAPTION", "")

# 防止用户转发 Bot 发送的文件（True = 开启防盗转）
PROTECT_CONTENT = True if os.environ.get('PROTECT_CONTENT', "True") == "True" else False

# 自动销毁倒计时（秒），设为 0 则不自动删除
AUTO_DELETE_TIME = int(os.getenv("AUTO_DELETE_TIME", "600"))
AUTO_DELETE_MSG = os.environ.get("AUTO_DELETE_MSG", "⏳ 注意！该文件将在 {time} 秒后自动销毁")
AUTO_DEL_SUCCESS_MSG = os.environ.get("AUTO_DEL_SUCCESS_MSG", "💨 文件已自动销毁~空投完成，感谢使用小芽空投机！")

# 是否禁用频道帖子的分享按钮
DISABLE_CHANNEL_BUTTON = os.environ.get("DISABLE_CHANNEL_BUTTON", True) == 'True'

# 存储 Session 配置
STORE_SESSION_TIMEOUT = int(os.getenv("STORE_SESSION_TIMEOUT", "50"))  # 10分钟无操作自动关闭
STORE_ALBUM_WAIT = float(os.getenv("STORE_ALBUM_WAIT", "0.5"))         # 相册消息合并等待秒数

BOT_STATS_TEXT = "<b>🤖 小芽空投机运行状态</b>\n⏱ 已持续运行：{uptime}"
USER_REPLY_TEXT = "📦 我是【小芽空投机】，请发送提货口令来领取您的资源~"

ADMINS.append(OWNER_ID)

LOG_FILE_NAME = "filesharingbot.txt"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s",
    datefmt='%d-%b-%y %H:%M:%S',
    handlers=[
        RotatingFileHandler(
            LOG_FILE_NAME,
            maxBytes=50000000,
            backupCount=10
        ),
        logging.StreamHandler()
    ]
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)

def LOGGER(name: str) -> logging.Logger:
    return logging.getLogger(name)
