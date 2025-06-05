import json
import time
from typing import List, Dict, Any, Optional, Union, Tuple
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure
from datetime import datetime

from app.core.config import settings
from app.core.logging import LogEntry
from app.core.cache import Cache
from app.core.security import sanitize_mongo_query
from app.core.permissions import verify_permissions, check_collection_access
from app.models.database_query import MongoDBQuery, QueryResult, AIQueryResponse
from app.core.querys import AIQuery

import anthropic
import logging

db_client = AsyncIOMotorClient(settings.MONGODB.MONGODB_URL)
db = db_client[settings.MONGODB.MONGODB_DB_NAME]

# Configurar logger
logger = logging.getLogger(__name__)

async def get_collection_names() -> List[str]:
    """Obtiene los nombres de todas las colecciones en la base de datos"""
    return await db.list_collection_names()

async def get_collection_schema(collection_name: str, sample_size: int = 5) -> Dict[str, Any]:
    """
    Infiere el esquema de una colección basado en una muestra de documentos
    
    Args:
        collection_name: Nombre de la colección
        sample_size: Cantidad de documentos a analizar
        
    Returns:
        Esquema inferido con tipos de datos
    """
    collection = db[collection_name]
    
    # Obtener una muestra de documentos
    cursor = collection.find().limit(sample_size)
    documents = await cursor.to_list(length=sample_size)
    
    if not documents:
        return {}
    
    # Analizar estructura
    schema = {}
    
    for doc in documents:
        for key, value in doc.items():
            if key == "_id":  # Omitir campo _id
                continue
                
            # Determinar tipo
            value_type = type(value).__name__
            
            if key not in schema:
                schema[key] = {
                    "type": value_type,
                    "example": str(value)[:50] + ("..." if len(str(value)) > 50 else "")
                }
            else:
                # Si ya existe pero con otro tipo, marcarlo como mixto
                if schema[key]["type"] != value_type:
                    schema[key]["type"] = f"mixed({schema[key]['type']}, {value_type})"
    
    return schema

async def execute_query(
    collection_name: str, 
    query: Dict[str, Any], 
    api_key_level: str,
    projection: Optional[Dict[str, int]] = None,
    sort: Optional[Dict[str, int]] = None,
    limit: int = 100,
    skip: int = 0
) -> QueryResult:
    """
    Ejecuta una consulta find en una colección específica
    
    Args:
        collection_name: Nombre de la colección
        query: Filtro de la consulta
        api_key_level: Nivel de permisos del API key
        projection: Campos a incluir/excluir
        sort: Ordenamiento
        limit: Límite de resultados
        skip: Cantidad de resultados a saltar
        
    Returns:
        Resultado de la consulta
    """
    start_time = time.time()
    
    # Verificar permisos de acceso a la colección
    if not check_collection_access(api_key_level, collection_name):
        raise PermissionError(f"Sin acceso a la colección: {collection_name}")
    
    # Sanitizar la consulta
    safe_query = sanitize_mongo_query(query)
    
    try:
        collection = db[collection_name]
        
        # Contar total de documentos que coinciden (para pagination)
        total_count = await collection.count_documents(safe_query)
        
        # Ejecutar consulta
        cursor = collection.find(
            filter=safe_query,
            projection=projection
        )
        
        # Aplicar sort, skip y limit
        if sort:
            cursor = cursor.sort(list(sort.items()))
        
        cursor = cursor.skip(skip).limit(limit)
        
        # Convertir a lista
        results = await cursor.to_list(length=limit)
        
        # Convertir ObjectId a string para serialización JSON
        for doc in results:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
        
        # Calcular tiempo de ejecución
        query_time = (time.time() - start_time) * 1000  # ms
        
        return QueryResult(
            data=results,
            count=len(results),
            query_time_ms=query_time,
            has_more=total_count > skip + limit,
            metadata={
                "total_count": total_count,
                "skip": skip,
                "limit": limit,
                "collection": collection_name
            }
        )
    
    except Exception as e:
        # Registrar error
        LogEntry("db_query_error", "error") \
            .add_data("collection", collection_name) \
            .add_data("error", str(e)) \
            .log()
        
        raise

