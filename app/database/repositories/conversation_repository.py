from typing import List, Optional, Dict, Any
from app.database.repositories.base_repository import BaseRepository
from app.models.conversation import ConversationInDB, ConversationUpdate

class ConversationRepository(BaseRepository[ConversationInDB]):
    """
    Repositorio para operaciones con conversaciones
    """
    
    def __init__(self, db):
        super().__init__(db, "conversations", ConversationInDB)
    
    async def find_by_user_id(self, user_id: str, limit: int = 20, skip: int = 0) -> List[ConversationInDB]:
        """
        Busca conversaciones por ID de usuario
        """
        return await self.find_many(
            {"user_id": user_id},
            limit=limit,
            skip=skip
        )
    
    async def find_by_api_key_id(self, api_key_id: str, limit: int = 20, skip: int = 0) -> List[ConversationInDB]:
        """
        Busca conversaciones por ID de API key
        """
        return await self.find_many(
            {"api_key_id": api_key_id},
            limit=limit,
            skip=skip
        )
    
    async def find_recent(self, limit: int = 20) -> List[ConversationInDB]:
        """
        Obtiene las conversaciones más recientes
        """
        cursor = self.collection.find().sort("updated_at", -1).limit(limit)
        documents = await cursor.to_list(length=limit)
        return [self.model_class(**document) for document in documents]
    
    async def search_by_title(self, search_term: str, limit: int = 20) -> List[ConversationInDB]:
        """
        Busca conversaciones por título
        """
        cursor = self.collection.find(
            {"title": {"$regex": search_term, "$options": "i"}}
        ).limit(limit)
        
        documents = await cursor.to_list(length=limit)
        return [self.model_class(**document) for document in documents]