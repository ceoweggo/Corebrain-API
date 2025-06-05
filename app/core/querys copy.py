from typing import Dict, Any, Optional, List, Tuple, Union
from bson.json_util import dumps, loads

import json
import re
import time
import openai
import motor.motor_asyncio

from app.core.config import settings
from app.core.logging import logger
from app.core.utils import Utils
from app.core.diagnostic import Diagnostic
from app.models.database_query import MongoDBQuery, QueryResult

class AIQuery:
    def __init__(self, query: str, collection_name: str = None, limit: int = 50, 
                 config_id: Optional[str] = None, db_schema: Optional[Dict[str, Any]] = None):
        """
        Inicializa un objeto AIQuery para procesar consultas en lenguaje natural.
        
        Args:
            query: Consulta en lenguaje natural
            collection_name: Nombre de la colección/tabla a consultar (opcional)
            limit: Límite de resultados (por defecto 50)
            config_id: ID de configuración (opcional)
            db_schema: Esquema de la base de datos (opcional)
        """
        self.query = query
        self.collection_name = collection_name
        self.limit = limit
        self.config_id = config_id
        self.db_schema = db_schema

    @staticmethod
    async def generate_sql_query(
        query: str, 
        db_info: Dict[str, Any],
        engine: str
    ) -> str:
        """
        Genera una consulta SQL a partir de una consulta en lenguaje natural y la información de la base de datos.
        
        Args:
            query: Consulta en lenguaje natural
            db_info: Información del esquema de la base de datos
            engine: Motor de base de datos (sqlite, mysql, postgresql)
            
        Returns:
            Consulta SQL generada
        """
        # Preparar el contexto con la información de la base de datos
        db_context = json.dumps(db_info, indent=2, default=str)  # Usar default=str para manejar tipos especiales
        
        # Limitar el contexto si es demasiado grande
        if len(db_context) > 10000:
            # Extraer solo las primeras tablas
            tables = list(db_info.get("tables", {}).keys())
            truncated_tables = tables[:5]
            
            truncated_db_info = {
                "engine": db_info.get("engine", engine),
                "tables": {
                    name: db_info["tables"][name]
                    for name in truncated_tables if name in db_info.get("tables", {})
                }
            }
            
            db_context = json.dumps(truncated_db_info, indent=2, default=str)
            db_context += f"\n\n... y {len(tables) - 5} tablas más."
        
        # Crear system prompt específico para SQL
        system_prompt = f"""
        Eres un asistente especializado en traducir consultas en lenguaje natural a SQL.
        
        ESTRUCTURA DE LA BASE DE DATOS:
        {db_context}
        
        MOTOR DE BASE DE DATOS: {engine}
        
        Tu tarea es:
        1. Analizar la consulta del usuario
        2. Determinar qué tablas deben ser consultadas
        3. Construir una consulta SQL válida para el motor {engine}
        4. Devolver SOLO la consulta SQL, sin ningún otro texto o explicación
        
        REGLAS:
        - Usa la sintaxis específica de {engine}
        - Para consultas de agregación, usa GROUP BY cuando sea necesario
        - Limita los resultados a 100 filas como máximo usando LIMIT 100
        - No uses características avanzadas específicas de versiones recientes que pueden no estar disponibles
        - Si la consulta no es clara, genera una consulta simple que obtenga información relevante
        
        Responde ÚNICAMENTE con la consulta SQL, sin ningún otro texto ni explicación.
        """
        
        print("Prompt a pasar a la IA: ", system_prompt)
        
        try:
            # Inicializar cliente de OpenAI
            client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)
            
            # Enviar solicitud a OpenAI
            response = await client.chat.completions.create(
                model=settings.OPENAI.OPENAI_MODEL,
                max_tokens=settings.OPENAI.MAX_TOKENS,
                temperature=0.2,  # Temperatura baja para respuestas más deterministas
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ]
            )
            
            # Extraer la consulta SQL generada
            sql_query = response.choices[0].message.content.strip()
            
            # Limpiar la consulta (eliminar comentarios, etc.)
            sql_query = AIQuery.clean_sql_query(sql_query)
            
            return sql_query
            
        except Exception as e:
            logger.error(f"Error al generar consulta SQL: {str(e)}")
            # En caso de error, generar una consulta segura y simple
            safe_table = next(iter(db_info.get("tables", {}).keys()), "users")
            return f"SELECT * FROM {safe_table} LIMIT 10"


    def generate_default_mongo_explanation(mongo_query, result_data):
        """
        Genera una explicación predeterminada para consultas MongoDB cuando la IA falla.
        
        Args:
            mongo_query: La consulta MongoDB ejecutada
            result_data: Los resultados obtenidos
            
        Returns:
            Explicación generada en texto plano
        """
        # Obtener información de la consulta de manera segura
        collection = "desconocida"
        if hasattr(mongo_query, "collection"):
            collection = mongo_query.collection
        elif isinstance(mongo_query, dict) and "collection" in mongo_query:
            collection = mongo_query["collection"]
        
        operation = "find"
        if hasattr(mongo_query, "operation"):
            operation = mongo_query.operation
        elif isinstance(mongo_query, dict) and "operation" in mongo_query:
            operation = mongo_query["operation"]
        
        # Normalizar la operación
        operation = operation.lower() if isinstance(operation, str) else "find"
        
        # Obtener el recuento de resultados
        result_count = 0
        if isinstance(result_data, list):
            result_count = len(result_data)
        elif result_data is not None:
            result_count = 1
        
        # Determinar tipo de operación y generar explicación apropiada
        if operation == "find":
            if result_count == 0:
                return f"No se encontraron documentos en la colección {collection} que coincidan con los criterios de búsqueda."
            else:
                return f"Se encontraron {result_count} documentos en la colección {collection} que coinciden con los criterios de búsqueda."
        
        elif operation in ["findone", "find_one"]:
            if result_count > 0:
                return f"Se encontró el documento solicitado en la colección {collection}."
            else:
                return f"No se encontró ningún documento en la colección {collection} que coincida con los criterios de búsqueda."
        
        elif operation == "aggregate":
            return f"La agregación en la colección {collection} devolvió {result_count} resultados."
        
        elif operation in ["count", "countdocuments", "count_documents"]:
            if isinstance(result_data, list) and len(result_data) > 0 and "count" in result_data[0]:
                count = result_data[0]["count"]
                return f"Se encontraron {count} documentos en la colección {collection} que coinciden con los criterios especificados."
            else:
                return f"Se realizó un conteo de documentos en la colección {collection}."
        
        elif operation in ["insertone", "insert_one"]:
            return f"Se ha insertado correctamente un nuevo documento en la colección {collection}."
        
        elif operation in ["updateone", "update_one"]:
            if isinstance(result_data, list) and len(result_data) > 0:
                matched = result_data[0].get("matchedCount", 0)
                modified = result_data[0].get("modifiedCount", 0)
                
                if matched == 0:
                    return f"No se encontró ningún documento en la colección {collection} que coincida con los criterios para actualizar."
                elif modified == 0:
                    return f"Se encontró un documento en la colección {collection} pero no se realizaron cambios (los valores ya son idénticos)."
                else:
                    return f"Se actualizó correctamente un documento en la colección {collection}."
            else:
                return f"Se realizó una operación de actualización en la colección {collection}."
        
        elif operation in ["deleteone", "delete_one"]:
            if isinstance(result_data, list) and len(result_data) > 0 and "deletedCount" in result_data[0]:
                deleted = result_data[0]["deletedCount"]
                if deleted == 0:
                    return f"No se encontró ningún documento en la colección {collection} que coincida con los criterios para eliminar."
                else:
                    return f"Se eliminó correctamente un documento de la colección {collection}."
            else:
                return f"Se realizó una operación de eliminación en la colección {collection}."
        
        # Fallback general
        return f"Se ejecutó la operación {operation} en la colección {collection} y se obtuvieron {result_count} resultados."
    
    @staticmethod
    async def generate_mongodb_query(
        query: str,
        db_info: Dict[str, Any],
        collection_name: Optional[str] = None,
        db_connection = None
    ) -> Union[MongoDBQuery, Dict[str, Any]]:
        """
        Genera una consulta MongoDB a partir de una consulta en lenguaje natural.
        Adaptado para funcionar con bases de datos de clientes sin conocimiento previo de colecciones.
        
        Args:
            query: Consulta en lenguaje natural
            db_info: Información del esquema de la base de datos
            collection_name: Nombre de la colección a consultar (opcional)
            db_connection: Conexión a la base de datos para exploración adicional (opcional)
            
        Returns:
            Objeto MongoDBQuery con la consulta generada o un diccionario con la misma estructura
        """
        # Inicializar mongo_query como None para prevenir errores de referencia antes de asignación
        mongo_query = None
        
        try:
            # Preparar el contexto con la información de la base de datos
            db_context = json.dumps(db_info, indent=2, default=str)
            
            # Obtener colecciones disponibles
            available_collections = []
            if "tables" in db_info:
                available_collections = list(db_info.get("tables", {}).keys())
            elif "collections" in db_info:
                available_collections = list(db_info.get("collections", {}).keys())
            
            # Si se proporcionó una colección específica, usarla
            if collection_name:
                selected_collection = collection_name
            else:
                # Intentar determinar la mejor colección basada en la consulta
                selected_collection = Utils.determine_best_collection(query, available_collections, db_connection)
                logger.info(f"Colección seleccionada automáticamente: {selected_collection}")
            
            # Crear system prompt específico para la colección seleccionada
            collection_info = ""
            if selected_collection and selected_collection in db_info.get("tables", {}):
                # Incluir información específica sobre la colección seleccionada
                try:
                    collection_data = db_info["tables"][selected_collection]
                    collection_info = f"\nINFORMACIÓN DE LA COLECCIÓN SELECCIONADA ({selected_collection}):\n"
                    
                    # Incluir campos/estructura
                    if "columns" in collection_data or "fields" in collection_data:
                        fields = collection_data.get("columns", collection_data.get("fields", []))
                        collection_info += f"Campos: {json.dumps([f.get('name') for f in fields], default=str)}\n"
                    
                    # Incluir muestra de documentos si está disponible
                    if "sample_data" in collection_data and collection_data["sample_data"]:
                        collection_info += f"Muestra de documentos: {json.dumps(collection_data['sample_data'][:2], default=str)}\n"
                        
                except Exception as e:
                    logger.warning(f"Error al preparar información de colección: {str(e)}")
            
            # Crear system prompt para consulta MongoDB
            system_prompt = f"""
            Eres un asistente especializado en traducir consultas en lenguaje natural a operaciones MongoDB.
            
            ESTRUCTURA DE LA BASE DE DATOS:
            {db_context}
            
            {collection_info}
            
            Tu tarea es:
            1. Analizar cuidadosamente la consulta del usuario
            2. Utilizar la colección '{selected_collection}' para esta consulta
            3. Construir la consulta apropiada (find o aggregate)
            4. Devolver la consulta como un objeto JSON con el siguiente formato:
            
            Para búsquedas simples:
            {{
            "collection": "{selected_collection}",
            "operation": "find",
            "query": {{ /* filtros */ }},
            "filter": {{ /* mismo que query, para compatibilidad */ }},
            "projection": {{ /* campos a incluir/excluir */ }},
            "sort": {{ /* ordenamiento */ }},
            "limit": 10
            }}
            
            Para agregaciones:
            {{
            "collection": "{selected_collection}",
            "operation": "aggregate",
            "pipeline": [
                {{ /* etapa 1 */ }},
                {{ /* etapa 2 */ }}
            ]
            }}
            
            IMPORTANTE: 
            - Utiliza exactamente la colección "{selected_collection}" en tu respuesta.
            - Incluye SIEMPRE tanto "query" como "filter" con el mismo valor para compatibilidad.
            
            Responde ÚNICAMENTE con el objeto JSON, sin ningún otro texto.
            """
            
            try:
                # Inicializar cliente de OpenAI
                client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)
                
                # Enviar solicitud a OpenAI
                response = await client.chat.completions.create(
                    model=settings.OPENAI.OPENAI_MODEL,
                    max_tokens=settings.OPENAI.MAX_TOKENS,
                    temperature=0.2,  # Temperatura baja para respuestas más deterministas
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": query}
                    ]
                )
                
                # Extraer y procesar respuesta JSON
                ai_response = response.choices[0].message.content
                json_text = AIQuery.clean_json_response(ai_response)
                
                # Parsear JSON
                query_data = json.loads(json_text)
                
                # Verificar que se está usando la colección seleccionada
                if query_data.get("collection") != selected_collection:
                    logger.warning(f"La IA seleccionó una colección diferente: {query_data.get('collection')}. Forzando uso de {selected_collection}")
                    query_data["collection"] = selected_collection
                
                # Asegurar que tanto query como filter estén presentes y sincronizados
                if "query" in query_data and "filter" not in query_data:
                    query_data["filter"] = query_data["query"]
                elif "filter" in query_data and "query" not in query_data:
                    query_data["query"] = query_data["filter"]
                elif "query" not in query_data and "filter" not in query_data:
                    query_data["query"] = {}
                    query_data["filter"] = {}
                
                # Primero crear un diccionario completo para usar en caso de error
                mongo_query_dict = {
                    "collection": query_data["collection"],
                    "operation": query_data.get("operation", "find"),
                    "query": query_data.get("query", {}),
                    "filter": query_data.get("filter", {}),
                    "pipeline": query_data.get("pipeline", []),
                    "projection": query_data.get("projection"),
                    "sort": query_data.get("sort"),
                    "limit": query_data.get("limit", 10),
                    "skip": query_data.get("skip", 0)
                }
                
                # Intentar crear el objeto MongoDBQuery
                try:
                    # Crear objeto MongoDBQuery
                    mongo_query = MongoDBQuery(
                        collection=mongo_query_dict["collection"],
                        operation=mongo_query_dict["operation"],
                        query=mongo_query_dict["query"],
                        filter=mongo_query_dict["filter"],
                        pipeline=mongo_query_dict["pipeline"],
                        projection=mongo_query_dict["projection"],
                        sort=mongo_query_dict["sort"],
                        limit=mongo_query_dict["limit"],
                        skip=mongo_query_dict["skip"]
                    )
                except Exception as model_error:
                    logger.warning(f"Error al crear objeto MongoDBQuery: {str(model_error)}. Usando diccionario en su lugar.")
                    mongo_query = mongo_query_dict
                
                # Registrar la consulta generada
                if hasattr(mongo_query, 'dict'):
                    logger.info(f"Consulta MongoDB generada para colección {mongo_query.collection}: {json.dumps(mongo_query.dict(), default=str)}")
                else:
                    logger.info(f"Consulta MongoDB generada (diccionario): {json.dumps(mongo_query, default=str)}")
                
                return mongo_query
                
            except Exception as e:
                logger.error(f"Error al generar consulta MongoDB con IA: {str(e)}")
                
                # Realizar diagnóstico de la conexión si está disponible
                if db_connection:
                    try:
                        debug_result = await Diagnostic.debug_mongodb_connection(db_connection, collection_name)
                        logger.info(f"Diagnóstico MongoDB: {json.dumps(debug_result, default=str)}")
                        
                        # Usar información del diagnóstico para una mejor selección de fallback
                        if debug_result.get("available_collections"):
                            available_collections = debug_result["available_collections"]
                    except Exception as debug_error:
                        logger.error(f"Error al realizar diagnóstico: {str(debug_error)}")
                
                # Determinar la colección a usar
                final_collection = selected_collection or (available_collections[0] if available_collections else "users")
                
                # Crear un diccionario de consulta seguro
                mongo_query_dict = {
                    "collection": final_collection,
                    "operation": "find",
                    "query": {},
                    "filter": {},
                    "limit": 10,
                    "skip": 0
                }
                
                # Intentar crear el objeto MongoDBQuery o devolver el diccionario
                try:
                    mongo_query = MongoDBQuery(**mongo_query_dict)
                    return mongo_query
                except Exception as model_error:
                    logger.warning(f"Error al crear objeto MongoDBQuery fallback: {str(model_error)}. Usando diccionario.")
                    return mongo_query_dict
                    
        except Exception as e:
            # Error global - asegurar que siempre devolvemos algo válido
            logger.error(f"Error crítico en generate_mongodb_query: {str(e)}")
            
            # Determinar una colección segura
            safe_collection = collection_name
            if not safe_collection:
                safe_collection = next(iter(available_collections), "users") if 'available_collections' in locals() else "users"
            
            # Crear un diccionario de consulta mínima
            fallback_query = {
                "collection": safe_collection,
                "operation": "find",
                "query": {},
                "filter": {},
                "limit": 10
            }
            
            # Intentar crear objeto o devolver diccionario
            try:
                return MongoDBQuery(**fallback_query)
            except:
                return fallback_query
    
    @staticmethod
    def clean_sql_query(sql_query: str) -> str:
        """
        Limpia una consulta SQL eliminando comentarios, backticks y otros elementos innecesarios.
        
        Args:
            sql_query: Consulta SQL a limpiar
            
        Returns:
            Consulta SQL limpia
        """
        # Eliminar bloques de código markdown
        if sql_query.startswith('```') and sql_query.endswith('```'):
            sql_query = sql_query[3:-3].strip()
        elif '```' in sql_query:
            # Extraer contenido entre las primeras comillas de código triple
            match = re.search(r'```(?:sql)?(.*?)```', sql_query, re.DOTALL)
            if match:
                sql_query = match.group(1).strip()
        
        # Eliminar especificadores de lenguaje al inicio
        if sql_query.lower().startswith('sql'):
            sql_query = sql_query[3:].strip()
        
        # Eliminar comentarios de una línea
        sql_query = re.sub(r'--.*$', '', sql_query, flags=re.MULTILINE)
        
        # Eliminar comentarios de múltiples líneas
        sql_query = re.sub(r'/\*.*?\*/', '', sql_query, flags=re.DOTALL)
        
        # Eliminar líneas vacías y espacios extras
        sql_query = '\n'.join(line.strip() for line in sql_query.split('\n') if line.strip())
        
        return sql_query

    @staticmethod
    async def execute_sql_query(
        sql_query: str,
        db_config: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], float]:
        """
        Ejecuta una consulta SQL en la base de datos configurada.
        
        Args:
            sql_query: Consulta SQL a ejecutar
            db_config: Configuración de la base de datos
            
        Returns:
            Tupla con (resultado, tiempo_ejecución)
        """
        start_time = time.time()
        engine = db_config.get("engine", "").lower()
        result_data = []
        
        try:
            if engine == "sqlite":
                result_data = await AIQuery.execute_sqlite_query(sql_query, db_config)
            elif engine == "mysql":
                result_data = await AIQuery.execute_mysql_query(sql_query, db_config)
            elif engine == "postgresql":
                result_data = await AIQuery.execute_postgresql_query(sql_query, db_config)
            else:
                raise ValueError(f"Motor SQL no soportado: {engine}")
            
            # Calcular tiempo de ejecución
            execution_time = time.time() - start_time
            
            # Limitar resultados si son demasiados
            if len(result_data) > 100:
                result_data = result_data[:100]
            
            return result_data, execution_time
            
        except Exception as e:
            # Registrar error
            logger.error(f"Error al ejecutar consulta SQL ({engine}): {str(e)}")
            execution_time = time.time() - start_time
            raise

    @staticmethod
    async def execute_sqlite_query(sql_query: str, db_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Ejecuta una consulta en SQLite."""
        import sqlite3
        import aiosqlite
        
        database_path = db_config.get("database", "")
        if not database_path:
            raise ValueError("Ruta de base de datos SQLite no especificada")
        
        async with aiosqlite.connect(database_path) as db:
            # Configurar para obtener resultados como diccionarios
            db.row_factory = aiosqlite.Row
            
            # Ejecutar la consulta
            cursor = await db.execute(sql_query)
            rows = await cursor.fetchall()
            
            # Convertir a lista de diccionarios
            result = []
            for row in rows:
                # Convertir Row a dict
                row_dict = {key: row[key] for key in row.keys()}
                # Serializar valores especiales
                for key, value in row_dict.items():
                    if hasattr(value, 'isoformat') and callable(getattr(value, 'isoformat')):
                        row_dict[key] = value.isoformat()
                    elif isinstance(value, (bytes, bytearray)):
                        row_dict[key] = value.hex()
                
                result.append(row_dict)
            
            return result

    @staticmethod
    async def execute_mysql_query(sql_query: str, db_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Ejecuta una consulta en MySQL."""
        import aiomysql
        
        # Extraer parámetros de conexión
        host = db_config.get("host", "localhost")
        port = db_config.get("port", 3306)
        user = db_config.get("user", "")
        password = db_config.get("password", "")
        database = db_config.get("database", "")
        
        # Crear pool de conexiones
        pool = await aiomysql.create_pool(
            host=host,
            port=port,
            user=user,
            password=password,
            db=database,
            autocommit=True
        )
        
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                # Ejecutar la consulta
                await cursor.execute(sql_query)
                rows = await cursor.fetchall()
                
                # Convertir resultados
                result = []
                for row in rows:
                    # Convertir valores no serializables (como bytes, datetime, etc.)
                    row_dict = {}
                    for key, value in row.items():
                        if isinstance(value, (bytes, bytearray)):
                            row_dict[key] = value.decode('utf-8', errors='replace')
                        elif hasattr(value, 'isoformat'):  # datetime, date, time
                            row_dict[key] = value.isoformat()
                        else:
                            row_dict[key] = value
                    
                    result.append(row_dict)
                
                return result
        
        # Cerrar el pool
        pool.close()
        await pool.wait_closed()

    @staticmethod
    async def execute_postgresql_query(sql_query: str, db_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Ejecuta una consulta en PostgreSQL."""
        import asyncpg
        
        # Extraer parámetros de conexión
        host = db_config.get("host", "localhost")
        port = db_config.get("port", 5432)
        user = db_config.get("user", "")
        password = db_config.get("password", "")
        database = db_config.get("database", "")
        
        # Conectar a la base de datos
        conn = await asyncpg.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database
        )
        
        try:
            # Ejecutar la consulta
            rows = await conn.fetch(sql_query)
            
            # Convertir Record a dict
            result = []
            for row in rows:
                # Convertir asyncpg.Record a dict
                row_dict = dict(row)
                
                # Convertir valores no serializables
                for key, value in row_dict.items():
                    if hasattr(value, 'isoformat'):  # datetime, date, time
                        row_dict[key] = value.isoformat()
                    elif isinstance(value, asyncpg.BitString):
                        row_dict[key] = str(value)
                    elif isinstance(value, (bytes, bytearray)):
                        row_dict[key] = value.hex()
                
                result.append(row_dict)
            
            return result
            
        finally:
            # Cerrar la conexión
            await conn.close()

    @staticmethod
    async def execute_mongodb_query(mongo_query, db_config):
        """
        Ejecuta una consulta MongoDB y devuelve los resultados.
        
        Args:
            mongo_query: Objeto MongoDBQuery o diccionario con la consulta a ejecutar
            db_config: Diccionario con la configuración de conexión a MongoDB
            
        Returns:
            Tupla con (resultado, explicación)
        """
        
        try:
            # Obtener parámetros de conexión
            host = db_config.get("host", "localhost")
            port = db_config.get("port", 27017)
            user = db_config.get("user", "")
            password = db_config.get("password", "")
            database = db_config.get("database", "")
            
            # Construir la URL de conexión
            if user and password:
                connection_string = f"mongodb://{user}:{password}@{host}:{port}/{database}"
            else:
                connection_string = f"mongodb://{host}:{port}/{database}"
            
            # Conectar a MongoDB
            client = motor.motor_asyncio.AsyncIOMotorClient(connection_string)
            db = client[database]
            
            # Obtener la colección de forma segura
            collection_name = None
            if hasattr(mongo_query, "collection"):
                collection_name = mongo_query.collection
            elif isinstance(mongo_query, dict) and "collection" in mongo_query:
                collection_name = mongo_query["collection"]
            
            if not collection_name:
                raise ValueError("No se especificó una colección para la consulta MongoDB")
            
            collection = db[collection_name]
            
            # Obtener el filtro de consulta de manera segura
            query_filter = {}
            
            # Intentar varias formas de obtener el filtro
            if hasattr(mongo_query, "filter") and getattr(mongo_query, "filter") is not None:
                query_filter = mongo_query.filter
            elif hasattr(mongo_query, "query") and getattr(mongo_query, "query") is not None:
                query_filter = mongo_query.query
            elif isinstance(mongo_query, dict):
                if "filter" in mongo_query and mongo_query["filter"] is not None:
                    query_filter = mongo_query["filter"]
                elif "query" in mongo_query and mongo_query["query"] is not None:
                    query_filter = mongo_query["query"]
            
            # Obtener otros parámetros de manera segura
            projection = None
            if hasattr(mongo_query, "projection"):
                projection = mongo_query.projection
            elif isinstance(mongo_query, dict) and "projection" in mongo_query:
                projection = mongo_query["projection"]
            
            sort = None
            if hasattr(mongo_query, "sort"):
                sort = mongo_query.sort
            elif isinstance(mongo_query, dict) and "sort" in mongo_query:
                sort = mongo_query["sort"]
            
            limit = 10  # Valor predeterminado
            if hasattr(mongo_query, "limit") and mongo_query.limit is not None:
                limit = mongo_query.limit
            elif isinstance(mongo_query, dict) and "limit" in mongo_query and mongo_query["limit"] is not None:
                limit = mongo_query["limit"]
            
            skip = 0  # Valor predeterminado
            if hasattr(mongo_query, "skip") and mongo_query.skip is not None:
                skip = mongo_query.skip
            elif isinstance(mongo_query, dict) and "skip" in mongo_query and mongo_query["skip"] is not None:
                skip = mongo_query["skip"]
            
            # Obtener la operación a realizar
            operation = "find"  # Valor predeterminado
            if hasattr(mongo_query, "operation"):
                operation = getattr(mongo_query, "operation")
            elif isinstance(mongo_query, dict) and "operation" in mongo_query:
                operation = mongo_query["operation"]
            
            # Normalizar la operación a minúsculas
            operation = operation.lower() if isinstance(operation, str) else "find"
            
            # Ejecutar la operación según su tipo
            if operation == "find":
                # Construir la consulta de búsqueda
                cursor = collection.find(
                    filter=query_filter,
                    projection=projection
                )
                
                # Aplicar sort, skip y limit si están presentes
                if sort:
                    # Convertir sort de diccionario a lista de tuplas si es necesario
                    sort_list = []
                    if isinstance(sort, dict):
                        sort_list = list(sort.items())
                    else:
                        sort_list = sort
                    cursor = cursor.sort(sort_list)
                
                if skip and isinstance(skip, int) and skip > 0:
                    cursor = cursor.skip(skip)
                
                if limit and isinstance(limit, int) and limit > 0:
                    cursor = cursor.limit(limit)
                
                # Obtener los resultados
                result_list = await cursor.to_list(length=limit)
                
                # Convertir ObjectId a string para serialización JSON
                result_serializable = json.loads(dumps(result_list))
                
                # Generar explicación
                count = len(result_serializable)
                explanation = f"Se encontraron {count} documentos en la colección {collection_name}."
                
                if count == 0:
                    explanation = f"No se encontraron documentos en la colección {collection_name} que coincidan con los criterios especificados."
                else:
                    if projection:
                        fields = ", ".join(projection.keys())
                        explanation += f" Campos seleccionados: {fields}."
                    if sort:
                        explanation += " Los resultados están ordenados según los criterios especificados."
                    if limit:
                        explanation += f" Se limitó la búsqueda a {limit} documentos."
                
                return result_serializable, explanation
                
            elif operation in ["findone", "find_one"]:
                # Ejecutar búsqueda de un solo documento
                document = await collection.find_one(
                    filter=query_filter,
                    projection=projection
                )
                
                # Convertir a formato serializable
                result_serializable = json.loads(dumps(document)) if document else None
                
                # Generar explicación
                if result_serializable:
                    explanation = f"Se encontró el documento solicitado en la colección {collection_name}."
                    if projection:
                        fields = ", ".join(projection.keys())
                        explanation += f" Campos seleccionados: {fields}."
                else:
                    explanation = f"No se encontró ningún documento en la colección {collection_name} que coincida con los criterios de búsqueda."
                
                # Devolver el documento como lista para consistencia
                return [result_serializable] if result_serializable else [], explanation
                
            elif operation == "aggregate":
                # Obtener el pipeline de agregación
                pipeline = []
                if hasattr(mongo_query, "pipeline"):
                    pipeline = mongo_query.pipeline
                elif isinstance(mongo_query, dict) and "pipeline" in mongo_query:
                    pipeline = mongo_query["pipeline"]
                
                if not pipeline:
                    raise ValueError("Se especificó operación 'aggregate' pero no se proporcionó un pipeline")
                
                # Ejecutar agregación
                cursor = collection.aggregate(pipeline)
                result_list = await cursor.to_list(length=100)  # Limitar a 100 por defecto
                
                # Convertir a formato serializable
                result_serializable = json.loads(dumps(result_list))
                
                # Generar explicación
                count = len(result_serializable)
                if count == 0:
                    explanation = f"La agregación en la colección {collection_name} no devolvió ningún resultado."
                else:
                    explanation = f"La agregación en la colección {collection_name} devolvió {count} resultado{'s' if count != 1 else ''}."
                
                return result_serializable, explanation
                
            elif operation in ["count", "countdocuments", "count_documents"]:
                # Ejecutar conteo de documentos
                count = await collection.count_documents(query_filter)
                
                # Generar explicación
                if count == 0:
                    explanation = f"No se encontraron documentos en la colección {collection_name} que coincidan con los criterios especificados."
                else:
                    explanation = f"Se encontraron {count} documento{'s' if count != 1 else ''} en la colección {collection_name} que coinciden con los criterios especificados."
                
                return [{"count": count}], explanation
                
            elif operation in ["insertone", "insert_one"]:
                # Obtener el documento a insertar
                document = None
                if hasattr(mongo_query, "document"):
                    document = mongo_query.document
                elif isinstance(mongo_query, dict) and "document" in mongo_query:
                    document = mongo_query["document"]
                
                if not document:
                    # Si no hay documento específico, usar el filtro como documento
                    document = query_filter
                
                # Ejecutar inserción
                result = await collection.insert_one(document)
                
                # Generar explicación
                inserted_id = str(result.inserted_id)
                explanation = f"Se ha insertado correctamente un nuevo documento en la colección {collection_name} con ID: {inserted_id}."
                
                return [{"insertedId": inserted_id}], explanation
                
            elif operation in ["updateone", "update_one"]:
                # Obtener la actualización
                update = None
                if hasattr(mongo_query, "update"):
                    update = mongo_query.update
                elif isinstance(mongo_query, dict) and "update" in mongo_query:
                    update = mongo_query["update"]
                
                if not update:
                    raise ValueError("Se especificó operación 'updateOne' pero no se proporcionó el documento de actualización")
                
                # Ejecutar actualización
                result = await collection.update_one(
                    filter=query_filter,
                    update=update
                )
                
                # Generar explicación
                matched = result.matched_count
                modified = result.modified_count
                
                if matched == 0:
                    explanation = f"No se encontró ningún documento en la colección {collection_name} que coincida con los criterios para actualizar."
                elif modified == 0:
                    explanation = f"Se encontró un documento en la colección {collection_name} pero no se realizaron cambios (los valores ya son idénticos)."
                else:
                    explanation = f"Se actualizó correctamente un documento en la colección {collection_name}."
                
                return [{
                    "matchedCount": matched,
                    "modifiedCount": modified
                }], explanation
                
            elif operation in ["deleteone", "delete_one"]:
                # Ejecutar eliminación
                result = await collection.delete_one(query_filter)
                
                # Generar explicación
                deleted = result.deleted_count
                
                if deleted == 0:
                    explanation = f"No se encontró ningún documento en la colección {collection_name} que coincida con los criterios para eliminar."
                else:
                    explanation = f"Se eliminó correctamente un documento de la colección {collection_name}."
                
                return [{"deletedCount": deleted}], explanation
                
            else:
                # Operación no reconocida
                raise ValueError(f"Operación MongoDB no soportada: {operation}")
        
        except Exception as e:
            # Capturar y registrar el error
            import traceback
            error_message = str(e)
            error_traceback = traceback.format_exc()
            
            print(f"Error en execute_mongodb_query: {error_message}")
            print(f"mongo_query: {mongo_query}")
            print(f"Traceback: {error_traceback}")
            
            # Proporcionar una explicación del error
            explanation = f"Error al ejecutar la consulta MongoDB: {error_message}"
            
            # Sugerir soluciones según el tipo de error
            if "Authentication failed" in error_message:
                explanation += " Las credenciales de acceso son incorrectas."
            elif "not authorized" in error_message:
                explanation += " El usuario no tiene permisos suficientes para esta operación."
            elif "No such collection" in error_message:
                explanation += " La colección especificada no existe en la base de datos."
            elif "Cannot connect" in error_message or "Connection refused" in error_message:
                explanation += " No se pudo establecer conexión con el servidor MongoDB."
            elif "filter" in error_message and "has no attribute" in error_message:
                explanation += " Problema con el formato del filtro de consulta."
            
            # Devolver vacío y explicación del error
            return [], explanation

    @staticmethod
    def clean_json_response(response_text: str) -> str:
        """Limpia y extrae el JSON de la respuesta del modelo."""
        json_text = response_text.strip()
        
        # Eliminar bloques de código markdown
        if json_text.startswith('```') and json_text.endswith('```'):
            json_text = json_text[3:-3].strip()
        elif '```' in json_text:
            # Extraer contenido entre las primeras comillas de código triple
            match = re.search(r'```(?:json)?(.*?)```', json_text, re.DOTALL)
            if match:
                json_text = match.group(1).strip()
                
        # Eliminar prefijo de lenguaje
        if json_text.startswith('json'):
            json_text = json_text[4:].strip()
        
        return json_text
    
    @staticmethod
    async def generate_result_explanation(
        query: str,
        mongo_query: Any,
        result: QueryResult
    ) -> str:
        """
        Genera una explicación en lenguaje natural de los resultados de una consulta MongoDB.
        
        Args:
            query: Consulta original en lenguaje natural
            mongo_query: Consulta MongoDB generada (objeto o diccionario)
            result: Resultado de la consulta
            
        Returns:
            Explicación en lenguaje natural
        """
        # Convertir mongo_query a diccionario si es un objeto
        if hasattr(mongo_query, "model_dump"):
            mongo_query_dict = mongo_query.model_dump()
        elif hasattr(mongo_query, "dict"):
            mongo_query_dict = mongo_query.dict()
        elif not isinstance(mongo_query, dict):
            # Intentar extraer atributos comunes
            mongo_query_dict = {
                "collection": getattr(mongo_query, "collection", ""),
                "operation": getattr(mongo_query, "operation", "find"),
                "filter": getattr(mongo_query, "filter", {}),
                "projection": getattr(mongo_query, "projection", {}),
                "sort": getattr(mongo_query, "sort", {}),
                "limit": getattr(mongo_query, "limit", 0),
                "skip": getattr(mongo_query, "skip", 0),
                "pipeline": getattr(mongo_query, "pipeline", [])
            }
        else:
            mongo_query_dict = mongo_query
        
        # Limitar resultado para el prompt
        result_sample = result.data[:5] if isinstance(result.data, list) else [result.data]
        
        # Extraer información relevante de la consulta
        collection = mongo_query_dict.get("collection", "")
        operation = mongo_query_dict.get("operation", "find")
        filter_criteria = mongo_query_dict.get("filter", {}) or mongo_query_dict.get("query", {})
        fields = list(mongo_query_dict.get("projection", {}).keys()) if mongo_query_dict.get("projection") else []
        pipeline = mongo_query_dict.get("pipeline", [])
        
        # Analizar la estructura de los resultados
        fields_in_results = []
        if result.count > 0 and isinstance(result_sample[0], dict):
            fields_in_results = list(result_sample[0].keys())
        
        # Determinar el tipo de consulta
        query_type = operation
        is_aggregation = operation == "aggregate" or (isinstance(pipeline, list) and len(pipeline) > 0)
        has_filter = bool(filter_criteria)
        has_projection = bool(fields)
        
        # Crear resumen para el contexto
        summary = {
            "query_type": query_type,
            "collection": collection,
            "total_results": result.count,
            "execution_time_ms": result.query_time_ms,
            "is_aggregation": is_aggregation,
            "has_filter": has_filter,
            "has_projection": has_projection,
            "fields_selected": fields,
            "fields_in_results": fields_in_results
        }
        
        # Añadir información específica según el tipo de operación
        if operation == "find" or operation == "findOne":
            summary["filter_criteria"] = filter_criteria
            summary["sort"] = mongo_query_dict.get("sort", {})
            summary["limit"] = mongo_query_dict.get("limit", 0)
            summary["skip"] = mongo_query_dict.get("skip", 0)
        elif operation == "aggregate":
            summary["pipeline_stages"] = [list(stage.keys())[0] if isinstance(stage, dict) else str(stage) for stage in pipeline]
        elif operation in ["insertOne", "updateOne", "deleteOne"]:
            summary["affected_documents"] = result.count
        
        # Preparar contexto para OpenAI
        context = {
            "original_query": query,
            "mongodb_query": mongo_query_dict,
            "result_count": result.count,
            "result_sample": result_sample,
            "query_time_ms": result.query_time_ms,
            "summary": summary
        }
        
        context_json = json.dumps(context, indent=2, default=str)
        
        # Generar prompt para OpenAI
        system_prompt = """
        Eres un asistente especializado en explicar resultados de consultas MongoDB.
        Tu objetivo es proporcionar explicaciones claras y naturales de los resultados obtenidos,
        evitando tecnicismos innecesarios pero manteniendo la precisión de la información.
        
        Directrices específicas:
        1. Comienza con un resumen conciso de los resultados (cuántos documentos se encontraron)
        2. Describe el tipo de consulta realizada (búsqueda, filtrado, agregación, etc.) sin usar terminología técnica de MongoDB
        3. Comenta los hallazgos más importantes o patrones identificados en los datos
        4. Destaca información relevante sobre los documentos mostrados
        5. La explicación debe ser comprensible para personas sin conocimientos técnicos
        6. Usa un lenguaje accesible pero preciso
        7. NO menciones la sintaxis MongoDB ni términos técnicos como "$match", "$group", etc.
        8. Prioriza la relevancia para el usuario sobre los detalles técnicos
        
        Formato deseado:
        - Empezar con un resumen directo y claro
        - Continuar con 1-3 observaciones importantes sobre los datos
        - Si es relevante, terminar con una conclusión breve
        - Mantener la explicación en 100-150 palabras como máximo
        """
        
        try:
            # Inicializar cliente OpenAI
            client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)
            
            # Enviar solicitud a OpenAI
            response = await client.chat.completions.create(
                model=settings.OPENAI.OPENAI_MODEL,
                max_tokens=settings.OPENAI.MAX_TOKENS,
                temperature=0.7,  # Un poco más de creatividad para la explicación
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Explica los resultados de esta consulta MongoDB:\n{context_json}"}
                ]
            )
            
            # Extraer explicación
            explanation = response.choices[0].message.content
            
            return explanation
        
        except Exception as e:
            # En caso de error, generar explicación básica
            logger.error(f"Error al generar explicación MongoDB: {str(e)}")
            
            # Crear una explicación sencilla basada en los datos disponibles
            if result.count == 0:
                return f"No se encontraron documentos en la colección {collection} que coincidan con tu consulta."
            else:
                explanation = f"Se encontraron {result.count} documentos en la colección {collection}."
                
                # Añadir información sobre el tipo de operación
                if operation == "findOne":
                    explanation = f"Se encontró el documento solicitado en la colección {collection}."
                elif operation == "aggregate":
                    explanation = f"La agregación en la colección {collection} devolvió {result.count} resultados."
                elif operation == "insertOne":
                    explanation = f"Se ha insertado correctamente un nuevo documento en la colección {collection}."
                elif operation == "updateOne":
                    explanation = f"Se ha actualizado correctamente un documento en la colección {collection}."
                elif operation == "deleteOne":
                    explanation = f"Se ha eliminado correctamente un documento de la colección {collection}."
                
                # Añadir información sobre campos si están disponibles
                if fields_in_results:
                    sample_fields = fields_in_results[:5]  # Limitar a 5 campos para no saturar la explicación
                    explanation += f" Los campos presentes en los resultados incluyen: {', '.join(sample_fields)}"
                    if len(fields_in_results) > 5:
                        explanation += f" y {len(fields_in_results) - 5} más."
                    else:
                        explanation += "."
                
                # Añadir información sobre el tiempo de ejecución
                if result.query_time_ms > 0:
                    explanation += f" La consulta se ejecutó en {result.query_time_ms:.1f} ms."
                    
                return explanation
    
    @staticmethod
    async def generate_sql_result_explanation(
        query: str,
        sql_query: str,
        result: QueryResult
    ) -> str:
        """
        Genera una explicación en lenguaje natural de los resultados de una consulta SQL.
        
        Args:
            query: Consulta original en lenguaje natural
            sql_query: Consulta SQL ejecutada
            result: Resultado de la consulta
            
        Returns:
            Explicación en lenguaje natural
        """
        # Comprobar si hay resultados
        if result.count == 0:
            return "No se encontraron registros que coincidan con tu consulta."
        
        # Limitar resultado para el prompt
        result_sample = result.data[:5]
        
        # Analizar la estructura de los resultados para una mejor explicación
        column_names = []
        if result.count > 0 and isinstance(result.data[0], dict):
            column_names = list(result.data[0].keys())
        
        # Analizar la consulta SQL para extraer información relevante
        sql_lower = sql_query.lower()
        selected_tables = []
        join_tables = []
        
        # Detectar tablas en la consulta
        from_pattern = r'from\s+([a-zA-Z0-9_\.]+)'
        join_pattern = r'join\s+([a-zA-Z0-9_\.]+)'
        
        from_matches = re.findall(from_pattern, sql_lower)
        if from_matches:
            selected_tables.extend(from_matches)
        
        join_matches = re.findall(join_pattern, sql_lower)
        if join_matches:
            join_tables.extend(join_matches)
        
        # Detectar tipo de consulta SQL
        query_type = "consulta"
        if "count" in sql_lower and "group by" not in sql_lower:
            query_type = "conteo"
        elif "group by" in sql_lower:
            query_type = "agrupación"
        elif "join" in sql_lower:
            query_type = "relación"
        elif "order by" in sql_lower:
            query_type = "ordenamiento"
        elif "where" in sql_lower:
            query_type = "filtrado"
        
        # Crear resumen básico para incluir en el contexto
        summary = {
            "query_type": query_type,
            "total_results": result.count,
            "execution_time_ms": result.query_time_ms,
            "tables_involved": selected_tables + join_tables,
            "columns_returned": column_names,
            "has_aggregation": "group by" in sql_lower or "sum(" in sql_lower or "avg(" in sql_lower or "count(" in sql_lower,
            "has_conditions": "where" in sql_lower,
            "has_ordering": "order by" in sql_lower,
            "has_joins": len(join_tables) > 0
        }
        
        # Preparar contexto para OpenAI
        context = {
            "original_query": query,
            "sql_query": sql_query,
            "result_count": result.count,
            "result_sample": result_sample,
            "query_time_ms": result.query_time_ms,
            "engine": result.metadata.get("engine", ""),
            "summary": summary,
            "column_names": column_names
        }
        
        context_json = json.dumps(context, indent=2, default=str)
        
        # Generar prompt para OpenAI
        system_prompt = """
        Eres un asistente especializado en explicar resultados de consultas SQL.
        Tu objetivo es proporcionar explicaciones claras y naturales, evitando tecnicismos innecesarios
        pero manteniendo la precisión de la información.
        
        Directrices específicas:
        1. Comienza con un resumen conciso de los resultados (cuántos registros se encontraron)
        2. Describe el tipo de consulta realizada (búsqueda, filtrado, relación, conteo, etc.) sin usar terminología SQL
        3. Comenta los hallazgos más importantes o patrones identificados
        4. Destaca información relevante sobre los datos mostrados
        5. Cuando sea apropiado, menciona valores específicos (máximos, mínimos, promedios, etc.)
        6. La explicación debe ser comprensible para personas sin conocimientos técnicos
        7. Usa un lenguaje accesible pero preciso
        8. NO menciones la sintaxis SQL ni términos técnicos como JOIN, GROUP BY, etc.
        9. Prioriza la relevancia para el usuario sobre los detalles técnicos
        
        Formato deseado:
        - Empezar con un resumen directo y claro
        - Continuar con 1-3 observaciones importantes sobre los datos
        - Si es relevante, terminar con una conclusión breve
        - Mantener la explicación en 100-150 palabras como máximo
        """
        
        try:
            # Inicializar cliente OpenAI
            client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)
            
            # Enviar solicitud a OpenAI
            response = await client.chat.completions.create(
                model=settings.OPENAI.OPENAI_MODEL,
                max_tokens=settings.OPENAI.MAX_TOKENS,
                temperature=0.7,  # Un poco más de creatividad para la explicación
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Explica los resultados de esta consulta SQL:\n{context_json}"}
                ]
            )
            
            # Extraer explicación
            explanation = response.choices[0].message.content
            
            return explanation
        
        except Exception as e:
            # En caso de error, generar explicación básica
            logger.error(f"Error al generar explicación SQL: {str(e)}")
            
            # Crear una explicación sencilla basada en los datos disponibles
            if result.count == 0:
                return "No se encontraron registros que coincidan con tu consulta."
            else:
                explanation = f"Se encontraron {result.count} registros en la base de datos."
                
                # Añadir información básica sobre las columnas si están disponibles
                if column_names:
                    explanation += f" Los campos presentes en los resultados son: {', '.join(column_names)}."
                
                # Añadir información sobre las tablas involucradas
                if selected_tables or join_tables:
                    tables = list(set(selected_tables + join_tables))
                    explanation += f" La consulta se realizó sobre {', '.join(tables)}."
                
                # Añadir información sobre el tiempo de ejecución
                if result.query_time_ms > 0:
                    explanation += f" La consulta se ejecutó en {result.query_time_ms:.1f} ms."
                    
                return explanation
         
    @staticmethod
    async def process_natural_language_query(
        query: str,
        db_schema: Dict[str, Any],
        collection_name: Optional[str] = None,
        config_id: Optional[str] = None,
        db_connection = None
    ) -> Dict[str, Any]:
        """
        Procesa una consulta en lenguaje natural y genera una explicación.
        
        Args:
            query: Consulta en lenguaje natural
            db_schema: Esquema de la base de datos
            collection_name: Nombre de la colección/tabla a consultar (opcional)
            config_id: ID de configuración (opcional)
            
        Returns:
            Diccionario con la consulta generada, resultados y explicación
        """
        try:
            # Determinar el tipo de base de datos
            db_type = db_schema.get("type", "").lower()
            
            if db_type == "sql":
                # Para bases de datos SQL
                engine = db_schema.get("engine", "sqlite").lower()
                
                # Generar consulta SQL
                sql_query = await AIQuery.generate_sql_query(query, db_schema, engine)
                
                # Ejecutar consulta SQL
                result_data, execution_time = await AIQuery.execute_sql_query(sql_query, db_schema)
                
                # Crear objeto QueryResult
                result = QueryResult(
                    data=result_data,
                    count=len(result_data),
                    query_time_ms=int(execution_time * 1000),
                    metadata={
                        "engine": engine,
                        "database": db_schema.get("database", ""),
                        "config_id": config_id
                    }
                )
                
                # Generar explicación
                explanation = await AIQuery.generate_sql_result_explanation(query, sql_query, result)
                
                # Devolver respuesta completa
                return {
                    "explanation": explanation,
                    "query": {
                        "sql": sql_query,
                        "engine": engine
                    },
                    "result": result
                }
                
            elif db_type in ["nosql", "mongodb"]:
                # Para MongoDB
                
                if db_connection:
                    debug_info = await Diagnostic.debug_mongodb_connection(db_connection)
                    logger.info(f"Diagnóstico MongoDB previo a consulta: {json.dumps(debug_info, default=str)}")
                    
                    if debug_info.get("connection_status") != "connected":
                        return {
                            "explanation": "No se pudo conectar a la base de datos MongoDB",
                            "query": None,
                            "result": None,
                            "error": True,
                            "debug_info": debug_info
                        }
                
                # Generar consulta MongoDB
                mongo_query = await AIQuery.generate_mongodb_query(
                    query, db_schema, collection_name, db_connection
                )
                
                # Ejecutar la consulta MongoDB
                # Implementación de ejecución de consulta MongoDB...
                # (Suponiendo que existe un método para ejecutar la consulta)
                
                # Placeholder para resultados
                result = QueryResult(
                    data=[],  # Aquí irían los datos reales
                    count=0,
                    query_time_ms=0,
                    metadata={
                        "config_id": config_id
                    }
                )
                
                # Preparar el objeto de consulta para devolverlo
                # Convertir MongoDBQuery a dict de forma segura
                if hasattr(mongo_query, "model_dump"):
                    query_dict = mongo_query.model_dump()
                elif hasattr(mongo_query, "dict"):
                    query_dict = mongo_query.dict()
                else:
                    # Fallback: crear diccionario manualmente
                    query_dict = {
                        "collection": getattr(mongo_query, "collection", ""),
                        "operation": getattr(mongo_query, "operation", "find"),
                        "query": getattr(mongo_query, "query", {}),
                        "pipeline": getattr(mongo_query, "pipeline", []),
                        "projection": getattr(mongo_query, "projection", {}),
                        "sort": getattr(mongo_query, "sort", {}),
                        "limit": getattr(mongo_query, "limit", 10),
                        "skip": getattr(mongo_query, "skip", 0)
                    }
                
                # Generar explicación
                explanation = await AIQuery.generate_result_explanation(query, mongo_query, result)
                
                # Devolver respuesta completa
                return {
                    "explanation": explanation,
                    "query": query_dict,
                    "result": result
                }
            
            else:
                # Tipo de base de datos no soportado
                return {
                    "explanation": f"Tipo de base de datos no soportado: {db_type}",
                    "query": None,
                    "result": None,
                    "error": True
                }
                
        except Exception as e:
            logger.error(f"Error al procesar consulta en lenguaje natural: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            
            return {
                "explanation": f"Error al procesar la consulta: {str(e)}",
                "query": None,
                "result": None,
                "error": True
            }
            
    @staticmethod
    async def process_collections_query(
        question: str,
        db_schema: Dict[str, Any]
    ) -> List[str]:
        """
        Procesa una consulta en lenguaje natural y genera una explicación.
        
        Args:
            question: Consulta en lenguaje natural
            db_schema: Esquema de la base de datos
            
        Returns:
            Diccionario con la consulta generada, resultados y explicación sobre los datos
            Determina qué colección se debe consultar	
        """

        # Crear system prompt para consulta MongoDB
        system_prompt = f"""
        Eres un asistente especializado en identificar las colecciones necesarias y relativas a la consulta del usuario.
        
        ESTRUCTURA DE LA BASE DE DATOS:
        {db_schema}
    
        Tu tarea es:
        1. Analizar cuidadosamente la consulta del usuario
        2. Entender el esquema de la base de datos
        3. Determinar cual o cuales son las colecciones que se deben consultar
        
        Responde ÚNICAMENTE con las colecciones que se deben consultar en formato de lista, separadas por comas.
        """
        
        try:
            # Inicializar cliente de OpenAI
            client = openai.AsyncOpenAI(api_key=settings.OPENAI.OPENAI_API_KEY)

            # Enviar solicitud a OpenAI
            response = await client.chat.completions.create(
                model=settings.OPENAI.OPENAI_MODEL,
                max_tokens=settings.OPENAI.MAX_TOKENS,
                temperature=0.2,  # Temperatura baja para respuestas más deterministas
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question}
                ]
            )
            
            # Extraer y procesar respuesta JSON
            ai_response = response.choices[0].message.content
            json_text = AIQuery.clean_json_response(ai_response)
            
            # Parsear JSON
            query_data = json.loads(json_text)
            
            return query_data
            
        except Exception as e:
            logger.error(f"Error al generar consulta MongoDB: {str(e)}")
            
            
        