async def execute_aggregation(
    collection_name: str,
    pipeline: List[Dict[str, Any]],
    api_key_level: str
) -> QueryResult:
    """
    Ejecuta una agregación en una colección específica
    
    Args:
        collection_name: Nombre de la colección
        pipeline: Pipeline de agregación
        api_key_level: Nivel de permisos del API key
        
    Returns:
        Resultado de la agregación
    """
    start_time = time.time()
    
    # Verificar permisos de acceso a la colección
    if not check_collection_access(api_key_level, collection_name):
        raise PermissionError(f"Sin acceso a la colección: {collection_name}")
    
    # Sanitizar el pipeline
    safe_pipeline = [sanitize_mongo_query(stage) for stage in pipeline]
    
    try:
        collection = db[collection_name]
        
        # Ejecutar agregación
        cursor = collection.aggregate(safe_pipeline)
        
        # Convertir a lista
        results = await cursor.to_list(length=None)
        
        # Convertir ObjectId a string para serialización JSON
        for doc in results:
            if "_id" in doc:
                doc["_id"] = str(doc["_id"])
        
        # Calcular tiempo de ejecución
        query_time = (time.time() - start_time) * 1000  # ms
        
        return QueryResult(
            data=results,
            count=len(results),
            query_time_ms=query_time,
            has_more=False,  # No aplicable para agregaciones
            metadata={
                "collection": collection_name,
                "pipeline_stages": len(safe_pipeline)
            }
        )
    
    except Exception as e:
        # Registrar error
        LogEntry("db_aggregation_error", "error") \
            .add_data("collection", collection_name) \
            .add_data("error", str(e)) \
            .log()
        
        raise

async def get_database_name():
    """
    Retorna el nombre de la base de datos actual
    """
    # Asumiendo que db_service tiene acceso a la conexión de la base de datos
    # o al cliente de MongoDB
    return db_client.get_database().name

async def get_collection(collection_name: str):
    """
    Obtiene una referencia a una colección específica de la base de datos
    
    Args:
        collection_name: Nombre de la colección a obtener
        
    Returns:
        La colección de MongoDB solicitada
    """
    db = db_client.get_database()
    return db[collection_name]

async def get_database_info() -> Dict[str, Any]:
    """
    Obtiene información general sobre la base de datos
    """
    # Intentar obtener de caché
    cache_key = Cache.generate_key("db_info")
    cached_info = Cache.get(cache_key)
    
    if cached_info:
        return cached_info
    
    result = {"collections": {}}
    
    # Obtener nombres de colecciones
    collections = await get_collection_names()
    
    for collection_name in collections:
        collection = db[collection_name]
        
        # Obtener conteo de documentos
        count = await collection.count_documents({})
        
        # Obtener esquema inferido
        schema = await get_collection_schema(collection_name)
        
        # Agregar a resultado
        result["collections"][collection_name] = {
            "document_count": count,
            "schema": schema
        }
    
    # Guardar en caché (10 minutos)
    Cache.set(cache_key, result, ttl=600)
    
    return result


