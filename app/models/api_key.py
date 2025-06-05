
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Dict, Any

class ApiKeyBase(BaseModel):
    name: str
    level: str = "read"  # Por defecto, nivel más bajo de permisos

class ApiKeyCreate(ApiKeyBase):
    user_id: str
    expires_at: Optional[datetime] = None
    allowed_domains: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None

class ApiKeyUpdate(BaseModel):
    name: Optional[str] = None
    level: Optional[str] = None
    active: Optional[bool] = None
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    usage_count: Optional[int] = None
    allowed_domains: Optional[List[str]] = None
    metadata: Optional[Dict[str, Any]] = None

class ApiKeyInDB(ApiKeyBase):
    id: str
    key: str
    user_id: str
    active: bool = True
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime] = None
    allowed_domains: List[str] = []
    last_used_at: Optional[datetime] = None
    usage_count: int = 0
    status: Optional[str] = None
    metadata: Dict[str, Any] = {}
    
    model_config = {"from_attributes": True}

    def get(self, key, default=None):
        """
        Método para mantener compatibilidad con código que espera un diccionario.
        Permite usar api_key_data.get('atributo') en lugar de api_key_data.atributo
        """
        if hasattr(self, key):
            return getattr(self, key)
        return default

class ApiKeyResponse(BaseModel):
    id: str
    name: str
    level: str
    key: str  # Solo se envía una vez al crear
    created_at: datetime
    expires_at: Optional[datetime] = None
    allowed_domains: List[str] = []
    
    model_config = {"from_attributes": True}