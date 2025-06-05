from fastapi import Request, HTTPException, status, Depends
from fastapi.security import APIKeyHeader, HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional, Dict, Any, Tuple
from app.core.config import settings
from app.core.logging import LogEntry
from app.services import auth_service, cli_token_service
from app.database import get_database
from app.models.api_key import ApiKeyInDB
from datetime import datetime
from bson.objectid import ObjectId
from app.models.user import UserInDB

# Reemplazamos jwt con jose
from jose import jwt, JWTError
import time

# Definir el header de API key
API_KEY_HEADER = APIKeyHeader(name=settings.SECURITY.API_KEY_NAME)
API_SECRET_KEY = settings.SECURITY.SECRET_KEY
ALGORITHM = "HS256"  # Definimos explícitamente el algoritmo
security = HTTPBearer()  # Esquema de seguridad para tokens de autenticación

"""
API KEYS --> Used by CLI configurations
"""

async def get_api_key(
    api_key: str = Depends(API_KEY_HEADER)
) -> ApiKeyInDB:
    """
    Dependencia para obtener y validar la API key
    """
    try:
        print("Entra en la api key: ", api_key)
        if not api_key:
            # Registrar intento sin API key
            LogEntry("api_key_missing", "warning").log()
            
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key no proporcionada"
            )
        
        # Validar API key
        api_key_data = await auth_service.get_api_key_data(api_key)
        
        if not api_key_data:
            # Registrar intento fallido
            LogEntry("api_key_validation_failed", "warning") \
                .add_data("key_prefix", api_key[:5] if api_key else None) \
                .log()
                
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key inválida o expirada"
            )
        
        # Verificar que no esté revocada
        if api_key_data.active != True:
            LogEntry("api_key_revoked", "warning") \
                .set_api_key_id(api_key_data.id) \
                .set_user_id(api_key_data.user_id) \
                .log()
                
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key revocada"
            )
        
        # Registrar uso válido
        LogEntry("api_key_validation_success", "info") \
            .set_api_key_id(api_key_data.id) \
            .set_user_id(api_key_data.user_id) \
            .log()
            
        # Actualizar último uso
        await _update_api_key_usage(api_key_data.id)
        
        return api_key_data
    
    except HTTPException:
        raise
    except Exception as e:
        # Registrar error
        LogEntry("api_key_validation_error", "error") \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al validar API key"
        )

async def _update_api_key_usage(api_key_id: str):
    """
    Actualiza la fecha de último uso y contador de uso de una API key
    """
    try:
        now = datetime.now()
        
        db = await get_database()
        
        # En MongoDB usamos $inc para incrementar contadores
        await db.api_keys.update_one(
            {"id": api_key_id},
            {
                "$set": {"last_used_at": now},
                "$inc": {"usage_count": 1}
            }
        )
    except Exception as e:
        LogEntry("api_key_usage_update_error", "error") \
            .add_data("error", str(e)) \
            .add_data("api_key_id", api_key_id) \
            .log()
        # No bloqueamos la ejecución si esto falla

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    
    """
    Obtiene el usuario actual basado en el token JWT de autenticación
    """
    try:
        
        if not credentials or not credentials.credentials:
            LogEntry("token_missing", "warning").log()
            
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token de autorización no proporcionado",
                headers={"WWW-Authenticate": "Bearer"},
            )
        print("Credentials: ", credentials)
        token = credentials.credentials
        print("Token: ", token)
        try:
            # Verificar token con python-jose
            print("Verificando token...")
            payload = await cli_token_service.verify_token(token)
            print("payload: ", payload)
            if not payload:
                LogEntry("token_invalid", "warning") \
                    .add_data("token_prefix", token[:5] if token else None) \
                    .log()
                    
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token inválido o revocado",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            
            # Registrar uso válido del token
            LogEntry("token_validation_success", "info") \
                .set_user_id(payload.get("sub")) \
                .add_data("token_type", payload.get("sso_provider", "regular")) \
                .log()
                
            # Añadir indicación de que es un token API generado desde SSO
            if payload.get("token_source") == "sso_exchange":
                payload["is_api_token"] = True
                
            return payload
            
        except JWTError as e:
            LogEntry("token_decode_error", "warning") \
                .add_data("error", str(e)) \
                .add_data("token_prefix", token[:5] if token else None) \
                .log()
                
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except HTTPException:
        raise
    except Exception as e:
        LogEntry("token_validation_error", "error") \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error en autenticación"
        )