async def get_sql_database_info(db_config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Obtiene información de una base de datos SQL a partir de la configuración.
    
    Args:
        db_config: Configuración de la base de datos
        
    Returns:
        Información del esquema de la base de datos
    """
    try:
        # Obtener el tipo de motor
        engine = db_config.get("engine", "").lower()
        result = {"engine": engine, "tables": {}}
        
        # Intentar obtener de caché
        cache_key = Cache.generate_key("sql_schema", 
                                      db_config.get("host", ""),
                                      db_config.get("database", ""),
                                      db_config.get("config_id", ""))
        cached_info = Cache.get(cache_key)
        
        if cached_info:
            return cached_info
        
        if engine == "sqlite":
            # Implementación para SQLite
            import sqlite3
            database_path = db_config.get("database", "")
            
            if not database_path:
                raise ValueError("Ruta de base de datos SQLite no especificada")
            
            conn = sqlite3.connect(database_path)
            cursor = conn.cursor()
            
            # Obtener lista de tablas
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
            tables = cursor.fetchall()
            
            for table in tables:
                table_name = table[0]
                
                # Obtener información de columnas
                cursor.execute(f"PRAGMA table_info({table_name})")
                columns = cursor.fetchall()
                
                # Intentar obtener conteo aproximado
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table_name} LIMIT 1")
                    count = cursor.fetchone()[0]
                except:
                    count = "desconocido"
                
                # Crear estructura de esquema
                schema = {}
                for col in columns:
                    # col = (cid, name, type, notnull, dflt_value, pk)
                    schema[col[1]] = {
                        "type": col[2],
                        "nullable": not col[3],
                        "primary_key": col[5] == 1
                    }
                
                # Intentar obtener una muestra de datos
                try:
                    cursor.execute(f"SELECT * FROM {table_name} LIMIT 1")
                    sample = cursor.fetchone()
                    sample_dict = {}
                    if sample:
                        for i, col in enumerate(columns):
                            col_name = col[1]
                            sample_dict[col_name] = str(sample[i])
                except:
                    sample_dict = {}
                
                result["tables"][table_name] = {
                    "schema": schema,
                    "approx_count": count,
                    "sample": sample_dict
                }
            
            cursor.close()
            conn.close()
            
        elif engine == "mysql":
            # Implementación para MySQL
            import mysql.connector
            
            # Establecer conexión
            conn = mysql.connector.connect(
                host=db_config.get("host", "localhost"),
                user=db_config.get("user", ""),
                password=db_config.get("password", ""),
                database=db_config.get("database", ""),
                port=db_config.get("port", 3306)
            )
            cursor = conn.cursor(dictionary=True)
            
            # Obtener lista de tablas
            cursor.execute("SHOW TABLES")
            tables = cursor.fetchall()
            
            # El resultado es una lista de diccionarios, donde la clave varía según la configuración
            table_key = list(tables[0].keys())[0] if tables else None
            
            for table_data in tables:
                if not table_key:
                    continue
                    
                table_name = table_data[table_key]
                
                # Obtener información de columnas
                cursor.execute(f"DESCRIBE {table_name}")
                columns = cursor.fetchall()
                
                # Intentar obtener conteo aproximado
                try:
                    cursor.execute(f"SELECT COUNT(*) AS count FROM {table_name} LIMIT 1")
                    count_result = cursor.fetchone()
                    count = count_result["count"] if count_result else "desconocido"
                except:
                    count = "desconocido"
                
                # Crear estructura de esquema
                schema = {}
                for col in columns:
                    schema[col["Field"]] = {
                        "type": col["Type"],
                        "nullable": col["Null"] == "YES",
                        "primary_key": col["Key"] == "PRI"
                    }
                
                # Intentar obtener una muestra de datos
                try:
                    cursor.execute(f"SELECT * FROM {table_name} LIMIT 1")
                    sample = cursor.fetchone()
                    sample_dict = {}
                    if sample:
                        for col_name, value in sample.items():
                            sample_dict[col_name] = str(value)
                except:
                    sample_dict = {}
                
                result["tables"][table_name] = {
                    "schema": schema,
                    "approx_count": count,
                    "sample": sample_dict
                }
            
            cursor.close()
            conn.close()
            
        elif engine == "postgresql":
            # Implementación para PostgreSQL
            import psycopg2
            import psycopg2.extras
            
            # Establecer conexión
            conn = psycopg2.connect(
                host=db_config.get("host", "localhost"),
                user=db_config.get("user", ""),
                password=db_config.get("password", ""),
                dbname=db_config.get("database", ""),
                port=db_config.get("port", 5432)
            )
            conn.set_session(readonly=True)  # Para seguridad
            cursor = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
            
            # Obtener lista de tablas
            cursor.execute("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public';
            """)
            tables = cursor.fetchall()
            
            for table_data in tables:
                table_name = table_data[0]
                
                # Obtener información de columnas
                cursor.execute(f"""
                    SELECT column_name, data_type, is_nullable, 
                           CASE WHEN pk.column_name IS NOT NULL THEN TRUE ELSE FALSE END AS is_primary
                    FROM information_schema.columns c
                    LEFT JOIN (
                        SELECT ku.column_name
                        FROM information_schema.table_constraints tc
                        JOIN information_schema.key_column_usage ku ON tc.constraint_name = ku.constraint_name
                        WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_name = '{table_name}'
                    ) pk ON c.column_name = pk.column_name
                    WHERE c.table_name = '{table_name}'
                """)
                columns = cursor.fetchall()
                
                # Intentar obtener conteo aproximado
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM {table_name} LIMIT 1")
                    count = cursor.fetchone()[0]
                except:
                    count = "desconocido"
                
                # Crear estructura de esquema
                schema = {}
                for col in columns:
                    schema[col[0]] = {
                        "type": col[1],
                        "nullable": col[2] == "YES",
                        "primary_key": col[3]
                    }
                
                # Intentar obtener una muestra de datos
                try:
                    cursor.execute(f"SELECT * FROM {table_name} LIMIT 1")
                    sample = cursor.fetchone()
                    sample_dict = {}
                    if sample:
                        for col_name in sample.keys():
                            sample_dict[col_name] = str(sample[col_name])
                except:
                    sample_dict = {}
                
                result["tables"][table_name] = {
                    "schema": schema,
                    "approx_count": count,
                    "sample": sample_dict
                }
            
            cursor.close()
            conn.close()
        
        else:
            logger.warning(f"Motor SQL desconocido: {engine}")
        
        # Guardar en caché (10 minutos)
        Cache.set(cache_key, result, ttl=600)
        
        return result
        
    except Exception as e:
        logger.error(f"Error al obtener información de base de datos SQL: {str(e)}")
        # Devolver información mínima en caso de error
        return {
            "engine": db_config.get("engine", "unknown"),
            "error": str(e),
            "tables": {}
        }

