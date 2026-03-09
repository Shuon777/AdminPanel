from sqlalchemy import Column, Integer, String, Text, DateTime, Float, Boolean, ForeignKey, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# --- СЛУЖЕБНЫЕ ТАБЛИЦЫ ---

class ErrorLog(Base):
    __tablename__ = "error_log"
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user_query = Column(Text)
    error_message = Column(Text, nullable=False)
    context = Column(JSONB)
    additional_info = Column(JSONB)


# ==========================================
# ИНФОРМАЦИОННАЯ МОДЕЛЬ: <Описание объекта>
# ==========================================

class BiologicalEntity(Base):
    """Объект флоры и фауны (ОФФ)"""
    __tablename__ = "biological_entity"
    id = Column(Integer, primary_key=True, index=True)
    common_name_ru = Column(String(500), nullable=False, index=True)
    scientific_name = Column(String(500), index=True)
    description = Column(Text)
    status = Column(String(100))
    type = Column(String(100))
    feature_data = Column(JSONB) # Здесь хранятся <Признак ресурса> специфичные для объекта


# ==========================================
# ИНФОРМАЦИОННАЯ МОДЕЛЬ: <Описание модальности>
# ==========================================

class TextContent(Base):
    """Текстовая модальность"""
    __tablename__ = "text_content"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500))
    content = Column(Text)
    structured_data = Column(JSONB) # <Метаданные сопровождения> (параметры для обработки)
    description = Column(Text)
    feature_data = Column(JSONB)    # <Признак ресурса> (онтология: сезон, место и т.д.)
    # Поле embedding (вектор) пока не объявляем в ORM, так как им управляет pgvector,
    # а в CMS мы векторы руками не редактируем.

class ImageContent(Base):
    """Изобразительная модальность"""
    __tablename__ = "image_content"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(Text)      # Ссылка на файл или описание
    feature_data = Column(JSONB)    # <Признак ресурса> (онтология: ракурс, сезон на фото)


# ==========================================
# СВЯЗУЮЩЕЕ ЗВЕНО (Сборка <Ресурса>)
# ==========================================

class EntityRelation(Base):
    """
    Таблица, объединяющая Объект и Модальность в единый Ресурс.
    Например: source_id=1 (Нерпа), source_type='biological_entity', 
              target_id=10 (Текст), target_type='text_content', relation_type='has_text'
    """
    __tablename__ = "entity_relation"
    source_id = Column(Integer, primary_key=True)
    source_type = Column(String(30), primary_key=True)
    target_id = Column(Integer, primary_key=True)
    target_type = Column(String(30), primary_key=True)
    relation_type = Column(String(50), nullable=False)

# ==========================================
# ИДЕНТИФИКАТОРЫ (Хранилище ссылок и путей)
# ==========================================

class EntityIdentifier(Base):
    __tablename__ = "entity_identifier"
    id = Column(Integer, primary_key=True, index=True)
    url = Column(String(1000))
    db_path = Column(String(500))
    file_path = Column(String(500))
    name_ru = Column(String(500))
    name_en = Column(String(500))
    name_latin = Column(String(500))

class EntityIdentifierLink(Base):
    __tablename__ = "entity_identifier_link"
    entity_id = Column(Integer, primary_key=True)
    entity_type = Column(String(30), primary_key=True)
    identifier_id = Column(Integer, primary_key=True)