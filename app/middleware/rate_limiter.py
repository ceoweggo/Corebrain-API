
from fastapi import Request, HTTPException, status
import time
import redis
from app.core.config import settings
from app.core.logging import LogEntry

# Cliente Redis para rate limiting
redis_client = redis.from_url(settings.CACHE.REDIS_URL)

class RateLimiter:
    """
    Middleware para limitar la tasa de peticiones usando algoritmo Token Bucket
    """
    
    async def __call__(self, request: Request, call_next):
        if not settings.RATE_LIMIT.ENABLE_RATE_LIMIT:
            return await call_next(request)
        
        # Obtener IP del cliente o API key como identificador
        client_id = self._get_client_id(request)
        
        # Calcular claves para Redis
        tokens_key = f"rate_limit:{client_id}:tokens"
        last_check_key = f"rate_limit:{client_id}:last_check"
        
        # Implementar algoritmo Token Bucket
        max_tokens = settings.RATE_LIMIT.BURST_SIZE
        tokens_per_second = settings.RATE_LIMIT.REQUESTS_PER_MINUTE / 60
        
        # Obtener tokens actuales y última verificación
        current_tokens = float(redis_client.get(tokens_key) or max_tokens)
        last_check = float(redis_client.get(last_check_key) or time.time())
        
        # Calcular tiempo transcurrido
        now = time.time()
        time_passed = now - last_check
        
        # Rellenar tokens basados en el tiempo transcurrido
        new_tokens = current_tokens + (time_passed * tokens_per_second)
        new_tokens = min(new_tokens, max_tokens)  # No exceder máximo
        
        # Verificar si hay suficientes tokens
        if new_tokens < 1:
            # Registrar límite excedido
            LogEntry("rate_limit_exceeded", "warning") \
                .add_data("client_id", client_id) \
                .add_data("path", request.url.path) \
                .add_data("method", request.method) \
                .log()
                
            # Calcular tiempo de espera para reintentar
            retry_after = int((1 - new_tokens) / tokens_per_second)
            
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Demasiadas peticiones. Intente más tarde.",
                headers={"Retry-After": str(retry_after)}
            )
        
        # Consumir token
        new_tokens -= 1
        
        # Actualizar valores en Redis
        pipeline = redis_client.pipeline()
        pipeline.set(tokens_key, new_tokens, ex=3600)  # Expirar en 1 hora
        pipeline.set(last_check_key, now, ex=3600)
        pipeline.execute()
        
        # Procesar solicitud
        return await call_next(request)
    
    def _get_client_id(self, request: Request) -> str:
        """
        Obtener identificador único del cliente (API key o IP)
        """
        # Priorizar API key si está disponible
        api_key = request.headers.get(settings.SECURITY.API_KEY_NAME)
        if api_key:
            return f"api_key:{api_key[:10]}"  # Usar prefijo de API key
        
        # Usar IP como fallback
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Obtener primera IP en la cadena X-Forwarded-For
            client_ip = forwarded_for.split(",")[0].strip()
        else:
            # Usar IP directa del cliente
            client_ip = request.client.host if request.client else "unknown"
        
        return f"ip:{client_ip}"