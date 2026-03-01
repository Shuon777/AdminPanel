from sqlalchemy import Column, Integer, Text, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()

class ErrorLog(Base):
    __tablename__ = "error_log"
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user_query = Column(Text)
    error_message = Column(Text)
    context = Column(JSONB)
    additional_info = Column(JSONB)