class AuthenticationMiddleware:
    """
    Middleware para autenticación y registro de solicitudes
    """
    
    async def authenticate_user_decoding_access_token(token: str) -> Optional[UserInDB]:
        """
        Autentica un usuario usando un token de SSO decodificado
        
        Args:
            token: El token de acceso del SSO
            
        Returns:
            UserInDB: El usuario autenticado o None si no se pudo autenticar
        """
        try:
            # Decodificar el token para obtener la información del usuario
            # Usamos verify=False porque ya viene del SSO y ha sido validado previamente
            payload = jwt.decode(
                token,
                settings.SECURITY.SECRET_KEY,
                algorithms=[settings.SECURITY.ALGORITHM],
                options={"verify_signature": True}
            )
            
            # Obtener el ID de usuario
            user_id = payload.get("sub")
            if not user_id:
                # Registrar intento fallido
                LogEntry("sso_login_failed", "warning") \
                    .add_data("reason", "missing_sub_claim") \
                    .log()
                return None
            
            # Obtener el usuario de la base de datos
            db = await get_database()
            user = await db.users.find_one({"_id": ObjectId(user_id)})
            if not user:
                # Registrar intento fallido
                LogEntry("sso_login_failed", "warning") \
                    .add_data("user_id", user_id) \
                    .add_data("reason", "user_not_found") \
                    .log()
                return None
            
            if not user.active:
                # Registrar intento fallido
                LogEntry("sso_login_failed", "warning") \
                    .set_user_id(user_id) \
                    .add_data("reason", "user_inactive") \
                    .log()
                return None
            
            # Actualizar último login
            user.last_login = datetime.now()
            await db.users.update_one({"_id": ObjectId(user_id)}, {"$set": {"last_login": datetime.now()}})
            
            # Registrar login exitoso
            LogEntry("sso_login_success") \
                .set_user_id(user.id) \
                .add_data("provider", payload.get("sso_provider", "unknown")) \
                .log()
            
            return user
        except JWTError as e:
            # Registrar error
            LogEntry("sso_token_decode_error", "error") \
                .add_data("error", str(e)) \
                .log()
            return None
        except Exception as e:
            # Registrar error general
            LogEntry("sso_login_error", "error") \
                .add_data("error", str(e)) \
                .log()
            return None
    
    async def __call__(self, request: Request, call_next):
        # Generar ID único para la solicitud
        request_id = LogEntry("").request_id
        request.state.request_id = request_id
        
        # Registrar inicio de solicitud
        start_time = time.time()
        path = request.url.path
        method = request.method
        
        # Definir rutas que no requieren autenticación
        exempt_paths = ["/api/auth/sso/token"]
        
        log_entry = LogEntry("request_started") \
            .add_data("path", path) \
            .add_data("method", method) \
            .add_data("request_id", request_id)
        
        # Extraer y registrar API key (si existe)
        api_key = request.headers.get(settings.SECURITY.API_KEY_NAME)
        if api_key and path not in exempt_paths:
            # Solo registramos los primeros 5 caracteres por seguridad
            log_entry.add_data("api_key_prefix", api_key[:5])
            
            # Intentar obtener información de la API key sin validar aún
            try:
                api_key_data = await auth_service.get_api_key_data(api_key, validate=False)
                if api_key_data:
                    request.state.api_key_data = api_key_data
                    log_entry.set_api_key_id(str(api_key_data.id))  # Convertir ObjectId a string
                    log_entry.set_user_id(api_key_data.user_id)
            except Exception as e:
                # Si hay error, registramos y continuamos sin datos de API key
                LogEntry("api_key_prefetch_error", "warning") \
                    .add_data("error", str(e)) \
                    .add_data("api_key_prefix", api_key[:5]) \
                    .log()
        
        # Extraer y registrar token JWT (si existe)
        auth_header = request.headers.get("Authorization")
        if auth_header and auth_header.startswith("Bearer ") and path not in exempt_paths:
            token = auth_header.replace("Bearer ", "")
            # Solo registramos los primeros 5 caracteres por seguridad
            log_entry.add_data("token_prefix", token[:5])
            
            # Intentar decodificar el token sin validar completamente
            try:
                # Decodificar sin verificación para obtener datos básicos
                decoded = jwt.decode(token, API_SECRET_KEY, algorithms=[ALGORITHM], options={"verify_signature": True})
                if decoded and "sub" in decoded:
                    log_entry.set_user_id(decoded["sub"])
                    request.state.token_data = decoded
            except Exception as e:
                # Si hay error, registramos y continuamos sin datos de token
                LogEntry("token_prefetch_error", "warning") \
                    .add_data("error", str(e)) \
                    .add_data("token_prefix", token[:5]) \
                    .log()
        
        log_entry.log()
        
        # Procesar la solicitud
        try:
            response = await call_next(request)
            
            # Registrar finalización exitosa
            process_time = time.time() - start_time
            status_code = response.status_code
            
            # Obtener ID y usuario de manera segura, considerando que pueden ser ObjectId
            api_key_id = None
            if hasattr(request.state, "api_key_data") and request.state.api_key_data:
                api_key_id = str(request.state.api_key_data.get("_id", "")) if isinstance(request.state.api_key_data, dict) else str(request.state.api_key_data.id)
            
            user_id = None
            if hasattr(request.state, "api_key_data") and request.state.api_key_data:
                user_id = request.state.api_key_data.get("user_id", "") if isinstance(request.state.api_key_data, dict) else request.state.api_key_data.user_id
            
            if not user_id and hasattr(request.state, "token_data") and request.state.token_data:
                user_id = request.state.token_data.get("sub", "")
            
            LogEntry("request_completed") \
                .add_data("path", path) \
                .add_data("method", method) \
                .add_data("status_code", status_code) \
                .add_data("process_time_ms", round(process_time * 1000, 2)) \
                .add_data("request_id", request_id) \
                .set_api_key_id(api_key_id) \
                .set_user_id(user_id) \
                .log()
            
            return response
            
        except Exception as e:
            # Registrar error
            process_time = time.time() - start_time
            
            # Obtener ID y usuario de manera segura, considerando que pueden ser ObjectId
            api_key_id = None
            if hasattr(request.state, "api_key_data") and request.state.api_key_data:
                api_key_id = str(request.state.api_key_data.get("_id", "")) if isinstance(request.state.api_key_data, dict) else str(request.state.api_key_data.id)
            
            user_id = None
            if hasattr(request.state, "api_key_data") and request.state.api_key_data:
                user_id = request.state.api_key_data.get("user_id", "") if isinstance(request.state.api_key_data, dict) else request.state.api_key_data.user_id
            
            if not user_id and hasattr(request.state, "token_data") and request.state.token_data:
                user_id = request.state.token_data.get("sub", "")
            
            LogEntry("request_failed", "error") \
                .add_data("path", path) \
                .add_data("method", method) \
                .add_data("error", str(e)) \
                .add_data("process_time_ms", round(process_time * 1000, 2)) \
                .add_data("request_id", request_id) \
                .set_api_key_id(api_key_id) \
                .set_user_id(user_id) \
                .log()
            
            raise