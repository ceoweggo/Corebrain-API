
import redis
import json
import pickle
import hashlib

from typing import Optional, Any

from app.core.config import settings
from app.core.utils import Utils, logger

# Cliente Redis
redis_client = redis.from_url(settings.CACHE.REDIS_URL)


class Cache:
    """
    Clase para interactuar con la caché Redis con soporte mejorado para serialización.
    """
    
    @staticmethod
    def generate_key(prefix: str, *args, **kwargs) -> str:
        """
        Genera una clave de caché a partir de parámetros, con manejo mejorado para objetos complejos.
        
        Args:
            prefix: Prefijo para la clave
            *args: Argumentos posicionales
            **kwargs: Argumentos de palabras clave
            
        Returns:
            Clave de caché en formato MD5
        """
        key_parts = [prefix]
        
        # Procesar argumentos posicionales
        for arg in args:
            try:
                # Para tipos simples, usar directamente
                if isinstance(arg, (str, int, float, bool, type(None))):
                    key_parts.append(str(arg))
                # Para ApiKeyInDB u objetos con clave 'key', usar solo esa propiedad
                elif hasattr(arg, "key") and isinstance(getattr(arg, "key"), str):
                    key_parts.append(getattr(arg, "key"))
                # Para otros objetos, usar representación JSON con el encoder personalizado
                else:
                    json_str = json.dumps(arg, sort_keys=True, cls=Utils.JSON.CustomJSONEncoder)
                    key_parts.append(hashlib.md5(json_str.encode()).hexdigest())
            except Exception as e:
                # En caso de error, usar hash del str del objeto
                logger.warning(f"Error serializing arg for cache key: {e}")
                key_parts.append(hashlib.md5(str(arg).encode()).hexdigest())
        
        # Procesar argumentos de palabras clave (ordenados por clave)
        for k in sorted(kwargs.keys()):
            v = kwargs[k]
            key_parts.append(f"{k}:")
            
            try:
                # Similar a los args, pero con manejo específico para kwargs
                if isinstance(v, (str, int, float, bool, type(None))):
                    key_parts.append(str(v))
                elif hasattr(v, "key") and isinstance(getattr(v, "key"), str):
                    key_parts.append(getattr(v, "key"))
                else:
                    json_str = json.dumps(v, sort_keys=True, cls=Utils.JSON.CustomJSONEncoder)
                    key_parts.append(hashlib.md5(json_str.encode()).hexdigest())
            except Exception as e:
                logger.warning(f"Error serializing kwarg {k} for cache key: {e}")
                key_parts.append(hashlib.md5(str(v).encode()).hexdigest())
        
        # Unir todas las partes con ':'
        key = ":".join(key_parts)
        
        # Aplicar hash MD5 para limitar longitud y asegurar compatibilidad
        return hashlib.md5(key.encode()).hexdigest()
    
    @staticmethod
    def set(key: str, value: Any, ttl: Optional[int] = None) -> bool:
        """
        Almacena un valor en caché
        
        Args:
            key: Clave de caché
            value: Valor a almacenar
            ttl: Tiempo de vida en segundos (opcional)
            
        Returns:
            True si se almacenó correctamente, False en caso contrario
        """
        if not settings.CACHE.ENABLE_CACHE:
            return False
        
        ttl = ttl if ttl is not None else settings.CACHE.CACHE_TTL
        
        try:
            # Intentar serializar objetos Pydantic antes de usar pickle
            if hasattr(value, "model_dump"):
                # Pydantic V2+
                value = json.dumps(value.model_dump(), cls=Utils.JSON.CustomJSONEncoder)
            elif hasattr(value, "dict"):
                # Pydantic V1
                value = json.dumps(value.dict(), cls=Utils.JSON.CustomJSONEncoder)
            # Para otros objetos complejos, usar pickle
            elif not isinstance(value, (str, int, float, bool, type(None))):
                value = pickle.dumps(value)
            
            return redis_client.set(key, value, ex=ttl)
        except Exception as e:
            logger.error(f"Error setting cache: {e}")
            return False
    
    @staticmethod
    def get(key: str, default: Any = None) -> Any:
        """
        Recupera un valor de la caché
        
        Args:
            key: Clave de caché
            default: Valor por defecto si no se encuentra
            
        Returns:
            Valor almacenado o default si no existe
        """
        if not settings.CACHE.ENABLE_CACHE:
            return default
        
        try:
            value = redis_client.get(key)
            if value is None:
                return default
            
            # Intentar deserializar
            try:
                # Primero intentar como JSON (para objetos Pydantic)
                return json.loads(value)
            except (TypeError, json.JSONDecodeError):
                try:
                    # Luego intentar como pickle
                    return pickle.loads(value)
                except Exception:
                    # Si no es JSON ni pickle, devolver como está
                    # (posiblemente bytes que deben ser decodificados)
                    try:
                        return value.decode('utf-8')
                    except (AttributeError, UnicodeDecodeError):
                        return value
        except Exception as e:
            logger.error(f"Error getting cache: {e}")
            return default
    
    @staticmethod
    def delete(key: str) -> bool:
        """
        Elimina una clave de la caché
        
        Args:
            key: Clave a eliminar
            
        Returns:
            True si se eliminó, False en caso contrario
        """
        if not settings.CACHE.ENABLE_CACHE:
            return False
        
        try:
            return redis_client.delete(key) > 0
        except Exception as e:
            logger.error(f"Error deleting cache: {e}")
            return False
    
    @staticmethod
    def flush() -> bool:
        """
        Limpia toda la caché
        
        Returns:
            True si se limpió correctamente, False en caso contrario
        """
        if not settings.CACHE.ENABLE_CACHE:
            return False
        
        try:
            return redis_client.flushdb()
        except Exception as e:
            logger.error(f"Error flushing cache: {e}")
            return False
    
    @staticmethod
    def exists(key: str) -> bool:
        """
        Verifica si una clave existe en la caché
        
        Args:
            key: Clave a verificar
            
        Returns:
            True si la clave existe, False en caso contrario
        """
        if not settings.CACHE.ENABLE_CACHE:
            return False
        
        try:
            return redis_client.exists(key) > 0
        except Exception as e:
            logger.error(f"Error checking cache existence: {e}")
            return False
    
    @staticmethod
    def increment(key: str, amount: int = 1) -> int:
        """
        Incrementa el valor de una clave
        
        Args:
            key: Clave a incrementar
            amount: Cantidad a incrementar
            
        Returns:
            Nuevo valor o 0 si falla
        """
        if not settings.CACHE.ENABLE_CACHE:
            return 0
        
        try:
            return redis_client.incrby(key, amount)
        except Exception as e:
            logger.error(f"Error incrementing cache: {e}")
            return 0
    
    @staticmethod
    def expire(key: str, ttl: int) -> bool:
        """
        Establece un tiempo de expiración para una clave
        
        Args:
            key: Clave a modificar
            ttl: Tiempo de vida en segundos
            
        Returns:
            True si se estableció, False en caso contrario
        """
        if not settings.CACHE.ENABLE_CACHE:
            return False
        
        try:
            return redis_client.expire(key, ttl)
        except Exception as e:
            logger.error(f"Error setting expiration: {e}")
            return False