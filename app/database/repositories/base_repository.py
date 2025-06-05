from typing import TypeVar, Generic, List, Optional, Dict, Any, Type
from datetime import datetime
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorDatabase, AsyncIOMotorCollection
from bson import ObjectId
from bson.errors import InvalidId

# Tipo genérico para modelos
T = TypeVar('T', bound=BaseModel)

class BaseRepository(Generic[T]):
    """
    Repositorio base para operaciones CRUD genéricas
    """
    
    def __init__(self, db: AsyncIOMotorDatabase, collection_name: str, model_class: Type[T]):
        self.db = db
        self.collection_name = collection_name
        self.collection = db[collection_name]
        self.model_class = model_class
    
    def _serialize_query(self, query: Dict[str, Any]) -> Dict[str, Any]:
        """
        Serializa un query para asegurar que todos los valores son compatibles con MongoDB.
        
        Args:
            query: Query original
            
        Returns:
            Query serializado
        """
        serialized_query = {}
        
        for key, value in query.items():
            # Si el valor es un objeto Pydantic, extraer el atributo apropiado
            if hasattr(value, 'key') and key == 'key' and isinstance(getattr(value, 'key'), str):
                serialized_query[key] = getattr(value, 'key')
            elif hasattr(value, 'id') and key == 'id' and isinstance(getattr(value, 'id'), str):
                serialized_query[key] = getattr(value, 'id')
            # Si es un modelo Pydantic, convertirlo a diccionario
            elif hasattr(value, 'model_dump'):
                serialized_query[key] = value.model_dump()
            elif hasattr(value, 'dict'):
                serialized_query[key] = value.dict()
            # Si es otro tipo de objeto con __dict__, usar su representación como diccionario
            elif hasattr(value, '__dict__') and not isinstance(value, (str, int, float, bool, list, dict, type(None))):
                serialized_query[key] = {k: v for k, v in value.__dict__.items() if not k.startswith('_')}
            # Para tipos básicos, usar directamente
            else:
                serialized_query[key] = value
        
        return serialized_query
    
    
    async def create(self, item: T) -> T:
        """
        Crea un nuevo documento
        """
        item_dict = item.model_dump(by_alias=True)
        await self.collection.insert_one(item_dict)
        return item
    
    async def find_by_id(self, id: str) -> Optional[T]:
        """
        Busca un documento por su ID
        """
        try:
            document = await self.collection.find_one({"id": id})
            if document:
                return self.model_class(**document)
            return None
        except Exception:
            return None
    
    async def find_by_other(self, other_field: str, other_value: str) -> Optional[T]:
        """
        Busca un documento por otro campo
        """
        document = await self.collection.find_one({other_field: other_value})
        if document:
            return self.model_class(**document)
        return None
    
    async def find_one(self, query: Dict[str, Any]) -> Optional[T]:
        """
        Busca un documento según un filtro.
        """
        # Serializar el query antes de enviarlo a MongoDB
        serialized_query = self._serialize_query(query)
        
        document = await self.collection.find_one(serialized_query)
        if document:
            print("Encuentra el documento: ", document)
            if self.model_class:
                return self.model_class(**document)
            return document
        return None  # Retornar None, no False
    
    async def find_many(self, query: Dict[str, Any], limit: int = 100, skip: int = 0) -> List[T]:
        """
        Busca múltiples documentos según un filtro
        """
        cursor = self.collection.find(query).skip(skip).limit(limit)
        documents = await cursor.to_list(length=limit)
        return [self.model_class(**document) for document in documents]
    
    async def update(self, field: str, value: str, item_update: BaseModel) -> Optional[T]:
        """
        Actualiza un documento por su ID u otro campo
        """
        # Obtener solo los campos que se están actualizando (no nulos)
        update_data = item_update.model_dump(exclude_unset=True, exclude_none=True)
        print("update_data que entra en la function: ", update_data)

        # Primero verificar si el documento existe
        exists = None
        if field == "id":
            exists = await self.find_by_id(value)
        else:
            exists = await self.find_by_other(field, value)
        
        if not exists:
            return None
            
        if not update_data:
            return exists
        
        # Añadir timestamp de actualización
        update_data["updated_at"] = datetime.now()
        
        # Realizar actualización
        print("Entra a actualizar la función")
        
        # Usar el ID del documento encontrado para la actualización
        result = await self.collection.update_one(
            {"id": exists.id},
            {"$set": update_data}
        )
        print("Result: ", result)
        
        if result.modified_count > 0:
            # Obtener el documento actualizado
            return await self.find_by_id(exists.id)
        else:
            # No hubo cambios, retornar el documento existente
            return exists
    
    async def delete(self, id: str) -> bool:
        """
        Elimina un documento por su ID
        """
        result = await self.collection.delete_one({"id": id})
        return result.deleted_count > 0
    
    async def count(self, query: Dict[str, Any]) -> int:
        """
        Cuenta documentos según un filtro
        """
        return await self.collection.count_documents(query)