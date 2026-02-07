import os
import httpx
import uvicorn

from fastapi import FastAPI, Request, Depends, Body
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta, timezone

from database import get_db
from models import ErrorLog
from heartbeat import BotHeartbeat
from dotenv import load_dotenv

app = FastAPI()
load_dotenv()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
hb = BotHeartbeat(host='localhost', port=6379, db=2)
BOT_CORE_URL = os.getenv("BOT_CORE_URL")

async def is_bot_online_redis():
    return await hb.is_alive()

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    bot_online = await is_bot_online_redis()
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "active_page": "dashboard", "bot_online": bot_online
    })

@app.get("/logs", response_class=HTMLResponse)
async def view_logs(request: Request, db: AsyncSession = Depends(get_db)):
    bot_online = await is_bot_online_redis()
    # Берем последние 50 ошибок для полноты картины
    query = select(ErrorLog).order_by(ErrorLog.created_at.desc()).limit(50)
    result = await db.execute(query)
    errors = result.scalars().all()
    
    return templates.TemplateResponse("logs.html", {
        "request": request, "errors": errors, "active_page": "logs", "bot_online": bot_online
    })

@app.get("/logs/stats", response_class=HTMLResponse)
async def view_stats(request: Request, db: AsyncSession = Depends(get_db)):
    bot_online = await is_bot_online_redis()
    return templates.TemplateResponse("stats.html", {
        "request": request, "active_page": "logs", "bot_online": bot_online
    })

@app.get("/bot-status")
async def get_bot_status_api():
    online = await hb.is_alive()
    return {"online": online}

@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    bot_online = await hb.is_alive()
    return templates.TemplateResponse("chat.html", {
        "request": request, 
        "active_page": "chat", 
        "bot_online": bot_online
    })

@app.post("/chat/ask")
async def proxy_to_core(data: dict = Body(...)):
    query = data.get("text")
    user_id = data.get("user_id", "admin_web_interface")

    # Увеличиваем таймаут до 2 минут (для тяжелых моделей)
    timeout = httpx.Timeout(120.0, connect=60.0) 
    
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.post(
                BOT_CORE_URL,
                json={
                    "query": query,
                    "user_id": user_id,
                    "settings": {"mode": "gigachat"}
                }
            )
            # Логируем ответ для отладки в консоли админки
            print(f"DEBUG: Core API returned: {response.text}")
            return response.json()
        except Exception as e:
            print(f"ERROR in proxy_to_core: {str(e)}")
            # Возвращаем структуру, которую поймет JS
            return [{"type": "text", "content": f"❌ Ошибка на стороне бэкенда админки: {str(e)}"}]

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)