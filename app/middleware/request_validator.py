from fastapi import Request, HTTPException, status
import json
from app.core.logging import LogEntry
from app.core.security import sanitize_mongo_query
from typing import Dict, Any

class RequestValidator:
    """
    Middleware para validar y sanitizar solicitudes
    """
    
    # Lista de operadores MongoDB permitidos
    ALLOWED_OPERATORS = [
        # Operadores de comparación básicos
        '$gt', '$lt', '$gte', '$lte', '$eq', '$ne', '$in', '$nin',
        # Operadores de agregación
        '$group', '$sum', '$avg', '$min', '$max', '$count', '$push', 
        '$addToSet', '$first', '$last', '$match', '$project', 
        '$lookup', '$unwind', '$sort', '$limit', '$skip',
        # Otros operadores comunes
        '$set', '$unset', '$inc', '$mul', '$rename', '$exists'
    ]
    
    async def __call__(self, request: Request, call_next):
        # Determinar si estamos en una ruta especial
        path = request.url.path
        is_mongodb_route = "/api/database/sdk/process_results" in path
        
        # Solo procesar solicitudes POST, PUT o PATCH con JSON
        if request.method in ["POST", "PUT", "PATCH"] and "application/json" in request.headers.get("content-type", ""):
            # Leer y validar cuerpo de la solicitud
            try:
                body = await request.body()
                json_body = json.loads(body)
                
                # Validar tamaño máximo (5MB)
                if len(body) > 5 * 1024 * 1024:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="El tamaño del cuerpo de la solicitud excede el límite permitido"
                    )
                
                # Buscar patrones maliciosos, pasando el contexto de la ruta
                self._check_for_malicious_patterns(json_body, is_mongodb_route)
                
                # Sanitizar consultas MongoDB si existe la clave 'query'
                if isinstance(json_body, dict) and 'query' in json_body and isinstance(json_body['query'], dict):
                    json_body['query'] = sanitize_mongo_query(json_body['query'])
                
                # Reemplazar el cuerpo de la solicitud con la versión sanitizada
                # Esto es un hack y no es ideal en FastAPI, pero es una solución simple para este ejemplo
                # En producción, sería mejor usar dependencias para procesar el cuerpo
                setattr(request, "_body", json.dumps(json_body).encode())
                
            except json.JSONDecodeError:
                # Registrar el error
                LogEntry("invalid_json_body", "warning") \
                    .add_data("path", request.url.path) \
                    .add_data("method", request.method) \
                    .log()
                    
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cuerpo JSON inválido"
                )
            except HTTPException:
                raise
            except Exception as e:
                # Registrar otros errores
                LogEntry("request_validation_error", "error") \
                    .add_data("path", request.url.path) \
                    .add_data("method", request.method) \
                    .add_data("error", str(e)) \
                    .log()
                    
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Error al procesar la solicitud"
                )
        
        # Continuar con el siguiente middleware o endpoint
        return await call_next(request)
    
    def _check_for_malicious_patterns(self, data: Any, is_mongodb_route: bool = False) -> None:
        """
        Verifica patrones maliciosos en los datos
        
        Args:
            data: Datos a verificar
            is_mongodb_route: Indica si estamos en una ruta que procesa consultas MongoDB
        """
        if isinstance(data, dict):
            # Determinar si estamos en un contexto de pipeline de MongoDB
            is_pipeline_context = False
            if 'pipeline' in data or 'operation' in data and data.get('operation') == 'aggregate':
                is_pipeline_context = True
            
            for key, value in data.items():
                # Detectar inyección NoSQL, pero permitir operadores MongoDB válidos en rutas especiales
                if key.startswith('$'):
                    # En rutas de MongoDB o contextos de pipeline, permitir más operadores
                    if not (key in self.ALLOWED_OPERATORS or is_mongodb_route or is_pipeline_context):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Operador no permitido: {key}"
                        )
                
                # Recursivamente verificar objetos anidados, pasando el contexto
                # Si estamos en un pipeline, permitir operadores MongoDB en los valores
                self._check_for_malicious_patterns(
                    value, 
                    is_mongodb_route=is_mongodb_route or is_pipeline_context or key == 'pipeline'
                )
                
        elif isinstance(data, list):
            for item in data:
                self._check_for_malicious_patterns(item, is_mongodb_route)