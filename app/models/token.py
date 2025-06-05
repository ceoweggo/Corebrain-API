"""
Route to generate tokens for CLI

Used to set up configuration for CLI.
"""

from pydantic import BaseModel, Field, field_validator, field_serializer, BeforeValidator
from typing import Optional, List, Annotated, Any
from datetime import datetime
from bson import ObjectId
import json

# Soporte para ObjectId de MongoDB en Pydantic v2
class PyObjectId(str):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return str(v)

    @classmethod
    def __get_pydantic_json_schema__(cls, _schema_generator):
        return {"type": "string"}

# Función para convertir automáticamente ObjectId a string
def object_id_to_str(v: Any) -> str:
    if isinstance(v, ObjectId):
        return str(v)
    return v

# Tipo personalizado para ObjectId
PydanticObjectId = Annotated[str, BeforeValidator(object_id_to_str)]

class TokenRequest(BaseModel):
    """
    Modelo para solicitud de token API con token SSO
    """
    access_token: str = Field(..., description="Token de acceso de Globodin SSO")
    client_id: str = Field(..., description="Identificador del cliente que solicita el token")
    
    @field_validator('access_token')
    def sso_access_token_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('El token de acceso no puede estar vacío')
        return v
    
    @field_validator('client_id')
    def client_id_must_not_be_empty(cls, v):
        if not v or not v.strip():
            raise ValueError('El client_id no puede estar vacío')
        return v

class TokenResponse(BaseModel):
    """
    Modelo para respuesta de creación/renovación de token
    """
    token: str = Field(..., description="Token API generado")
    expires: str = Field(..., description="Fecha de expiración ISO-8601")
    user_data: Optional[dict] = Field(default=None, description="Datos del usuario")

class TokenCreate(BaseModel):
    """
    Modelo para crear un nuevo token
    """
    name: str = Field(..., description="Nombre descriptivo del token", min_length=3, max_length=100)
    
    @field_validator('name')
    def name_must_be_valid(cls, v):
        if not v or not v.strip():
            raise ValueError('El nombre del token no puede estar vacío')
        return v

class Token(BaseModel):
    """
    Modelo para representar un token
    """
    id: PydanticObjectId = Field(..., alias="_id", description="ID único del token")
    name: str = Field(..., description="Nombre descriptivo del token")
    token: str = Field(..., description="Valor del token (mostrado parcialmente por seguridad)")
    created: str = Field(..., description="Fecha de creación (YYYY-MM-DD)")
    lastUsed: str = Field(..., description="Fecha de último uso (YYYY-MM-DD)")
    status: str = Field(..., description="Estado del token (active/revoked)")
    type: Optional[str] = Field(None, description="Tipo de token (sso/regular)")
    user_id: str = Field(..., description="ID del usuario propietario del token")
    jti: Optional[str] = Field(None, description="JWT ID único para verificación")
    
    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "examples": [
                {
                    "_id": "507f1f77bcf86cd799439011",
                    "name": "API Token Principal",
                    "token": "sk_•••••••AxB8s9Z",
                    "created": "2023-01-01",
                    "lastUsed": "2023-01-15",
                    "status": "active",
                    "type": "regular",
                    "user_id": "user_123",
                    "jti": "c7e56cf4bb8e4a91a33e"
                }
            ]
        }
    }

class TokenRevocation(BaseModel):
    """
    Modelo para revocación de token
    """
    id: PydanticObjectId = Field(..., alias="_id", description="ID único de la revocación")
    jti: str = Field(..., description="Identificador único del token (JWT ID)")
    revoked_at: datetime = Field(..., description="Fecha y hora de revocación")
    reason: str = Field(..., description="Razón de la revocación", 
                      examples=["user_request", "security", "refresh"])
    
    model_config = {
        "populate_by_name": True,
        "json_schema_extra": {
            "examples": [
                {
                    "_id": "507f1f77bcf86cd799439011",
                    "jti": "c7e56cf4bb8e4a91a33e",
                    "revoked_at": "2023-01-15T12:30:45",
                    "reason": "user_request"
                }
            ]
        }
    }

class TokenInDB(BaseModel):
    """
    Modelo para representar un token en la base de datos
    """
    id: PydanticObjectId = Field(..., alias="_id", description="ID único del token")
    user_id: str = Field(..., description="ID del usuario propietario del token")
    name: str = Field(..., description="Nombre descriptivo del token")
    token: str = Field(..., description="Valor completo del token")
    created_at: datetime = Field(..., description="Fecha y hora de creación")
    last_used_at: datetime = Field(..., description="Fecha y hora de último uso")
    status: str = Field(..., description="Estado del token (active/revoked)")
    type: Optional[str] = Field(None, description="Tipo de token (sso/regular)")
    jti: Optional[str] = Field(None, description="JWT ID único para verificación")
    
    model_config = {
        "populate_by_name": True
    }
    
    @field_serializer('id')
    def serialize_id(self, id: Any) -> str:
        return str(id)
        
    def to_api_model(self) -> Token:
        """
        Convierte el modelo de BD a un modelo de API
        """
        return Token(
            _id=str(self.id),
            name=self.name,
            token=mask_token(self.token),
            created=self.created_at.strftime("%Y-%m-%d"),
            lastUsed=self.last_used_at.strftime("%Y-%m-%d"),
            status=self.status,
            type=self.type,
            user_id=self.user_id,
            jti=self.jti
        )

def mask_token(token: str) -> str:
    """
    Oculta parte del token por seguridad
    """
    if not token or len(token) < 12:
        return token
    
    # Mostrar solo los primeros 4 y últimos 8 caracteres
    visible_chars = 12
    return f"{token[:4]}{'•' * (len(token) - visible_chars)}{token[-8:]}"