async def process_sql_query(
    query: str,
    user_id: Optional[str],
    api_key_id: str,
    api_key_level: str,
    collection_name: Optional[str],
    config_id: Optional[str],
    db_config: Dict[str, Any],
    query_metadata: Dict[str, Any]
) -> AIQueryResponse:
    """
    Procesa una consulta en lenguaje natural para bases de datos SQL.
    
    Args:
        query: Consulta en lenguaje natural
        user_id: ID del usuario (opcional)
        api_key_id: ID de la API key
        api_key_level: Nivel de permisos de la API key
        collection_name: Nombre de la tabla específica (opcional)
        config_id: ID de configuración del SDK
        db_config: Configuración de la base de datos del SDK
        query_metadata: Metadatos de la consulta para tracking
        
    Returns:
        Respuesta estructurada con los resultados y explicación
    """
    start_time = time.time()
    engine = db_config.get("engine", "").lower()
    
    # Log específico para consulta SQL
    LogEntry("sql_query_started", "info") \
        .set_user_id(user_id) \
        .set_api_key_id(api_key_id) \
        .add_data("query", query) \
        .add_data("engine", engine) \
        .add_data("config_id", config_id) \
        .log()
    
    # Obtener información del esquema SQL
    db_info = await get_sql_database_info(db_config)
    
    # Intentar obtener respuesta de caché
    cache_key = Cache.generate_key("sql_query", query, collection_name, config_id)
    cached_response = Cache.get(cache_key)
    
    sql_query = None
    if cached_response:
        LogEntry("sql_query_cache_hit", "debug") \
            .set_user_id(user_id) \
            .set_api_key_id(api_key_id) \
            .log()
        
        sql_query = cached_response
    else:
        LogEntry("sql_query_cache_miss", "debug") \
            .set_user_id(user_id) \
            .set_api_key_id(api_key_id) \
            .log()
            
        # Generar consulta SQL a través de la IA
        sql_query = await AIQuery.generate_sql_query(query, db_info, engine)
        
        # Guardar en caché (1 hora)
        Cache.set(cache_key, sql_query, ttl=3600)
    
    # Ejecutar la consulta SQL
    try:
        result_data, execution_time = await AIQuery.execute_sql_query(sql_query, db_config)
        
        # Crear objeto de resultado
        result = QueryResult(
            data=result_data,
            count=len(result_data),
            query_time_ms=execution_time * 1000,  # Convertir a ms
            has_more=len(result_data) >= 100,  # Suponemos que hay más si alcanzamos el límite
            metadata={
                "sql_query": sql_query,
                "engine": engine,
                "database": db_config.get("database", ""),
                "execution_time": execution_time
            }
        )
        
        # Crear un objeto MongoDBQuery equivalente para mantener compatibilidad
        mongo_query = MongoDBQuery(
            collection=collection_name or "sql_table",
            operation="sql",
            query={"sql": sql_query},
            limit=100
        )
        
        # Generar explicación de los resultados
        explanation = await AIQuery.generate_sql_result_explanation(query, sql_query, result)
        
        # Calcular tiempo total de procesamiento
        processing_time = time.time() - start_time
        
        # Registrar consulta exitosa
        LogEntry("sql_query_processed", "info") \
            .set_user_id(user_id) \
            .set_api_key_id(api_key_id) \
            .add_data("query", query) \
            .add_data("sql", sql_query) \
            .add_data("engine", engine) \
            .add_data("processing_time", processing_time) \
            .add_data("result_count", result.count) \
            .log()
        
        # Crear respuesta final
        return AIQueryResponse(
            natural_query=query,
            mongo_query=mongo_query,  # Usamos el objeto compatible
            result=result,
            explanation=explanation,
            processed_at=datetime.now(),
            metadata={
                "processing_time": processing_time,
                "anthropic_model": settings.ANTHROPIC.ANTHROPIC_MODEL,
                "api_key_id": api_key_id,
                "user_id": user_id,
                "config_id": config_id,
                "db_type": "sql",
                "engine": engine
            }
        )
        
    except Exception as e:
        # Registro de error
        error_msg = f"Error al ejecutar consulta SQL: {str(e)}"
        LogEntry("sql_query_error", "error") \
            .set_user_id(user_id) \
            .set_api_key_id(api_key_id) \
            .add_data("query", query) \
            .add_data("sql", sql_query) \
            .add_data("error", error_msg) \
            .log()
            
        # Crear resultado vacío
        result = QueryResult(
            data=[],
            count=0,
            query_time_ms=0,
            has_more=False,
            metadata={
                "sql_query": sql_query,
                "engine": engine,
                "error": error_msg
            }
        )
        
        # Crear objeto MongoDBQuery para compatibilidad
        mongo_query = MongoDBQuery(
            collection=collection_name or "sql_table",
            operation="sql",
            query={"sql": sql_query, "error": error_msg},
            limit=100
        )
        
        # Crear respuesta con error
        return AIQueryResponse(
            natural_query=query,
            mongo_query=mongo_query,
            result=result,
            explanation=f"Se produjo un error al ejecutar la consulta SQL: {error_msg}",
            processed_at=datetime.now(),
            metadata={
                "error": True,
                "error_message": error_msg,
                "api_key_id": api_key_id,
                "user_id": user_id,
                "config_id": config_id
            }
        )

