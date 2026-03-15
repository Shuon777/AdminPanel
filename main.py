import os
import httpx
import uvicorn
import requests
import folium
from shapely import wkb
import json
from sqlalchemy import func

from fastapi import FastAPI, Request, Depends, Body, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, delete
from urllib.parse import urlparse
from datetime import datetime, timedelta, timezone

from database import get_db
from models import ErrorLog, BiologicalEntity, TextContent, ImageContent, EntityRelation, EntityIdentifier, EntityIdentifierLink, GeographicalEntity, EntityGeo, MapContent
from heartbeat import BotHeartbeat
from dotenv import load_dotenv

app = FastAPI()
load_dotenv()

app.mount("/admin/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
hb = BotHeartbeat(host='localhost', port=6379, db=2)
app.add_middleware(SessionMiddleware, secret_key="super-secret-key-for-admins")
BOT_CORE_URL = os.getenv("BOT_CORE_URL")
parsed_url = urlparse(BOT_CORE_URL)
CORE_API_BASE = f"{parsed_url.scheme}://{parsed_url.netloc}" # Получится http://localhost:5001

async def is_bot_online_redis():
    return await hb.is_alive()


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login")

    bot_online = await is_bot_online_redis()

    # --- Считаем ошибки за последние 24 часа ---
    time_24h_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    query = select(func.count(ErrorLog.id)).where(ErrorLog.created_at >= time_24h_ago)
    result = await db.execute(query)
    errors_24h = result.scalar() or 0  # Получаем число (или 0, если пусто)
    # -------------------------------------------
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "active_page": "dashboard",
        "bot_online": bot_online,
        "errors_24h": errors_24h  # <--- Передаем число в шаблон
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


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login")

    bot_online = await is_bot_online_redis()
    prompts = {}
    config = {}

    # Стучимся в Core API бота, чтобы забрать текущие промпты и конфиг
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            p_resp = await client.get(f"{CORE_API_BASE}/prompts")
            if p_resp.status_code == 200:
                prompts = p_resp.json()

            c_resp = await client.get(f"{CORE_API_BASE}/config")
            if c_resp.status_code == 200:
                config = c_resp.json()
        except Exception as e:
            print(f"Ошибка загрузки настроек из бота: {e}")

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "active_page": "settings",
        "bot_online": bot_online,
        "prompts": prompts,
        "config": config
    })


