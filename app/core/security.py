
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Union
from jose import jwt
import secrets
import string
from passlib.context import CryptContext
from fastapi import HTTPException, status
from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def create_access_token(subject: Union[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    Crea un token JWT de acceso
    """
    if expires_delta:
        expire = datetime.now() + expires_delta
    else:
        expire = datetime.now() + timedelta(minutes=settings.SECURITY.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {"exp": expire, "sub": str(subject)}
    encoded_jwt = jwt.encode(to_encode, settings.SECURITY.SECRET_KEY, algorithm=settings.SECURITY.ALGORITHM)
    print("Retorna el encoded_jwt: ", encoded_jwt)
    return encoded_jwt

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verifica que una contraseña coincida con su hash
    """
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """
    Crea un hash seguro para una contraseña
    """
    return pwd_context.hash(password)

def generate_api_key(prefix: str = "sk") -> str:
    """
    Genera una API key segura con el formato: sk_xxxxxxxxxxxxxxxxxxxx
    """
    alphabet = string.ascii_letters + string.digits
    random_string = ''.join(secrets.choice(alphabet) for _ in range(24))
    return f"{prefix}_{random_string}"

def sanitize_mongo_query(query_obj: Dict[str, Any]) -> Dict[str, Any]:
    """
    Sanitiza una consulta MongoDB para prevenir operaciones peligrosas
    """
    # Eliminar operadores peligrosos
    dangerous_operators = ["$where", "$expr", "$function"]
    sanitized = {}
    
    for key, value in query_obj.items():
        # No permitir operadores peligrosos
        if key in dangerous_operators:
            continue
        
        # Recursivamente sanitizar objetos anidados
        if isinstance(value, dict):
            sanitized[key] = sanitize_mongo_query(value)
        elif isinstance(value, list):
            # Sanitizar cada objeto en la lista
            sanitized[key] = [
                sanitize_mongo_query(item) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            sanitized[key] = value
    
    return sanitized