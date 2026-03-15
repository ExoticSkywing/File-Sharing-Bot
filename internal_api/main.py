"""
小芽空投机内部 API — 独立 FastAPI 服务
供 Nebuluxe Center Gateway 调用，替代 Gateway 直连数据库。
复用精灵模式：md5 签名认证 + 独立域名。

启动:
    cd File-Sharing-Bot/internal_api
    python3 -m uvicorn main:app --host 127.0.0.1 --port 18690
"""

import sys
import os

# 将 Bot 根目录加入 Python 路径，以便 import config / database
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
from fastapi import FastAPI
from routes_packs import router as packs_router
from routes_tags import router as tags_router

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s %(levelname)s] %(name)s - %(message)s",
)

app = FastAPI(
    title="小芽空投机内部 API",
    description="供 Nebuluxe Center API Gateway 调用的数据服务",
    version="1.0.0",
)

app.include_router(packs_router)
app.include_router(tags_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "airdrop-internal-api"}


if __name__ == "__main__":
    import uvicorn
    from config import INTERNAL_API_PORT

    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=INTERNAL_API_PORT,
        log_level="info",
    )