@app.post("/settings/prompts")
async def save_prompts(request: Request, data: dict = Body(...)):
    """Отправляем измененные промпты обратно в бота"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{CORE_API_BASE}/prompts", json=data)
        if resp.status_code == 200:
            return resp.json()
        raise HTTPException(status_code=500, detail="Ошибка сохранения промптов")


@app.post("/settings/config")
async def save_config(request: Request, data: dict = Body(...)):
    """Отправляем измененный конфиг (.env) обратно в бота"""
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(f"{CORE_API_BASE}/config", json=data)
        if resp.status_code == 200:
            return resp.json()
        raise HTTPException(status_code=500, detail="Ошибка сохранения конфига")

# ==========================================
# CMS: ФЛОРА И ФАУНА (MVP)
# ==========================================

@app.get("/biological", response_class=HTMLResponse)
async def biological_list(request: Request, db: AsyncSession = Depends(get_db)):
    """Вывод списка всех биологических объектов"""
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login")
        
    bot_online = await is_bot_online_redis()
    
    # Получаем объекты из БД, отсортированные по убыванию ID
    query = select(BiologicalEntity).order_by(BiologicalEntity.id.desc())
    result = await db.execute(query)
    entities = result.scalars().all()
    
    return templates.TemplateResponse("biological_list.html", {
        "request": request, 
        "active_page": "biological", 
        "bot_online": bot_online, 
        "entities": entities
    })

@app.get("/biological/new", response_class=HTMLResponse)
async def biological_new(request: Request):
    """Страница с формой создания нового объекта"""
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login")
        
    bot_online = await is_bot_online_redis()
    return templates.TemplateResponse("biological_form.html", {
        "request": request, 
        "active_page": "biological", 
        "bot_online": bot_online, 
        "entity": None # Передаем None, так как это создание, а не редактирование
    })

@app.post("/biological/save")
async def biological_save(
    request: Request, 
    common_name_ru: str = Form(...),
    scientific_name: str = Form(""),
    type: str = Form(...),
    status: str = Form(""),
    description: str = Form(""),
    db: AsyncSession = Depends(get_db)
):
    """Сохранение базового <Описания объекта> в базу"""
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login")
        
    # 1. Создаем сам объект (ОФФ)
    new_entity = BiologicalEntity(
        common_name_ru=common_name_ru,
        scientific_name=scientific_name,
        type=type,
        status=status,
        description=description,
        feature_data={} # Пустой JSONB, сюда потом лягут <Признаки ресурса>
    )
    db.add(new_entity)
    await db.commit()
    
    # После создания возвращаем пользователя к списку
    return RedirectResponse(url=f"/biological/{new_entity.id}", status_code=303)

@app.post("/biological/{entity_id}/add_text")
async def biological_add_text(
    request: Request,
    entity_id: int,
    title: str = Form(...),
    content: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """
    РЕАЛИЗАЦИЯ ИНФОРМАЦИОННОЙ МОДЕЛИ:
    Сборка <Ресурса> = <Объект> + <Модальность> + <Связь>
    """
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login")
        
    # 1. Создаем <Описание модальности> (Текст)
    new_text = TextContent(
        title=title, 
        content=content, 
        feature_data={}
    )
    db.add(new_text)
    await db.flush() # Получаем ID нового текста (new_text.id) без полного коммита транзакции
    
    # 2. Создаем связующее звено
    relation = EntityRelation(
        source_id=new_text.id,
        source_type="text_content",
        target_id=entity_id,
        target_type="biological_entity",
        relation_type="описание объекта"
    )
    db.add(relation)
    await db.commit()
    
    return RedirectResponse(url=f"/biological/{entity_id}", status_code=303)

@app.get("/biological/{entity_id}", response_class=HTMLResponse)
async def biological_edit(request: Request, entity_id: int, db: AsyncSession = Depends(get_db)):
    """Карточка объекта: просмотр и управление связанными ресурсами"""
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login")
        
    bot_online = await is_bot_online_redis()
    
    result = await db.execute(select(BiologicalEntity).where(BiologicalEntity.id == entity_id))
    entity = result.scalars().first()
    
    if not entity:
        raise HTTPException(status_code=404, detail="Объект не найден")

    # ИСПРАВЛЕНО: Теперь ищем от текста (source) к объекту (target)
    text_query = (
        select(TextContent)
        .join(EntityRelation, (EntityRelation.source_id == TextContent.id) & (EntityRelation.source_type == 'text_content'))
        .where((EntityRelation.target_id == entity_id) & (EntityRelation.target_type == 'biological_entity'))
    )
    texts = (await db.execute(text_query)).scalars().all()

    # ИСПРАВЛЕНО: Теперь ищем от картинки (source) к объекту (target)
    image_query = (
        select(ImageContent, EntityIdentifier.file_path)
        .join(EntityRelation, (EntityRelation.source_id == ImageContent.id) & (EntityRelation.source_type == 'image_content'))
        .outerjoin(EntityIdentifierLink, (EntityIdentifierLink.entity_id == ImageContent.id) & (EntityIdentifierLink.entity_type == 'image_content'))
        .outerjoin(EntityIdentifier, EntityIdentifier.id == EntityIdentifierLink.identifier_id)
        .where((EntityRelation.target_id == entity_id) & (EntityRelation.target_type == 'biological_entity'))
    )
    images_result = await db.execute(image_query)
    
    # Формируем удобный список словарей для шаблона: [{"data": ImageContent, "url": "https..."}]
    images =[{"data": img, "url": url} for img, url in images_result.all()]

    geo_links_query = (
        select(EntityGeo)
        .where((EntityGeo.entity_id == entity_id) & (EntityGeo.entity_type == 'biological_entity'))
    )
    links = (await db.execute(geo_links_query)).scalars().all()
    
    locations = []

    for link in links:
        geo_id = link.geographical_entity_id

        # 1. Пытаемся найти географическую сущность (для названия)
        geo_res = await db.execute(select(GeographicalEntity).where(GeographicalEntity.id == geo_id))
        geo_obj = geo_res.scalars().first()
        
        # 2. Пытаемся найти карту (для геометрии)
        map_res = await db.execute(select(MapContent).where(MapContent.id == geo_id))
        map_obj = map_res.scalars().first()

        if geo_obj or map_obj:
            location_item = {
                "name_ru": geo_obj.name_ru if geo_obj else (map_obj.title if map_obj else "Без названия"),
                "type": geo_obj.type if geo_obj else "Карта",
                "is_map": bool(map_obj),
            }
            if map_obj:
                location_item["map_id"] = map_obj.id   # для загрузки карты
            locations.append(location_item)
            
    return templates.TemplateResponse("biological_edit.html", {
        "request": request, 
        "active_page": "biological", 
        "bot_online": bot_online, 
        "entity": entity,
        "texts": texts,
        "images": images,
        "locations": locations
    })

@app.post("/biological/{entity_id}/add_image")
async def biological_add_image(
    request: Request,
    entity_id: int,
    title: str = Form(...),
    image_url: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """
    Добавление изображения с сохранением названий объекта в entity_identifier
    """
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login")
        
    # 1. Сначала достаем информацию об объекте, чтобы узнать его названия
    result = await db.execute(select(BiologicalEntity).where(BiologicalEntity.id == entity_id))
    entity = result.scalars().first()
    
    if not entity:
        raise HTTPException(status_code=404, detail="Объект не найден")

    # 2. Создаем ImageContent
    new_image = ImageContent(
        title=title,
        description="Добавлено через админ-панель",
        feature_data={"source": "Admin Panel"} 
    )
    db.add(new_image)
    await db.flush() 
    
    # 3. Привязываем картинку к биологическому объекту (relation)
    relation = EntityRelation(
        source_id=new_image.id,
        source_type="image_content",
        target_id=entity_id,
        target_type="biological_entity",
        relation_type="изображение объекта"
    )
    db.add(relation)

    # 4. Создаем запись в entity_identifier
    # ИСПОЛЬЗУЕМ ДАННЫЕ ИЗ entity, КОТОРЫЕ ДОСТАЛИ ВЫШЕ
    new_identifier = EntityIdentifier(
        file_path=image_url,
        name_ru=entity.common_name_ru,    # Название из карточки объекта
        name_latin=entity.scientific_name # Латынь из карточки объекта
    )
    db.add(new_identifier)
    await db.flush() 

    # 5. Связываем картинку с её новым идентификатором
    identifier_link = EntityIdentifierLink(
        entity_id=new_image.id,
        entity_type="image_content",
        identifier_id=new_identifier.id
    )
    db.add(identifier_link)

    await db.commit() 
    
    return RedirectResponse(url=f"/biological/{entity_id}", status_code=303)

@app.post("/resource/delete/image/{image_id}")
async def delete_image_resource(
    request: Request,
    image_id: int,
    entity_id: int = Form(...), # Чтобы знать, куда вернуться
    db: AsyncSession = Depends(get_db)
):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login")

    # 1. Удаляем связи из entity_relation
    await db.execute(delete(EntityRelation).where(
        (EntityRelation.source_id == image_id) & (EntityRelation.source_type == 'image_content')
    ))

    # 2. Находим и удаляем идентификаторы (ссылки)
    # Сначала найдем ID идентификатора через линк
    link_result = await db.execute(select(EntityIdentifierLink).where(
        (EntityIdentifierLink.entity_id == image_id) & (EntityIdentifierLink.entity_type == 'image_content')
    ))
    links = link_result.scalars().all()
    
    for link in links:
        await db.execute(delete(EntityIdentifier).where(EntityIdentifier.id == link.identifier_id))
    
    # 3. Удаляем сами линки
    await db.execute(delete(EntityIdentifierLink).where(
        (EntityIdentifierLink.entity_id == image_id) & (EntityIdentifierLink.entity_type == 'image_content')
    ))

    # 4. Удаляем саму запись ImageContent
    await db.execute(delete(ImageContent).where(ImageContent.id == image_id))

    await db.commit()
    return RedirectResponse(url=f"/biological/{entity_id}", status_code=303)

@app.post("/resource/delete/text/{text_id}")
async def delete_text_modality(
    request: Request,
    text_id: int,
    entity_id: int = Form(...),
    db: AsyncSession = Depends(get_db)
):
    if not request.session.get("user_id"):
        return RedirectResponse(url="/login")

    # 1. Удаляем связь из entity_relation (разрываем связку Ресурса)
    await db.execute(delete(EntityRelation).where(
        (EntityRelation.source_id == text_id) & (EntityRelation.source_type == 'text_content')
    ))

    # 2. Удаляем саму текстовую модальность из text_content
    await db.execute(delete(TextContent).where(TextContent.id == text_id))

    await db.commit()
    return RedirectResponse(url=f"/biological/{entity_id}", status_code=303)

@app.post("/biological/get-map-html")
async def get_map_html(
    data: dict = Body(...),
    db: AsyncSession = Depends(get_db)
):
    map_id = data.get("map_id")
    
    result = await db.execute(select(MapContent).where(MapContent.id == map_id))
    map_obj = result.scalars().first()
    
    if not map_obj:
        return {"html": "<p>Карта не найдена</p>"}

    # Преобразуем геометрию в GeoJSON (если поле geometry — WKBElement)
    geojson_data = await db.scalar(select(func.ST_AsGeoJSON(map_obj.geometry)))
    if not geojson_data:
        return {"html": "<p>Геометрия отсутствует</p>"}
    
    geometry_geojson = json.loads(geojson_data)
    
    m = folium.Map(location=[53.2, 107.3], zoom_start=9, tiles="OpenStreetMap")
    folium.GeoJson(geometry_geojson).add_to(m)
    
    return {"html": m._repr_html_()}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)