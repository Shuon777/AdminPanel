import os
import httpx
import uvicorn

from fastapi import FastAPI, Request, Depends, Body, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
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
app.add_middleware(SessionMiddleware, secret_key="super-secret-key-for-admins")
BOT_CORE_URL = os.getenv("BOT_CORE_URL")

async def is_bot_online_redis():
    return await hb.is_alive()

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login")
    bot_online = await is_bot_online_redis()
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "active_page": "dashboard", "bot_online": bot_online
    })

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@app.post("/login")
async def login(request: Request, username: str = Form(...)):
    # Здесь можно добавить проверку пароля, но пока просто верим на слово
    request.session["user_id"] = f"admin_{username}"
    return RedirectResponse(url="/", status_code=303)

@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login")

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
    # ПРОВЕРКА:
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login")
    
    bot_online = await hb.is_alive()
    return templates.TemplateResponse("chat.html", {"request": request, "active_page": "chat", "bot_online": bot_online})

@app.post("/chat/ask")
async def proxy_to_core(request: Request, data: dict = Body(...)):
    user_id = request.session.get("user_id")
    if not user_id:
        return [{"type": "text", "content": "❌ Ошибка: вы не авторизованы"}]

    query = data.get("text")
    settings = data.get("settings", {}) # Принимаем настройки с фронта

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
        try:
            response = await client.post(
                BOT_CORE_URL,
                json={
                    "query": query,
                    "user_id": user_id, # Теперь ID уникален для каждого админа
                    "settings": settings
                }
            )
            import json
            try:
                # Пытаемся вывести красиво отформатированный JSON
                raw_data = response.json()
                print("\n=== [CORE API RESPONSE START] ===")
                print(json.dumps(raw_data, indent=2, ensure_ascii=False))
                print("=== [CORE API RESPONSE END] ===\n")
            except Exception:
                # Если это не JSON, выводим просто текст
                print(f"\n!!! [RAW TEXT RESPONSE]: {response.text}\n")
            return response.json()
        except Exception as e:
            return [{"type": "text", "content": f"❌ Ошибка Core API: {str(e)}"}]

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)