import uvicorn
from fastapi import FastAPI, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta, timezone

from database import get_db
from models import ErrorLog
from heartbeat import BotHeartbeat

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
hb = BotHeartbeat(host='localhost', port=6379, db=2)

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

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)