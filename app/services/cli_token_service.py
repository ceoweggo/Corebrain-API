"""
Route to generate tokens for CLI

Used to set up configuration for CLI.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from bson.objectid import ObjectId
from app.core.config import settings
from fastapi import HTTPException, status
from app.database import get_database
from jose import jwt, JWTError

import logging
import requests
import secrets

# Configuración
SSO_VALIDATION_URL = settings.SSO.VALIDATION_URL
API_SECRET_KEY = settings.SECURITY.SECRET_KEY
TOKEN_EXPIRATION = settings.SECURITY.TOKEN_EXPIRATION_MINUTES
ALGORITHM = "HS256"  # Definimos explícitamente el algoritmo

# Configurar logger
logger = logging.getLogger(__name__)

async def validate_sso_token(access_token: str) -> Optional[Dict]:
    """
    Verifica un token de acceso del SSO con el servidor de Globodain
    """
    try:
        print("Entra en el validate_sso_token: ", access_token)
        print("SSO_VALIDATION_URL: ", SSO_VALIDATION_URL)
        response = requests.post(
            SSO_VALIDATION_URL,
            json={"access_token": access_token, "token_type": "Bearer"},
            timeout=10  # Añadimos timeout para evitar bloqueos indefinidos
        )
        
        if response.status_code != 200:
            logger.warning(f"Validación de token SSO fallida. Código: {response.status_code}")
            return None
        
        
        print("response: ", response.json())
        
        user_data = response.json()
        logger.info(f"Token SSO validado para usuario: {user_data.get('email', 'N/A')}")
        return user_data
    except Exception as e:
        logger.error(f"Error validando token SSO: {str(e)}")
        return None

async def create_api_token(user_data: Dict, client_id: str) -> Tuple[str, datetime]:
    """
    Genera un nuevo token API basado en datos de usuario de SSO
    """
    # Generar expiración
    expiration = datetime.now() + timedelta(minutes=TOKEN_EXPIRATION)
    
    # Crear payload del token
    jti = secrets.token_hex(16)  # ID único para el token
    token_payload = {
        "sub": user_data.get("id", ""),
        "name": user_data.get("first_name", "") + " " + user_data.get("last_name", ""),
        "email": user_data.get("email", ""),
        "client_id": client_id,
        "exp": expiration,
        "iat": datetime.now(),
        "sso_provider": "Globodain",
        "token_source": "sso_exchange",  # Indicar que este token viene de un intercambio SSO
        "is_api_token": True,  # Marcar como token API
        "jti": jti
    }
    
    # Generar token JWT usando python-jose
    try:
        api_token = jwt.encode(
            token_payload,
            API_SECRET_KEY,
            algorithm=ALGORITHM
        )
        logger.debug(f"Token API generado para usuario {token_payload['sub']}")
    except Exception as e:
        logger.error(f"Error generando token: {str(e)}")
        raise Exception(f"Error generando token: {str(e)}")
    
    # Guardar el token en MongoDB
    try:
        db = await get_database()
        tokens_collection = db.tokens
        
        # Buscar si ya existe un token SSO para este usuario
        existing_token = await tokens_collection.find_one({
            "user_id": user_data.get("id", ""),
            "type": "sso"
        })
        
        now = datetime.now()
        
        if existing_token:
            # Actualizar el token existente
            await tokens_collection.update_one(
                {"_id": existing_token["_id"]},
                {
                    "$set": {
                        "token": api_token,
                        "last_used_at": now,
                        "jti": jti
                    }
                }
            )
            logger.info(f"Token SSO actualizado para usuario {user_data.get('id', '')}")
        else:
            # Crear un nuevo token
            token_doc = {
                "user_id": user_data.get("id", ""),
                "name": "Token SSO Globodain",
                "token": api_token,
                "created_at": now,
                "last_used_at": now,
                "status": "active",
                "type": "sso",
                "jti": jti
            }
            await tokens_collection.insert_one(token_doc)
            logger.info(f"Token SSO creado para usuario {user_data.get('id', '')}")
            
        # También agregar a la colección de tokens válidos para verificación rápida
        await db.valid_tokens.update_one(
            {"jti": jti},
            {
                "$set": {
                    "jti": jti,
                    "user_id": user_data.get("id", ""),
                    "exp": expiration,
                    "created_at": now
                }
            },
            upsert=True
        )
    except Exception as e:
        logger.error(f"Error guardando token en MongoDB: {str(e)}")
        # Continuamos aunque no se pueda guardar en BD
    
    return api_token, expiration

async def get_user_tokens(user_id: str) -> List[Dict]:
    """
    Obtiene todos los tokens de un usuario
    """
    try:
        db = await get_database()
        
        # Actualizar la fecha de último uso del token SSO
        now = datetime.now()
        await db.tokens.update_one(
            {"user_id": user_id, "type": "sso"},
            {"$set": {"last_used_at": now}}
        )
        
        # Obtener tokens del usuario
        print("User_id: ", user_id)
        cursor = db.tokens.find({"user_id": user_id})
        
        # Ordenar por tipo (SSO primero) y luego por fecha de creación
        cursor = cursor.sort([("type", -1), ("created_at", -1)])
        
        tokens = []
        async for token in cursor:
            # Por seguridad, solo mostramos parte del token
            masked_token = mask_token(token["token"])
            
            tokens.append({
                "id": str(token["_id"]),
                "name": token["name"],
                "token": masked_token,
                "created": token["created_at"].strftime("%Y-%m-%d"),
                "lastUsed": token["last_used_at"].strftime("%Y-%m-%d"),
                "status": token["status"],
                "type": token.get("type", "regular")
            })
        
        logger.info(f"Recuperados {len(tokens)} tokens para usuario {user_id}")
        return tokens
    except Exception as e:
        logger.error(f"Error obteniendo tokens: {str(e)}")
        raise e

def mask_token(token: str) -> str:
    """
    Oculta parte del token por seguridad
    """
    if not token or len(token) < 12:
        return token
    
    # Mostrar solo los primeros 4 y últimos 8 caracteres
    visible_chars = 12
    return f"{token[:4]}{'•' * (len(token) - visible_chars)}{token[-8:]}"

async def create_user_token(user_id: str, name: str) -> Tuple[str, datetime]:
    """
    Crea un nuevo token para un usuario
    """
    # Generar un nuevo token aleatorio
    token_value = f"sk_live_{''.join(secrets.choice('0123456789abcdefghijklmnopqrstuvwxyz') for _ in range(24))}"
    
    # Fecha actual
    now = datetime.now()
    
    # Fecha de expiración (1 año después)
    expiration = now + timedelta(days=365)
    
    # Generar un JTI único
    jti = secrets.token_hex(16)
    
    try:
        db = await get_database()
        
        # Crear el documento de token
        token_doc = {
            "user_id": user_id,
            "name": name,
            "token": token_value,
            "created_at": now,
            "last_used_at": now,
            "status": "active",
            "jti": jti
        }
        
        # Insertar en MongoDB
        result = await db.tokens.insert_one(token_doc)
        
        # Registrar en tokens válidos para verificación rápida
        await db.valid_tokens.insert_one({
            "jti": jti,
            "user_id": user_id,
            "exp": expiration,
            "created_at": now
        })
        
        logger.info(f"Token '{name}' creado para usuario {user_id}")
        return token_value, expiration
    except Exception as e:
        logger.error(f"Error creando token: {str(e)}")
        raise e

async def revoke_token(token_id: str, user_id: str) -> bool:
    """
    Revoca un token existente
    """
    try:
        db = await get_database()
        
        # Verificar que el token exista y pertenezca al usuario
        token = await db.tokens.find_one({"_id": ObjectId(token_id), "user_id": user_id})
        
        if not token:
            logger.warning(f"Token {token_id} no encontrado para usuario {user_id}")
            return False
        
        # No permitir revocar tokens SSO
        if token.get("type") == 'sso':
            logger.warning(f"Intento de revocar token SSO por usuario {user_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Los tokens SSO no pueden ser revocados"
            )
        
        # Si ya está revocado, no hacer nada
        if token["status"] == 'revoked':
            logger.info(f"Token {token_id} ya está revocado")
            return True
        
        now = datetime.now()
        
        # Actualizar el estado del token
        await db.tokens.update_one(
            {"_id": ObjectId(token_id)},
            {
                "$set": {
                    "status": "revoked", 
                    "last_used_at": now
                }
            }
        )
        
        # Eliminar de tokens válidos y añadir a revocados
        await db.valid_tokens.delete_one({"jti": token["jti"]})
        
        # Añadir a la colección de tokens revocados
        await db.revoked_tokens.insert_one({
            "jti": token["jti"],
            "revoked_at": now,
            "reason": "user_request"
        })
        
        logger.info(f"Token {token_id} revocado por usuario {user_id}")
        return True
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        logger.error(f"Error revocando token: {str(e)}")
        raise e

async def refresh_token(token_id: str, user_id: str) -> Tuple[Optional[str], Optional[datetime]]:
    """
    Renueva un token existente
    """
    try:
        db = await get_database()
        
        # Verificar que el token exista y pertenezca al usuario
        token = await db.tokens.find_one({"_id": ObjectId(token_id), "user_id": user_id})
        
        if not token:
            logger.warning(f"Token {token_id} no encontrado para usuario {user_id}")
            return None, None
        
        # No permitir renovar tokens SSO o revocados
        if token.get("type") == 'sso':
            logger.warning(f"Intento de renovar token SSO por usuario {user_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Los tokens SSO no pueden ser renovados"
            )
        
        if token["status"] != 'active':
            logger.warning(f"Intento de renovar token inactivo {token_id} por usuario {user_id}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Solo se pueden renovar tokens activos"
            )
        
        # Generar un nuevo token
        new_token_value = f"sk_live_{''.join(secrets.choice('0123456789abcdefghijklmnopqrstuvwxyz') for _ in range(24))}"
        
        # Fecha actual y de expiración
        now = datetime.now()
        expiration = now + timedelta(days=365)
        
        # Generar un nuevo JTI
        new_jti = secrets.token_hex(16)
        
        # Revocar el token anterior
        old_jti = token["jti"]
        
        # Eliminar token anterior de válidos
        await db.valid_tokens.delete_one({"jti": old_jti})
        
        # Añadir a revocados
        await db.revoked_tokens.insert_one({
            "jti": old_jti,
            "revoked_at": now,
            "reason": "refresh"
        })
        
        # Actualizar el token
        await db.tokens.update_one(
            {"_id": ObjectId(token_id)},
            {
                "$set": {
                    "token": new_token_value,
                    "last_used_at": now,
                    "jti": new_jti
                }
            }
        )
        
        # Agregar el nuevo token a los válidos
        await db.valid_tokens.insert_one({
            "jti": new_jti,
            "user_id": user_id,
            "exp": expiration,
            "created_at": now
        })
        
        logger.info(f"Token {token_id} ({token['name']}) renovado para usuario {user_id}")
        return new_token_value, expiration
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        logger.error(f"Error renovando token: {str(e)}")
        raise e

async def verify_token(token_str: str) -> Optional[Dict]:
    """
    Verifica un token y devuelve la información del usuario
    """
    try:
        # Decodificar el token con python-jose
        payload = jwt.decode(token_str, API_SECRET_KEY, algorithms=[ALGORITHM])
        
        # Verificar que no esté en la lista de revocación
        jti = payload.get("jti")
        if jti:
            db = await get_database()
            
            # Verificar si está revocado
            revoked = await db.revoked_tokens.find_one({"jti": jti})
            if revoked:
                logger.warning(f"Intento de uso de token revocado con JTI: {jti}")
                return None
            
            # Verificar si está en la lista de válidos
            valid = await db.valid_tokens.find_one({"jti": jti})
            if not valid:
                logger.warning(f"Token con JTI {jti} no encontrado en la lista de válidos")
                # En lugar de fallar, confiaremos en la verificación JWT
        
        # Actualizar último uso
        user_id = payload.get("sub")
        if user_id and jti:
            try:
                db = await get_database()
                now = datetime.now()
                
                await db.tokens.update_one(
                    {"user_id": user_id, "jti": jti},
                    {"$set": {"last_used_at": now}}
                )
            except Exception as e:
                logger.error(f"Error actualizando último uso: {str(e)}")
                # No bloqueamos la verificación por esto
        
        return payload
    except JWTError as e:
        logger.error(f"Error verificando token JWT: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado verificando token: {str(e)}")
        return None