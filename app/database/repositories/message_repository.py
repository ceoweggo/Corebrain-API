from typing import List, Optional, Dict, Any
from app.database.repositories.base_repository import BaseRepository
from app.models.message import MessageInDB

class MessageRepository(BaseRepository[MessageInDB]):
    """
    Repositorio para operaciones con mensajes
    """
    
    def __init__(self, db):
        super().__init__(db, "messages", MessageInDB)
    
    async def find_by_conversation_id(self, conversation_id: str, limit: int = 50) -> List[MessageInDB]:
        """
        Busca mensajes por ID de conversación
        """
        return await self.find_many(
            {"conversation_id": conversation_id},
            limit=limit,
            skip=0
        )
    
    async def find_by_conversation_and_user(self, conversation_id: str, user_id: str, limit: int = 50) -> List[MessageInDB]:
        """
        Busca mensajes por ID de conversación y usuario
        """
        return await self.find_many(
            {"conversation_id": conversation_id, "user_id": user_id},
            limit=limit,
            skip=0
        )
    
    async def find_latest_messages(self, limit: int = 10) -> List[MessageInDB]:
        """
        Obtiene los mensajes más recientes
        """
        cursor = self.collection.find().sort("created_at", -1).limit(limit)
        documents = await cursor.to_list(length=limit)
        return [self.model_class(**document) for document in documents]