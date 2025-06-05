# app/services/auth_service.py

from datetime import datetime, timedelta
import uuid
from typing import Optional, List, Dict, Any
from jose import jwt, JWTError
from app.core.security import generate_api_key, get_password_hash, verify_password, create_access_token
from app.core.config import settings
from app.core.cache import Cache
from app.core.logging import LogEntry
from app.database.repositories.api_key_repository import ApiKeyRepository
from app.database.repositories.user_repository import UserRepository
from app.models.api_key import ApiKeyCreate, ApiKeyInDB, ApiKeyUpdate
from app.models.user import UserCreate, UserInDB, UserUpdate
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi import HTTPException, status

db_client = AsyncIOMotorClient(settings.MONGODB.MONGODB_URL)
db = db_client[settings.MONGODB.MONGODB_DB_NAME]

# Repositorios
api_key_repo = ApiKeyRepository(db)
user_repo = UserRepository(db)

async def create_user(user_data: UserCreate) -> UserInDB:
    """Crea un nuevo usuario"""
    # Verificar si el email ya existe
    existing_user = await user_repo.find_by_email(user_data.email)
    if existing_user:
        raise ValueError(f"The email {user_data.email} is already registered")
    
    # Crear usuario
    user_id = str(uuid.uuid4())
    user_in_db = UserInDB(
        id=user_id,
        email=user_data.email,
        name=user_data.name,
        hashed_password=get_password_hash(user_data.password),
        created_at=datetime.now(),
        updated_at=datetime.now(),
        metadata=user_data.metadata or {}
    )
    
    # Guardar en la base de datos
    await user_repo.create(user_in_db)
    
    # Registrar creación
    LogEntry("user_created") \
        .set_user_id(user_id) \
        .add_data("email", user_data.email) \
        .log()
    
    return user_in_db

async def get_user(user_id: str) -> Optional[UserInDB]:
    """Obtiene un usuario por su ID"""
    return await user_repo.find_by_id(user_id)

async def get_user_by_email(user_email: str) -> Optional[UserInDB]:
    """Obtiene un usuario por su email"""
    user = await user_repo.find_by_email(user_email)
    print("Retorna el user: ", user)
    return user

async def authenticate_user_with_password(email: str, password: str) -> Optional[UserInDB]:
    """Autentica un usuario por email y contraseña"""
    user = await user_repo.find_by_email(email)
    
    if not user:
        # Registrar intento fallido
        LogEntry("login_failed", "warning") \
            .add_data("email", email) \
            .add_data("reason", "user_not_found") \
            .log()
        return None
    
    if not verify_password(password, user.hashed_password):
        # Registrar intento fallido
        LogEntry("login_failed", "warning") \
            .set_user_id(user.id) \
            .add_data("email", email) \
            .add_data("reason", "invalid_password") \
            .log()
        return None
    
    # Actualizar último login
    user.last_login = datetime.now()
    await user_repo.update("id", user.id, UserUpdate(last_login=user.last_login))
    
    # Registrar login exitoso
    LogEntry("login_success") \
        .set_user_id(user.id) \
        .add_data("email", email) \
        .log()
    
    return user

async def authenticate_user_decoding_access_token(token: str) -> Optional[UserInDB]:
    """Autentica un usuario decodificando el token"""
    try:
        payload = jwt.decode(
            token,
            settings.SECURITY.SECRET_KEY,
            algorithms=[settings.SECURITY.ALGORITHM]
        )
        print("Payload del token del SSO: ", payload)
        user_id = payload.get("sub")
        if user_id is None:
            return None
        
        user = await user_repo.find_by_id(user_id)
        if not user or not user.active:
            # Registrar intento fallido
            LogEntry("login_failed", "warning") \
                .add_data("reason", "user_not_found") \
                .log()
            return None
        
        # Actualizar último login
        user.last_login = datetime.now()
        await user_repo.update("id", user_id, UserUpdate(last_login=user.last_login))
        
        # Registrar login exitoso
        LogEntry("login_success") \
            .set_user_id(user.id) \
            .add_data("email", user.email) \
            .log()
        
        return user
    except JWTError as e:
        print("Error al decodificar el token: ", e)
        return None
    

def create_jwt_token(user_id: str, expires_delta: Optional[timedelta] = None) -> Dict[str, str]:
    """
    Crea un token JWT para un usuario
    """
    if expires_delta:
        expire = datetime.now() + expires_delta
    else:
        expire = datetime.now() + timedelta(minutes=settings.SECURITY.ACCESS_TOKEN_EXPIRE_MINUTES)
    
    to_encode = {"sub": user_id, "exp": expire}
    encoded_jwt = jwt.encode(
        to_encode, 
        settings.SECURITY.SECRET_KEY, 
        algorithm=settings.SECURITY.ALGORITHM
    )
    
    return {
        "access_token": encoded_jwt,
        "token_type": "bearer",
        "expires_at": expire.isoformat()
    }

