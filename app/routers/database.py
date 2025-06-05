from fastapi import APIRouter, Depends, HTTPException, status, Query, Body, Request
from typing import List, Optional, Dict, Any
from statistics import mean
from datetime import datetime

from app.models.database_query import DatabaseQuery, AIQueryResponse
from app.models.api_key import ApiKeyInDB
from app.services import db_service, auth_service
from app.middleware.authentication import get_api_key
from app.core.permissions import verify_permissions, PermissionError
from app.core.logging import LogEntry, logger
from app.core.querys import AIQuery
from app.models.database_query import QueryResult, MongoDBQuery

import re
import json
import traceback
import time
import bson
from bson import ObjectId, Decimal128

router = APIRouter()

def serialize_model(obj):
    """
    Convierte objetos Pydantic y otros tipos no serializables a formatos compatibles con JSON.
    
    Args:
        obj: El objeto a serializar
        
    Returns:
        Una versión serializable del objeto
    """
    if hasattr(obj, "model_dump"):
        # Para modelos Pydantic v2+
        return obj.model_dump()
    elif hasattr(obj, "dict"):
        # Para modelos Pydantic v1
        return obj.dict()
    elif isinstance(obj, datetime):
        # Para fechas y horas
        return obj.isoformat()
    elif isinstance(obj, (set, frozenset)):
        # Para conjuntos
        return list(obj)
    elif hasattr(obj, "__dict__"):
        # Para otros objetos con atributos
        return {k: serialize_model(v) for k, v in obj.__dict__.items() 
                if not k.startswith("_")}
    else:
        # Para tipos básicos
        return obj

def convert_bson_types(obj):
    """
    Recursively convert BSON types (ObjectId, Decimal128) to JSON-serializable types.
    """
    if isinstance(obj, list):
        return [convert_bson_types(item) for item in obj]
    elif isinstance(obj, dict):
        return {k: convert_bson_types(v) for k, v in obj.items()}
    elif isinstance(obj, ObjectId):
        return str(obj)
    elif isinstance(obj, Decimal128):
        # Convert to float if possible, else str
        try:
            return float(obj.to_decimal())
        except Exception:
            return str(obj)
    else:
        return obj


@router.post("/query", response_model=AIQueryResponse)
async def natural_language_query(
    query_data: DatabaseQuery = Body(...),
    api_key: ApiKeyInDB = Depends(get_api_key)
):
    """
    Ejecuta una consulta en lenguaje natural sobre la base de datos
    
    # * Sólo puede ejecutar actualmente consultas a 1 collection.
    # * No permite multiples consultas en diferentes collections.
    """
    try:
        # Verificar permisos básicos
        verify_permissions(api_key.level, "read")
        
        # Procesar consulta
        response = await db_service.process_natural_language_query(
            query=query_data.query,
            user_id=None,  # No hay usuario en API key
            api_key_id=api_key.id,
            api_key_level=api_key.level,
            collection_name=query_data.collection_name
        )
        
        return response
        
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
        
    except Exception as e:
        LogEntry("database_query_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("query", query_data.query) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al procesar consulta"
        )

