from typing import List, Optional, Dict, Any
from app.database.repositories.base_repository import BaseRepository
from app.models.user import UserInDB, UserUpdate
from bson import ObjectId

class UserRepository(BaseRepository[UserInDB]):
    """
    Repositorio para operaciones con usuarios
    """
    
    def __init__(self, db):
        super().__init__(db, "users", UserInDB)
    
    async def find_by_email(self, email: str) -> Optional[UserInDB]:
        """
        Busca un usuario por su email
        """
        user = await self.find_one({"email": email})
        print("Retorna el user desde repository")
        return user
    
    async def find_by_id(self, user_id: str) -> Optional[UserInDB]:
        """
        Busca un usuario por su ID
        """
        return await self.find_one({"id": user_id})
    
    async def find_active_users(self) -> List[UserInDB]:
        """
        Obtiene todos los usuarios activos
        """
        return await self.find_many({"active": True})
    
    async def deactivate_user(self, user_id: str) -> bool:
        """
        Desactiva un usuario
        """
        update = UserUpdate(active=False)
        result = await self.update(user_id, update)
        return result is not None