async def process_natural_language_query(
    query: str, 
    user_id: Optional[str],
    api_key_id: str,
    api_key_level: str,
    collection_name: Optional[str] = None,
    config_id: Optional[str] = None,
    db_config: Optional[Dict[str, Any]] = None
) -> AIQueryResponse:
    """
    Procesa una consulta en lenguaje natural y la traduce a operaciones de base de datos.
    
    Args:
        query: Consulta en lenguaje natural
        user_id: ID del usuario (opcional)
        api_key_id: ID de la API key
        api_key_level: Nivel de permisos de la API key
        collection_name: Nombre de la colección específica (opcional)
        config_id: ID de configuración del SDK (opcional)
        db_config: Configuración de la base de datos del SDK (opcional)
        
    Returns:
        Respuesta estructurada con los resultados y explicación
    """
    start_time = time.time()
    
    # Verificar permisos básicos
    verify_permissions(api_key_level, "read")
    
    # Si se especifica una colección, verificar acceso
    if collection_name and not check_collection_access(api_key_level, collection_name):
        raise PermissionError(f"Sin acceso a la colección: {collection_name}")
    
    # Registrar metadatos de la consulta para logs y tracking
    query_metadata = {
        "api_key_id": api_key_id,
        "user_id": user_id,
        "collection_name": collection_name,
        "config_id": config_id,
        "start_time": start_time,
        "query_type": "sdk" if db_config else "direct"
    }
    
    # Log inicial
    LogEntry("nl_query_started", "info") \
        .set_user_id(user_id) \
        .set_api_key_id(api_key_id) \
        .add_data("query", query) \
        .add_data("collection", collection_name) \
        .add_data("config_id", config_id) \
        .log()
    
    # Decidir qué tipo de base de datos consultar según la configuración
    if db_config and "type" in db_config:
        if db_config["type"] == "sql":
            # Procesar consulta SQL usando la configuración del SDK
            return await process_sql_query(
                query, user_id, api_key_id, api_key_level, 
                collection_name, config_id, db_config, query_metadata
            )
        elif db_config["type"] == "nosql" and db_config.get("engine") == "mongodb":
            # Para MongoDB con configuración específica
            db_info = await get_database_info(collection_name)
        else:
            # Tipo de base de datos no soportado, usar la DB por defecto
            LogEntry("unsupported_db_type", "warning") \
                .set_api_key_id(api_key_id) \
                .add_data("type", db_config["type"]) \
                .log()
            db_info = await get_database_info(collection_name)
    else:
        # Sin configuración específica, usar la DB por defecto
        db_info = await get_database_info(collection_name)
    
    # Intentar obtener respuesta de caché
    cache_key = Cache.generate_key("nl_query", query, collection_name, config_id)
    cached_response = Cache.get(cache_key)
    
    mongo_query = None
    if cached_response:
        LogEntry("nl_query_cache_hit", "debug") \
            .set_user_id(user_id) \
            .set_api_key_id(api_key_id) \
            .log()
            
        mongo_query = MongoDBQuery(**cached_response)
    else:
        LogEntry("nl_query_cache_miss", "debug") \
            .set_user_id(user_id) \
            .set_api_key_id(api_key_id) \
            .log()
            
        # Generar consulta MongoDB a través de la IA
        mongo_query = await AIQuery.generate_mongodb_query(query, db_info)
        
        # Guardar en caché (1 hora)
        Cache.set(cache_key, mongo_query.model_dump(), ttl=3600)
    
    # Si se especificó una colección y no coincide con la inferida, usamos la especificada
    if collection_name and mongo_query.collection != collection_name:
        LogEntry("collection_mismatch", "info") \
            .set_api_key_id(api_key_id) \
            .add_data("specified", collection_name) \
            .add_data("inferred", mongo_query.collection) \
            .log()
            
        mongo_query.collection = collection_name
    
    # Ejecutar la consulta MongoDB
    if mongo_query.operation == "find":
        result = await execute_query(
            collection_name=mongo_query.collection,
            query=mongo_query.query or {},
            api_key_level=api_key_level,
            sort=mongo_query.sort,
            limit=mongo_query.limit,
            skip=mongo_query.skip
        )
    elif mongo_query.operation == "aggregate":
        result = await execute_aggregation(
            collection_name=mongo_query.collection,
            pipeline=mongo_query.pipeline or [],
            api_key_level=api_key_level
        )
    else:
        raise ValueError(f"Operación no soportada: {mongo_query.operation}")
    
    # Generar explicación de los resultados
    explanation = await generate_result_explanation(query, mongo_query, result)
    
    # Calcular tiempo total de procesamiento
    processing_time = time.time() - start_time
    
    # Registrar consulta exitosa
    LogEntry("nl_query_processed", "info") \
        .set_user_id(user_id) \
        .set_api_key_id(api_key_id) \
        .add_data("query", query) \
        .add_data("collection", mongo_query.collection) \
        .add_data("operation", mongo_query.operation) \
        .add_data("processing_time", processing_time) \
        .add_data("result_count", result.count) \
        .log()
    
    # Crear respuesta final
    return AIQueryResponse(
        natural_query=query,
        mongo_query=mongo_query,
        result=result,
        explanation=explanation,
        processed_at=datetime.now(),
        metadata={
            "processing_time": processing_time,
            "anthropic_model": settings.ANTHROPIC.ANTHROPIC_MODEL,
            "api_key_id": api_key_id,
            "user_id": user_id,
            "config_id": config_id,
            "db_config_type": db_config["type"] if db_config and "type" in db_config else "default"
        }
    )