@router.post("/sdk/query")
async def process_sdk_query(
    request: Request,
    api_key: ApiKeyInDB = Depends(get_api_key)
):
    """
    Procesa una consulta en lenguaje natural enviada desde el SDK de CoreBrain.
    Genera la consulta para la base de datos, la ejecuta utilizando la configuración
    del API key o el config_id proporcionado, y devuelve los resultados con su explicación.
    """
    try:
        # Primero obtener y validar la API key como objeto
        api_key_data = await auth_service.get_api_key_data(api_key)
        
        if not api_key_data:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="API key inválida o inactiva"
            )
        
        # Verificar permisos básicos
        verify_permissions(api_key_data.level, "write")
        
        # Leer el cuerpo de la solicitud como JSON
        body_bytes = await request.body()
        try:
            # Intentar parsear el JSON
            query_data = json.loads(body_bytes.decode('utf-8'))
        except json.JSONDecodeError:
            raise ValueError("El cuerpo de la solicitud no es un JSON válido")
        
        print("Lo que entra: ", query_data)
        
        # Extraer datos de la consulta desde el formato del SDK
        question = query_data.get("question")
        db_schema = query_data.get("db_schema")
        config_id = query_data.get("config_id")
        
        if not question:
            raise ValueError("La consulta (question) no puede estar vacía")
        
        if not db_schema:
            raise ValueError("El esquema de la base de datos (db_schema) no puede estar vacío")
        
        # Registrar consulta recibida
        LogEntry("sdk_query_received", "info") \
            .set_api_key_id(api_key_data.id) \
            .add_data("question", question) \
            .add_data("config_id", config_id) \
            .log()
        
        # Determinar el tipo de base de datos
        db_type = db_schema.get("type", "").lower()
        
        # Obtener la configuración de la base de datos - Importante: Convertir api_key_data a dict si es necesario
        db_config = None
        
        # 1. Intentar obtener la configuración desde el metadata de la API key
        api_key_dict = api_key_data.model_dump() if hasattr(api_key_data, "model_dump") else api_key_data.dict()
        if api_key_dict.get('metadata') and 'db_config' in api_key_dict['metadata']:
            db_config = api_key_dict['metadata']['db_config']
        
        # 2. Intentar obtener la configuración desde la solicitud
        if not db_config and 'db_config' in query_data:
            db_config = query_data.get('db_config')
        
        # 3. Intentar obtener la configuración desde la base de datos usando el config_id
        if not db_config and config_id:
            try:
                from app.database.repositories.api_key_repository import ApiKeyRepository
                db_config_obj = await ApiKeyRepository.find_key_by_id(config_id)
                if db_config_obj:
                    db_config = db_config_obj.model_dump() if hasattr(db_config_obj, "model_dump") else db_config_obj.dict()
            except Exception as e:
                logger.error(f"Error al obtener configuración por config_id: {str(e)}")
        
        
        
        # Fallback si no se encuentra ninguna configuración
        if not db_config:
            raise ValueError("No se pudo obtener una configuración de base de datos válida.")
        
        
        
        # Obtener la configuración de la base de datos
        api_key_data = await auth_service.get_api_key_data(api_key, False)
        print("Api data recogida: ", api_key_data)
        db_config = serialize_model(api_key_data)['metadata']['db_config']
        
        # Generar y ejecutar la consulta basada en el tipo de base de datos
        if db_type == "sql":
            # Para bases de datos SQL
            engine = db_schema.get("engine", "").lower()
            
            if not engine:
                LogEntry("sql_engine_missing", "warning") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .log()
                engine = db_config.get("engine", "generic")  # Usar el motor del config o "generic" como fallback
                
            # Generar consulta SQL usando la clase AIQuery
            try:
                sql_query = await AIQuery.generate_sql_query(question, db_schema, engine)
                
                # Registrar consulta generada
                LogEntry("sql_query_generated", "info") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("sql", sql_query) \
                    .log()
                
                # Ejecutar la consulta SQL
                start_time = time.time()
                result_data = await AIQuery.execute_sql_query(sql_query, db_config)
                query_time_ms = (time.time() - start_time) * 1000
                
                # Crear objeto QueryResult para la explicación
                query_result = QueryResult(
                    data=result_data[0],
                    count=len(result_data[0]),
                    query_time_ms=int(query_time_ms),
                    has_more=False,
                    metadata={
                        "engine": engine,
                        "config_id": config_id,
                        "executed_by": "api"
                    }
                )
                
                # Generar explicación de los resultados
                try:
                    explanation = await AIQuery.generate_sql_result_explanation(
                        query=question,
                        sql_query=sql_query,
                        result=query_result
                    )
                    
                    # Verificar que la explicación sea realmente texto y no un número/float
                    if not isinstance(explanation, str) or len(explanation) < 10:
                        # Generar una explicación de fallback si no es un string válido
                        explanation = generate_default_explanation(sql_query, result_data)
                except Exception as exp_error:
                    logger.error(f"Error al generar explicación: {str(exp_error)}")
                    explanation = generate_default_explanation(sql_query, result_data)
                
                # Registrar ejecución exitosa
                LogEntry("sql_query_executed", "info") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("sql", sql_query) \
                    .add_data("result_size", len(result_data)) \
                    .log()
                
                # Devolver los resultados de la consulta junto con una explicación
                response = {
                    "query": {
                        "sql": sql_query,
                        "engine": engine
                    },
                    "result": result_data,
                    "explanation": explanation,
                    "config_id": config_id
                }
                print("Response: ", response)
                return response
            except Exception as e:
                LogEntry("sql_query_execution_error", "error") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("sql", sql_query if 'sql_query' in locals() else "No generada") \
                    .add_data("error", str(e)) \
                    .log()
                raise ValueError(f"Error al ejecutar consulta SQL: {str(e)}")
                
        elif db_type in ["nosql", "mongodb"]:
            # Para bases de datos MongoDB
            collection_name = query_data.get("collection_name")
            
            # Intentar determinar una colección por defecto si no se especificó
            if not collection_name and db_schema and "tables" in db_schema:
                tables = db_schema.get("tables", {})
                if isinstance(tables, dict) and tables:
                    collection_name = next(iter(tables.keys()))
                elif isinstance(tables, list) and tables:
                    collection_name = tables[0].get("name")
            
            # Generar consulta MongoDB
            try:
                mongo_query = await AIQuery.generate_mongodb_query(question, db_schema, collection_name)
                
                # Registrar consulta generada
                LogEntry("mongo_query_generated", "info") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("collection", mongo_query.collection) \
                    .log()
                
                # Ejecutar la consulta MongoDB
                start_time = time.time()
                result_data, _ = await AIQuery.execute_mongodb_query(mongo_query, db_config)
                query_time_ms = (time.time() - start_time) * 1000
                # Convertir ObjectId y Decimal128 a tipos serializables
                result_data = convert_bson_types(result_data)
                # Crear objeto QueryResult para la explicación
                query_result = QueryResult(
                    data = result_data,
                    count=len(result_data) if isinstance(result_data, list) else 1,
                    query_time_ms=int(query_time_ms),
                    has_more=False,
                    metadata={
                        "collection": mongo_query.collection,
                        "config_id": config_id,
                        "executed_by": "api"
                    }
                )
                
                # Preparar el objeto de consulta para devolverlo
                if hasattr(mongo_query, "model_dump"):
                    query_dict = mongo_query.model_dump()
                elif hasattr(mongo_query, "dict"):
                    query_dict = mongo_query.dict()
                else:
                    # Fallback: crear diccionario manualmente
                    query_dict = {
                        "collection": mongo_query.collection,
                        "operation": mongo_query.operation,
                        "filter": mongo_query.filter,
                        "projection": mongo_query.projection,
                        "sort": mongo_query.sort,
                        "limit": mongo_query.limit,
                        "skip": mongo_query.skip
                    }
                
                # Generar explicación con validación
                try:
                    explanation = await AIQuery.generate_result_explanation(
                        query=question,
                        mongo_query=mongo_query,
                        result=query_result
                    )
                    
                    # Verificar que la explicación sea realmente texto y no un número/float
                    if not isinstance(explanation, str) or len(explanation) < 10:
                        # Generar una explicación de fallback si no es un string válido
                        explanation = generate_default_mongo_explanation(mongo_query, result_data)
                except Exception as exp_error:
                    logger.error(f"Error al generar explicación MongoDB: {str(exp_error)}")
                    explanation = generate_default_mongo_explanation(mongo_query, result_data)
                
                # Registrar ejecución exitosa
                LogEntry("mongo_query_executed", "info") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("collection", mongo_query.collection) \
                    .add_data("result_size", len(result_data) if isinstance(result_data, list) else 1) \
                    .log()
                
                # Devolver los resultados de la consulta junto con una explicación
                response = {
                    "query": query_dict,
                    "result": result_data,
                    "explanation": explanation,
                    "config_id": config_id
                }
                print("Response: ", response)
                return response
            except Exception as e:
                LogEntry("mongo_query_execution_error", "error") \
                    .set_api_key_id(api_key.id) \
                    .add_data("question", question) \
                    .add_data("error", str(e)) \
                    .log()
                raise ValueError(f"Error al ejecutar consulta MongoDB: {str(e)}")
        
        else:
            # Tipo de base de datos no reconocido
            LogEntry("unsupported_db_type", "error") \
                .set_api_key_id(api_key.id) \
                .add_data("question", question) \
                .add_data("db_type", db_type) \
                .log()
                
            return {
                "query": None,
                "result": None,
                "explanation": f"Tipo de base de datos no soportado: {db_type}. Verifica la configuración de tu SDK.",
                "error": True,
                "config_id": config_id
            }
        
    except PermissionError as e:
        LogEntry("permission_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    
    except ValueError as e:
        LogEntry("value_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    except Exception as e:
        # Capturar los detalles del error para un mejor diagnóstico
        import traceback
        error_details = traceback.format_exc()
        
        LogEntry("sdk_query_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .add_data("traceback", error_details) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al procesar consulta: {str(e)}"
        )

def generate_default_explanation(sql_query: str, result_data: list) -> str:
    """
    Genera una explicación predeterminada cuando la IA falla en generar una explicación.
    
    Args:
        sql_query: La consulta SQL ejecutada
        result_data: Los resultados obtenidos
        
    Returns:
        Explicación generada
    """
    sql_lower = sql_query.lower()
    result_count = len(result_data)
    
    # Obtener nombres de tablas de la consulta
    table_names = []
    from_match = re.search(r'from\s+([a-zA-Z0-9_]+)', sql_lower)
    if from_match:
        table_names.append(from_match.group(1))
    
    join_matches = re.findall(r'join\s+([a-zA-Z0-9_]+)', sql_lower)
    table_names.extend(join_matches)
    
    # Determinar tipo de consulta
    if "select" in sql_lower:
        if "join" in sql_lower:
            # Consulta con JOIN
            if result_count == 0:
                return f"No se encontraron resultados que relacionen las tablas {', '.join(table_names)}."
            else:
                if "where" in sql_lower:
                    # Consulta filtrada con JOIN
                    return f"Se encontraron {result_count} registros que cumplen con los criterios especificados, relacionando información de las tablas {', '.join(table_names)}."
                else:
                    return f"Se obtuvieron {result_count} registros relacionando información de las tablas {', '.join(table_names)}."
        
        elif "where" in sql_lower:
            # Consulta con filtro
            if result_count == 0:
                return "No se encontraron registros que cumplan con los criterios de búsqueda."
            else:
                return f"Se encontraron {result_count} registros que cumplen con los criterios de búsqueda."
        
        else:
            # Consulta simple
            return f"La consulta devolvió {result_count} registros de la base de datos."
    
    # Fallback general
    return f"Se ejecutó la consulta y se obtuvieron {result_count} resultados."

def generate_default_mongo_explanation(mongo_query, result_data: list) -> str:
    """
    Genera una explicación predeterminada para consultas MongoDB cuando la IA falla.
    
    Args:
        mongo_query: La consulta MongoDB ejecutada
        result_data: Los resultados obtenidos
        
    Returns:
        Explicación generada
    """
    collection = getattr(mongo_query, "collection", "la colección")
    operation = getattr(mongo_query, "operation", "find")
    result_count = len(result_data) if isinstance(result_data, list) else (1 if result_data else 0)
    
    # Determinar tipo de operación
    if operation == "find":
        if result_count == 0:
            return f"No se encontraron documentos en {collection} que coincidan con los criterios de búsqueda."
        else:
            return f"Se encontraron {result_count} documentos en {collection} que coinciden con los criterios de búsqueda."
    
    elif operation == "findOne":
        if result_data:
            return f"Se encontró el documento solicitado en {collection}."
        else:
            return f"No se encontró ningún documento en {collection} que coincida con los criterios de búsqueda."
    
    elif operation == "aggregate":
        return f"La agregación en {collection} devolvió {result_count} resultados."
    
    elif operation == "insertOne":
        return f"Se ha insertado correctamente un nuevo documento en {collection}."
    
    elif operation == "updateOne":
        return f"Se ha actualizado correctamente un documento en {collection}."
    
    elif operation == "deleteOne":
        return f"Se ha eliminado correctamente un documento de {collection}."
    
    # Fallback general
    return f"Se ejecutó la operación {operation} y se obtuvieron {result_count} resultados."

@router.post("/sdk/query/explain")
async def process_query_results(
    request: Request,
    api_key: ApiKeyInDB = Depends(get_api_key)
):
    """
    Procesa los resultados de una consulta ejecutada en el cliente o en la API y genera una explicación.
    Puede recibir resultados ejecutados por el SDK (executed_by: "sdk") o por la API (executed_by: "api").
    """
    try:
        # Verificar permisos básicos
        verify_permissions(api_key.level, "read")
        
        # Leer y parsear el cuerpo de la solicitud
        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise ValueError("El cuerpo de la solicitud no es un JSON válido")
        
        # Extraer datos esenciales de la solicitud
        question = data.get("question")
        query = data.get("query")
        result = data.get("result", [])
        config_id = data.get("config_id")
        query_time_ms = data.get("query_time_ms", 0)
        metadata = data.get("metadata", {})
        
        if not question or not query:
            raise ValueError("La pregunta y la consulta ejecutada son obligatorias")
        
        # Si el resultado viene con estructura anidada (formato completo)
        if isinstance(result, dict) and "data" in result:
            query_time_ms = result.get("query_time_ms", query_time_ms)
            result = result.get("data", [])
        
        # Procesar resultados en formato string JSON si es necesario
        if isinstance(result, list) and result and isinstance(result[0], str) and result[0].startswith('{'):
            try:
                result = [json.loads(item) for item in result]
            except json.JSONDecodeError:
                pass  # Si falla, mantener los resultados originales
        
        # Registrar la solicitud
        LogEntry("process_results_received", "info") \
            .set_api_key_id(api_key.id) \
            .add_data("question", question) \
            .add_data("config_id", config_id) \
            .add_data("result_count", len(result) if isinstance(result, list) else 1) \
            .log()
        
        # Determinar el tipo de consulta
        is_sql_query = isinstance(query, dict) and "sql" in query
        
        # Determinar el origen de la ejecución (SDK o API)
        executed_by = metadata.get("executed_by", "sdk")
        
        # Crear objeto QueryResult con has_more=False por defecto
        query_result = QueryResult(
            data=result,
            count=len(result) if isinstance(result, list) else 1,
            query_time_ms=query_time_ms,
            has_more=False,  # Valor por defecto
            metadata={
                "engine": query.get("engine", "") if is_sql_query else "",
                "config_id": config_id,
                "executed_by": executed_by,
                **metadata  # Incluir cualquier otro metadata proporcionado
            }
        )
        
        # Generar explicación según el tipo de consulta
        try:
            if is_sql_query:
                explanation = await AIQuery.generate_sql_result_explanation(
                    query=question,
                    sql_query=query.get("sql", ""),
                    result=query_result
                )
                
                # Verificar que la explicación sea válida
                if not isinstance(explanation, str) or len(explanation) < 10:
                    explanation = generate_default_explanation(query.get("sql", ""), result)
            else:
                # Para consultas MongoDB
                try:
                    # Evitar problemas con la validación de operadores MongoDB
                    collection = query.get("collection", "")
                    operation = query.get("operation", "")
                    
                    # Si la consulta incluye información de pipeline, asegurarse de que esté disponible
                    if "pipeline" in query and not isinstance(query.get("pipeline"), list):
                        query["pipeline"] = []
                    
                    # Usar la función de explicación directamente
                    explanation = await AIQuery.generate_result_explanation(
                        query=question,
                        mongo_query=query,  # Pasar el diccionario original
                        result=query_result
                    )
                    
                    # Verificar que la explicación sea válida
                    if not isinstance(explanation, str) or len(explanation) < 10:
                        explanation = generate_default_mongo_explanation(query, result)
                except Exception as e:
                    logger.error(f"Error al procesar consulta MongoDB: {str(e)}")
                    # Explicación predeterminada
                    collection_name = query.get('collection', '') if isinstance(query, dict) else ''
                    explanation = generate_default_mongo_explanation(query, result)
        except Exception as exp_error:
            logger.error(f"Error en la generación de explicación: {str(exp_error)}")
            # Si la explicación está vacía o es muy genérica, enriquecerla
            explanation = enrich_explanation(question, query, result, is_sql_query)
        
        # Registrar éxito
        LogEntry("process_results_success", "info") \
            .set_api_key_id(api_key.id) \
            .add_data("question", question) \
            .add_data("result_count", len(result) if isinstance(result, list) else 1) \
            .log()
        
        # Devolver respuesta
        print("Explicación final: ", explanation)
        return {
            "explanation": explanation,
            "query": query,
            "result": {
                "data": result,
                "count": len(result) if isinstance(result, list) else 1,
                "query_time_ms": query_time_ms,
                "has_more": False
            },
            "processed_at": datetime.now().isoformat()
        }
    
    except PermissionError as e:
        LogEntry("permission_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    
    except ValueError as e:
        LogEntry("value_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    except Exception as e:
        # Registrar el error completo
        LogEntry("process_results_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .add_data("traceback", traceback.format_exc()) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al procesar resultados: {str(e)}"
        )

def enrich_explanation(question: str, query: Any, result: List[Dict], is_sql_query: bool) -> str:
    """
    Genera una explicación mejorada cuando la explicación original es insuficiente
    o cuando ocurre un error al generar la explicación con IA.
    
    Args:
        question: Pregunta original
        query: Consulta ejecutada
        result: Resultados obtenidos
        is_sql_query: Si es una consulta SQL
        
    Returns:
        Explicación enriquecida
    """
    try:
        # Determinar el tipo de consulta
        if is_sql_query:
            sql_text = query.get("sql", "").lower() if isinstance(query, dict) else str(query).lower()
            
            # Analizar el tipo de consulta SQL
            if "count" in sql_text and "group by" not in sql_text:
                return f"Se realizó un conteo que devolvió {result[0].get('count', 0) if result else 0} registros."
                
            if "join" in sql_text:
                tables = []
                join_pattern = r'(?:join|from)\s+([a-zA-Z0-9_]+)'
                matches = re.findall(join_pattern, sql_text)
                if matches:
                    tables = matches
                    
                return (f"Se consultaron datos relacionando las tablas {', '.join(tables)}. "
                       f"Se encontraron {len(result)} registros que cumplen con los criterios especificados.")
            
            if "group by" in sql_text:
                return f"Se realizó una agrupación de datos que devolvió {len(result)} grupos diferentes."
                
            # Explicación genérica para SQL
            return f"La consulta devolvió {len(result)} registros de la base de datos."
            
        else:
            # Para MongoDB
            if isinstance(query, dict):
                collection = query.get("collection", "documentos")
                operation = query.get("operation", "find")
                
                if operation == "find":
                    return f"Se buscaron documentos en la colección {collection} y se encontraron {len(result)} resultados."
                elif operation == "findOne":
                    return f"Se buscó un documento específico en la colección {collection}."
                elif operation == "aggregate":
                    return f"Se realizó una agregación en la colección {collection} que devolvió {len(result)} resultados."
                elif operation in ["insertOne", "updateOne", "deleteOne"]:
                    operations = {
                        "insertOne": "insertó",
                        "updateOne": "actualizó",
                        "deleteOne": "eliminó"
                    }
                    return f"Se {operations.get(operation)} un documento en la colección {collection}."
                    
            # Explicación genérica para MongoDB
            return f"La consulta devolvió {len(result)} documentos de la base de datos."
    
    except Exception as e:
        logger.error(f"Error al enriquecer explicación: {str(e)}")
        
        # Explicación muy básica como último recurso
        result_count = len(result) if isinstance(result, list) else 0
        return f"Se encontraron {result_count} resultados para tu consulta."


@router.post("/sdk/query/identify-collections")
async def identify_query_collections(
    request: Request,
    api_key: ApiKeyInDB = Depends(get_api_key)
):
    """
    Procesa la pregunta y los esquemas para determinar cuáles son las colecciones que se deben consultar.
    """
    try:
        # Verificar permisos básicos
        verify_permissions(api_key.level, "read")
        
        # Leer y parsear el cuerpo de la solicitud
        try:
            data = await request.json()
        except json.JSONDecodeError:
            raise ValueError("El cuerpo de la solicitud no es un JSON válido")
        
        # Extraer datos esenciales de la solicitud
        question = data.get("question")
        
        db_schema = data.get("db_schema")
        db_schema_type = db_schema['type']
        db_schema_database = db_schema['database']
        db_schema_total_collections = db_schema['total_collections']
        db_schema_collection_names = db_schema['collection_names']
        
        config_id = data.get("config_id")
        step = data.get("step")
        
        print("Question: ", question)
        print("DB Schema Type: ", db_schema_type)
        print("DB Schema Database: ", db_schema_database)
        print("DB Schema Total Collections: ", db_schema_total_collections)
        print("DB Schema Collection Names: ", db_schema_collection_names)
        print("Config ID: ", config_id)
        print("Step: ", step)
        
        if not question:
            raise ValueError("La pregunta y la consulta ejecutada son obligatorias")

        try:
            explanation = await AIQuery.process_collections_query(
                question=question,
                db_schema=db_schema_collection_names
            )
            
            print("Explanation: ", explanation)
            
            # Devolver respuesta
            return {
                "explanation": explanation,
                "query": question,
                "processed_at": datetime.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Error al procesar consulta: {str(e)}")
    

    except PermissionError as e:
        LogEntry("permission_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
    
    except ValueError as e:
        LogEntry("value_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    
    except Exception as e:
        # Registrar el error completo
        LogEntry("process_results_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .add_data("traceback", traceback.format_exc()) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al procesar resultados: {str(e)}"
        )



@router.get("/try", response_model=Dict[str, Any])
async def get_truth(
    api_key = Depends(get_api_key)
):
    """
    Obtiene información sobre las colecciones y esquemas de la base de datos
    y calcula estadísticas para las transacciones
    """
    try:
        print("Entra a la función")
        # Verificar permisos básicos
        verify_permissions(api_key.level, "read")
        print("Va al response data")
        # Preparar la respuesta estructurada
        response_data = {
            "status": "success",
            "message": "Información de la base de datos y estadísticas de transacciones",
            "database_info": {},
            "transaction_stats": {
                "aggregation_method": None,
                "accounts_processed": 0,
                "average_transaction_amount": None,
                "account_details": []
            }
        }

        # Obtener información de la base de datos para incluirla en la respuesta
        database = db_service.db

        # Acceder a la colección de transacciones
        transactions_collection = database['transactions']
        
        # Método 1: Usando agregación de MongoDB
        try:
            resultado_agregacion = await transactions_collection.aggregate([
                {
                    "$match": {
                        "transactions": { "$exists": True, "$ne": [] }
                    }
                },
                {
                    "$project": {
                        "_id": 1,
                        "account_id": 1,
                        "media_amount": { "$avg": "$transactions.amount" }
                    }
                }
            ]).to_list(length=None)
            
            print("\nResultados de agregación:")
            
            if resultado_agregacion:
                # Imprimir resultados de la agregación
                total_media = 0
                contador = 0
                
                for doc in resultado_agregacion:
                    if 'media_amount' in doc:
                        total_media += doc['media_amount']
                        contador += 1
                        print(f"  Cuenta ID: {doc.get('account_id', 'No ID')} - Media: {doc['media_amount']}")
                        
                        # Añadir información a la respuesta
                        response_data["transaction_stats"]["account_details"].append({
                            "account_id": doc.get('account_id', 'No ID'),
                            "average_amount": doc['media_amount']
                        })
                
                if contador > 0:
                    average = total_media / contador
                    print(f"Media general para todas las transacciones: {average}")
                    
                    # Actualizar los datos de la respuesta
                    response_data["transaction_stats"]["aggregation_method"] = "MongoDB Aggregation"
                    response_data["transaction_stats"]["accounts_processed"] = contador
                    response_data["transaction_stats"]["average_transaction_amount"] = average
                else:
                    print(f"No se encontraron documentos con 'transactions.amount' en la colección")
                    response_data["transaction_stats"]["aggregation_method"] = "MongoDB Aggregation - No Data"
            else:
                print("  La agregación no devolvió resultados. Intentando método alternativo...")
                
                # Método 2: Procesar los datos en Python si la agregación no funciona
                from statistics import mean
                
                documentos = await transactions_collection.find(
                    {"transactions": {"$exists": True}}
                ).to_list(length=None)
                
                total_docs = 0
                sum_medias = 0
                
                for doc in documentos:
                    if 'transactions' in doc and isinstance(doc['transactions'], list) and doc['transactions']:
                        # Extraer todos los valores de 'amount'
                        try:
                            amounts = [t['amount'] for t in doc['transactions'] 
                                    if isinstance(t, dict) and 'amount' in t]
                            
                            if amounts:
                                media_amount = mean(amounts)
                                sum_medias += media_amount
                                total_docs += 1
                                print(f"  Cuenta ID: {doc.get('account_id', 'No ID')} - Media (Python): {media_amount}")
                                
                                # Añadir información a la respuesta
                                response_data["transaction_stats"]["account_details"].append({
                                    "account_id": doc.get('account_id', 'No ID'),
                                    "average_amount": media_amount
                                })
                        except Exception as e:
                            print(f"  Error al procesar documento: {str(e)}")
                
                if total_docs > 0:
                    python_average = sum_medias / total_docs
                    print(f"  Media general (calculada en Python): {python_average}")
                    
                    # Actualizar los datos de la respuesta
                    response_data["transaction_stats"]["aggregation_method"] = "Python Calculation"
                    response_data["transaction_stats"]["accounts_processed"] = total_docs
                    response_data["transaction_stats"]["average_transaction_amount"] = python_average
                else:
                    print(f"  No se encontraron documentos con 'transactions.amount' válidos")
                    response_data["transaction_stats"]["aggregation_method"] = "Python Calculation - No Data"
        
        except Exception as e:
            error_message = f"Error en el cálculo de estadísticas: {str(e)}"
            print(error_message)
            response_data["status"] = "partial_success"
            response_data["message"] = error_message
        
        # Retornar la respuesta estructurada
        return response_data
        
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
        
    except Exception as e:
        LogEntry("get_schema_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
        print("Error: ", e)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al obtener esquema de la base de datos"
        )
        
@router.get("/collections", response_model=Dict[str, Any])
async def get_database_schema(
    api_key = Depends(get_api_key)
):
    """
    Obtiene información sobre las colecciones y esquemas de la base de datos
    """
    try:
        # Verificar permisos básicos
        verify_permissions(api_key.level, "read")

        # Obtener información de la base de datos
        db_info = await db_service.get_database_info()

        # Filtrar colecciones según permisos
        collections_to_remove = []

        for collection_name in db_info["collections"]:
            if not await db_service.check_collection_access(api_key.level, collection_name):
                collections_to_remove.append(collection_name)

        for collection_name in collections_to_remove:
            del db_info["collections"][collection_name]
            
        return db_info
        
    except PermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(e)
        )
        
    except Exception as e:
        LogEntry("get_schema_error", "error") \
            .set_api_key_id(api_key.id) \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al obtener esquema de la base de datos"
        )