import redis.asyncio as redis
from datetime import datetime

class BotHeartbeat:
    def __init__(self, host='localhost', port=6379, db=2):
        self.redis_client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
        self.key = "baikal_bot_last_seen"

    async def ping(self):
        """Бот вызывает этот метод, чтобы подтвердить, что он онлайн"""
        # Устанавливаем значение (текущее время) и TTL 120 секунд
        # Если бот не пришлет пинг в течение 2 минут, ключ исчезнет
        await self.redis_client.set(self.key, datetime.now().isoformat(), ex=120)

    async def is_alive(self) -> bool:
        """Админка вызывает этот метод, чтобы проверить статус"""
        exists = await self.redis_client.exists(self.key)
        return bool(exists)

    async def get_last_seen(self):
        """Дополнительно: когда был последний пинг"""
        return await self.redis_client.get(self.key)