async def verify_token(token: str) -> Optional[UserInDB]:
    """
    Verifica un token JWT y devuelve el usuario correspondiente
    """
    try:
        payload = jwt.decode(
            token, 
            settings.SECURITY.SECRET_KEY, 
            algorithms=[settings.SECURITY.ALGORITHM]
        )
        user_id = payload.get("sub")
        
        if user_id is None:
            return None
        
        # Obtener usuario de la base de datos
        user = await user_repo.find_by_id(user_id)
        if not user or not user.active:
            return None
        
        return user
    except JWTError:
        return None

async def create_api_key(api_key_data: ApiKeyCreate, user_id: str = None) -> ApiKeyInDB:
    """Crea una nueva API key para un usuario"""
    # Verificar que el usuario existe
    user = await user_repo.find_by_id(user_id)
    if not user:
        raise ValueError(f"Usuario con ID {user_id} no encontrado")
    
    # Generar API key
    api_key_id = str(uuid.uuid4())
    api_key = generate_api_key()
    
    # Crear objeto de API key
    api_key_in_db = ApiKeyInDB(
        id=api_key_id,
        key=api_key,
        name=api_key_data.name,
        level=api_key_data.level,
        user_id=user_id,
        created_at=datetime.now(),
        updated_at=datetime.now(),
        expires_at=api_key_data.expires_at if 'expires_at' in api_key_data else None,
        allowed_domains=api_key_data.allowed_domains or [] if 'allowed_domains' in api_key_data else [],
        metadata=api_key_data.metadata or {} if 'metadata' in api_key_data else {}
    )
    
    # Guardar en la base de datos
    await api_key_repo.create(api_key_in_db)
    
    # Registrar creación
    LogEntry("api_key_created") \
        .set_user_id(user_id) \
        .set_api_key_id(api_key_id) \
        .add_data("name", api_key_data.name) \
        .add_data("level", api_key_data.level) \
        .log()
    
    return api_key_in_db

async def update_api_key(api_key_id: str, api_key_data: ApiKeyUpdate) -> Optional[ApiKeyInDB]:
    # Guardar en la base de datos
    result = await api_key_repo.update("key", api_key_id, api_key_data)
    print("Result: ", result)
    
    # Registrar actualización
    LogEntry("api_key_updated") \
        .set_api_key_id(api_key_id) \
        .add_data("name", api_key_data.name if api_key_data.name else "unchanged") \
        .add_data("level", api_key_data.level if api_key_data.level else "unchanged") \
        .log()
    
    return result

async def get_api_key_data(api_key: str, validate: bool = True) -> Optional[ApiKeyInDB]:
    """
    Obtiene los datos de una API key y opcionalmente la valida
    
    Args:
        api_key: La API key a verificar
        validate: Si es True, verifica que la API key esté activa y no expirada
    
    Returns:
        ApiKeyInDB o None si no es válida
    """
    # Intentar obtener de caché primero
    cache_key = Cache.generate_key("api_key", api_key)
    cached_data = Cache.get(cache_key)
    
    if cached_data:
        # Convertir strings ISO a objetos datetime para campos de fecha
        if isinstance(cached_data.get('created_at'), str):
            cached_data['created_at'] = datetime.fromisoformat(cached_data['created_at'])
        if isinstance(cached_data.get('updated_at'), str):
            cached_data['updated_at'] = datetime.fromisoformat(cached_data['updated_at'])
        if cached_data.get('expires_at') and isinstance(cached_data['expires_at'], str):
            cached_data['expires_at'] = datetime.fromisoformat(cached_data['expires_at'])
        if cached_data.get('last_used_at') and isinstance(cached_data['last_used_at'], str):
            cached_data['last_used_at'] = datetime.fromisoformat(cached_data['last_used_at'])
        
        apiKeyInDB = ApiKeyInDB(**cached_data)
        return apiKeyInDB
    
    # Buscar en la base de datos
    print("Busca la db")
    api_key_data = await api_key_repo.find_by_key(api_key)
    print("Pasa el api key")
    if not api_key_data:
        print("Entra al api key data")
        return None
    print("Va al if validate")
    if validate:
        print("Entra en el validate")
        # Verificar que la API key esté activa
        if not api_key_data.active:
            return None
        
        # Verificar que no haya expirado
        if api_key_data.expires_at and api_key_data.expires_at < datetime.now():
            return None
    
    # Actualizar estadísticas de uso
    print("Update data")
    update_data = ApiKeyUpdate(
        last_used_at=datetime.now(),
        usage_count=api_key_data.usage_count + 1
    )
    print("Await api key repo")
    await api_key_repo.update("id", api_key_data.id, update_data)
    
    # Guardar en caché - Aquí está el cambio importante:
    # Convertir el modelo Pydantic a diccionario antes de guardarlo
    print("Guarda en cache")
    api_key_dict = api_key_data.model_dump() if hasattr(api_key_data, "model_dump") else api_key_data.dict()
    Cache.set(cache_key, api_key_dict, ttl=300)  # 5 minutos
    
    print("Return key data")
    return api_key_data


