from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from datetime import datetime

class DatabaseQuery(BaseModel):
    """
    Modelo para consultas a la base de datos desde el SDK de CoreBrain.
    """
    query: str
    collection_name: str
    limit: int = 50
    metadata: Dict[str, Any] = Field(default_factory=dict)


from pydantic import BaseModel, Field
from typing import Dict, List, Any, Optional, Union

from pydantic import BaseModel, Field
from typing import Dict, List, Any, Optional, Union

class MongoDBQuery(BaseModel):
    """
    Modelo para consultas MongoDB.
    Incluye tanto 'filter' como 'query' para máxima compatibilidad.
    """
    collection: str
    operation: str = "find"
    # Ambos campos como atributos reales
    query: Dict[str, Any] = Field(default_factory=dict)
    filter: Dict[str, Any] = Field(default_factory=dict)
    projection: Optional[Dict[str, Any]] = None
    sort: Optional[Dict[str, int]] = None
    limit: Optional[int] = None
    skip: Optional[int] = None
    pipeline: Optional[List[Dict[str, Any]]] = None
    document: Optional[Dict[str, Any]] = None
    update: Optional[Dict[str, Any]] = None
    
    class Config:
        extra = "allow"  # Permitir campos adicionales
    
    def __init__(self, **data):
        # Normalizar filter/query para sincronizarlos
        # Si hay filter pero no query, usar filter como query
        if "filter" in data and data["filter"] is not None:
            if "query" not in data or data["query"] is None:
                data["query"] = data["filter"]
        # Si hay query pero no filter, usar query como filter
        elif "query" in data and data["query"] is not None:
            if "filter" not in data or data["filter"] is None:
                data["filter"] = data["query"]
        
        # Normalizar la operación a minúsculas
        if "operation" in data:
            data["operation"] = data["operation"].lower()
        
        super().__init__(**data)
    
    def model_dump(self, *args, **kwargs):
        """
        Sobrescribe model_dump() para asegurar compatibilidad.
        """
        if hasattr(super(), "model_dump"):
            result = super().model_dump(*args, **kwargs)
            # Asegurarse de que tanto filter como query estén presentes
            if "query" in result and result["query"] is not None:
                result["filter"] = result["query"]
            elif "filter" in result and result["filter"] is not None:
                result["query"] = result["filter"]
            return result
        else:
            # Fallback para Pydantic v1
            return self.dict(*args, **kwargs)
    
    def dict(self, *args, **kwargs):
        """
        Sobrescribe dict() para asegurar compatibilidad con Pydantic v1.
        """
        result = super().dict(*args, **kwargs)
        # Asegurarse de que tanto filter como query estén presentes
        if "query" in result and result["query"] is not None:
            result["filter"] = result["query"]
        elif "filter" in result and result["filter"] is not None:
            result["query"] = result["filter"]
        return result
    
           
# Alternativa: Enfoque más simple sin propiedades (si el anterior no funciona)
class MongoDBQuerySimple(BaseModel):
    """
    Versión simplificada de MongoDBQuery sin usar propiedades.
    """
    collection: str
    operation: str = "find"
    # Usamos ambos campos como equivalentes
    query: Dict[str, Any] = Field(default_factory=dict)
    filter: Dict[str, Any] = Field(default_factory=dict)
    projection: Optional[Dict[str, Any]] = None
    sort: Optional[Dict[str, int]] = None
    limit: Optional[int] = None
    skip: Optional[int] = None
    pipeline: Optional[List[Dict[str, Any]]] = None
    document: Optional[Dict[str, Any]] = None
    update: Optional[Dict[str, Any]] = None
    
    class Config:
        extra = "allow"
    
    def __init__(self, **data):
        # Normalizar filter/query - si uno está presente, usarlo para ambos
        if "filter" in data and data["filter"] is not None:
            if "query" not in data or data["query"] is None:
                data["query"] = data["filter"]
        elif "query" in data and data["query"] is not None:
            if "filter" not in data or data["filter"] is None:
                data["filter"] = data["query"]
        
        # Normalizar la operación a minúsculas
        if "operation" in data:
            data["operation"] = data["operation"].lower()
        
        super().__init__(**data)
    
    def dict(self, *args, **kwargs):
        """
        Asegurar que filter y query estén sincronizados en la serialización.
        """
        result = super().dict(*args, **kwargs)
        return result
    
    def model_dump(self, *args, **kwargs):
        """
        Sobrescribe model_dump() para Pydantic v2.
        """
        if hasattr(super(), "model_dump"):
            return super().model_dump(*args, **kwargs)
        else:
            return self.dict(*args, **kwargs)


class QueryResult(BaseModel):
    """
    Modelo para resultados de consultas a la base de datos.
    """
    data: List[Any]
    count: int
    query_time_ms: float
    has_more: bool = False  # Valor por defecto para evitar el error de validación
    metadata: Dict[str, Any] = Field(default_factory=dict)

class AIQueryResponse(BaseModel):
    """
    Modelo para respuestas de consultas en lenguaje natural.
    """
    natural_query: str
    mongo_query: MongoDBQuery
    result: QueryResult
    explanation: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    processed_at: datetime = Field(default_factory=datetime.now)