async def generate_result_explanation(
    query: str,
    mongo_query: MongoDBQuery,
    result: QueryResult
) -> str:
    """
    Genera una explicación en lenguaje natural de los resultados de una consulta
    
    Args:
        query: Consulta original en lenguaje natural
        mongo_query: Consulta MongoDB generada
        result: Resultado de la consulta
        
    Returns:
        Explicación en lenguaje natural
    """
    # Limitar resultado para el prompt
    result_sample = result.data[:5]
    
    # Preparar contexto
    context = {
        "original_query": query,
        "mongodb_query": mongo_query.model_dump(),
        "result_count": result.count,
        "total_count": result.metadata.get("total_count", result.count),
        "result_sample": result_sample,
        "query_time_ms": result.query_time_ms
    }
    
    context_json = json.dumps(context, indent=2)
    
    # Generar prompt para Anthropic
    system_prompt = """
    Eres un asistente especializado en explicar resultados de consultas a bases de datos.
    Debes explicar los resultados de manera clara y concisa, destacando los aspectos más relevantes.
    
    Algunas pautas:
    1. Menciona cuántos resultados se encontraron
    2. Resume los hallazgos principales
    3. Si hay pocos o ningún resultado, sugiere posibles razones
    4. Evita tecnicismos innecesarios
    5. Sé breve y directo
    """
    
    try:
        # Inicializar cliente Anthropic
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC.ANTHROPIC_API_KEY)
        
        # Enviar solicitud a Anthropic
        response = client.messages.create(
            model=settings.ANTHROPIC.ANTHROPIC_MODEL,
            max_tokens=settings.ANTHROPIC.MAX_TOKENS,
            temperature=0.7,
            messages=[{"role": "user", "content": f"Explica los siguientes resultados de consulta:\n{context_json}"}],
            system=system_prompt
        )
        
        # Extraer explicación
        explanation = response.content[0].text
        
        return explanation
    
    except Exception as e:
        # En caso de error, generar explicación básica
        if result.count == 0:
            return "No se encontraron resultados para tu consulta."
        else:
            return f"Se encontraron {result.count} resultados en la colección {mongo_query.collection}."