async def validate_api_key(api_key: str) -> Optional[ApiKeyInDB]:
    """Valida si una API key es válida y devuelve sus datos"""
    api_key_data = await get_api_key_data(api_key)
    print("Va al api key return; ", api_key_data)
    return api_key_data

async def revoke_api_key(api_key_id: str, user_id: str = None) -> bool:
    """Revoca una API key estableciéndola como inactiva"""
    api_key = await api_key_repo.find_by_id(api_key_id)
    
    if not api_key:
        return False
    
    # Desactivar la API key
    update_data = ApiKeyUpdate(active=False)
    await api_key_repo.update("key", api_key_id, update_data)
    
    # Invalidar caché
    cache_key = Cache.generate_key("api_key", api_key.key)
    Cache.delete(cache_key)
    
    # Registrar revocación
    LogEntry("api_key_revoked") \
        .set_user_id(user_id) \
        .set_api_key_id(api_key_id) \
        .add_data("name", api_key.name) \
        .log()
    
    return True

async def is_domain_allowed(api_key_data: ApiKeyInDB, domain: str) -> bool:
    """Verifica si un dominio está autorizado para una API key"""
    # Si no hay dominios permitidos especificados, permitir todos
    if not api_key_data.allowed_domains:
        return True
    
    # Verificar si el dominio exacto está permitido
    if domain in api_key_data.allowed_domains:
        return True
    
    # Verificar dominios con comodín (*.example.com)
    domain_parts = domain.split('.')
    for allowed_domain in api_key_data.allowed_domains:
        if allowed_domain.startswith('*.'):
            wildcard_domain = allowed_domain[2:]  # Eliminar '*.'
            if len(domain_parts) >= 2 and '.'.join(domain_parts[1:]) == wildcard_domain:
                return True
    
    return False

async def get_user_api_keys(user_id: str, include_inactive: bool = False) -> List[ApiKeyInDB]:
    """Obtiene todas las API keys de un usuario"""
    print("Entra en el get_user_api_keys: ", user_id)
    return await api_key_repo.find_by_user_id(user_id, include_inactive)

async def get_api_key(api_key: str) -> dict:
    """Get a specific API key by its value"""
    return await api_key_repo.find_by_key(api_key)

async def get_user_by_id(user_id: str) -> Optional[UserInDB]:
    """Obtiene un usuario por su ID"""
    return await user_repo.find_by_id(user_id)

async def update_user(user_id: str, user_data: UserUpdate) -> Optional[UserInDB]:
    """Actualiza los datos de un usuario"""
    # Verificar que el usuario existe
    user = await user_repo.find_by_id(user_id)
    if not user:
        return None
    
    # Si se está actualizando la contraseña, hashearla
    if user_data.password:
        user_data.hashed_password = get_password_hash(user_data.password)
        user_data.password = None  # Eliminar contraseña en texto plano
    
    # Actualizar usuario
    updated_user = await user_repo.update("id", user_id, user_data)
    
    # Registrar actualización
    LogEntry("user_updated") \
        .set_user_id(user_id) \
        .log()
    
    return updated_user

async def deactivate_user(user_id: str) -> bool:
    """Desactiva un usuario"""
    # Verificar que el usuario existe
    user = await user_repo.find_by_id(user_id)
    if not user:
        return False
    
    # Actualizar usuario
    update_data = UserUpdate(active=False)
    await user_repo.update("id", user_id, update_data)
    
    # Revocar todas las API keys del usuario    
    api_keys = await api_key_repo.find_by_user_id(user_id, include_inactive=True)
    for api_key in api_keys:
        if api_key.active:
            await revoke_api_key(api_key.id)
    
    # Registrar desactivación
    LogEntry("user_deactivated") \
        .set_user_id(user_id) \
        .log()
    
    return True

async def change_user_password(user_id: str, current_password: str, new_password: str) -> bool:
    """Cambia la contraseña de un usuario"""
    # Verificar que el usuario existe
    user = await user_repo.find_by_id(user_id)
    if not user:
        return False
    
    # Verificar contraseña actual
    if not verify_password(current_password, user.hashed_password):
        # Registrar intento fallido
        LogEntry("password_change_failed", "warning") \
            .set_user_id(user_id) \
            .add_data("reason", "invalid_current_password") \
            .log()
        return False
    
    # Actualizar contraseña
    update_data = UserUpdate(hashed_password=get_password_hash(new_password))
    await user_repo.update("id", user_id, update_data)
    
    # Registrar cambio
    LogEntry("password_changed") \
        .set_user_id(user_id) \
        .log()
    
    return True