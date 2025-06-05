from typing import Dict, Any, Optional, List, Tuple
import anthropic
import json
import re
import time

from app.core.config import settings
from app.core.logging import logger
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
            # Inicializar cliente de Anthropic
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC.ANTHROPIC_API_KEY)
            
            # Enviar solicitud a Anthropic
            response = client.messages.create(
                model=settings.ANTHROPIC.ANTHROPIC_MODEL,
                max_tokens=settings.ANTHROPIC.MAX_TOKENS,
                temperature=0.2,  # Temperatura baja para respuestas más deterministas
                messages=[{"role": "user", "content": query}],
                system=system_prompt
            )
            
            # Extraer la consulta SQL generada
            sql_query = response.content[0].text.strip()
            
            # Limpiar la consulta (eliminar comentarios, etc.)
            sql_query = AIQuery.clean_sql_query(sql_query)
            
            return sql_query
            
        except Exception as e:
            logger.error(f"Error al generar consulta SQL: {str(e)}")
            # En caso de error, generar una consulta segura y simple
            safe_table = next(iter(db_info.get("tables", {}).keys()), "users")
            return f"SELECT * FROM {safe_table} LIMIT 10"

    
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
    async def generate_mongodb_query(
        query: str,
        db_info: Dict[str, Any],
        collection_name: Optional[str] = None
    ) -> MongoDBQuery:
        """
        Genera una consulta MongoDB a partir de una consulta en lenguaje natural.
        
        Args:
            query: Consulta en lenguaje natural
            db_info: Información del esquema de la base de datos
            collection_name: Nombre de la colección a consultar (opcional)
            
        Returns:
            Objeto MongoDBQuery con la consulta generada
        """
        # Preparar el contexto con la información de la base de datos
        db_context = json.dumps(db_info, indent=2, default=str)
        
        # Limitar el contexto si es demasiado grande
        if len(db_context) > 10000:
            # Determinar si estamos usando el formato tables o collections
            if "tables" in db_info:
                collections = list(db_info.get("tables", {}).keys())
                key_name = "tables"
            else:
                collections = list(db_info.get("collections", {}).keys())
                key_name = "collections"
                
            truncated_collections = collections[:5]
            
            truncated_db_info = {
                key_name: {
                    name: db_info[key_name][name]
                    for name in truncated_collections if name in db_info.get(key_name, {})
                }
            }
            
            db_context = json.dumps(truncated_db_info, indent=2, default=str)
            db_context += f"\n\n... y {len(collections) - 5} colecciones más."
        
        # Crear system prompt para consulta MongoDB
        collection_hint = f" Debes usar la colección '{collection_name}'." if collection_name else ""
        
        system_prompt = f"""
        Eres un asistente especializado en traducir consultas en lenguaje natural a operaciones MongoDB.
        
        ESTRUCTURA DE LA BASE DE DATOS:
        {db_context}
        
        Tu tarea es:
        1. Analizar la consulta del usuario
        2. Determinar qué colección debe ser consultada{collection_hint}
        3. Construir la consulta apropiada (find o aggregate)
        4. Devolver la consulta como un objeto JSON con el siguiente formato:
        
        Para búsquedas simples:
        {{
        "collection": "nombre_coleccion",
        "operation": "find",
        "query": {{ /* filtros */ }},
        "projection": {{ /* campos a incluir/excluir */ }},
        "sort": {{ /* ordenamiento */ }},
        "limit": 10
        }}
        
        Para agregaciones:
        {{
        "collection": "nombre_coleccion",
        "operation": "aggregate",
        "pipeline": [
            {{ /* etapa 1 */ }},
            {{ /* etapa 2 */ }}
        ]
        }}
        
        Responde ÚNICAMENTE con el objeto JSON, sin ningún otro texto.
        """
        
        try:
            # Inicializar cliente de Anthropic
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC.ANTHROPIC_API_KEY)
            
            # Enviar solicitud a Anthropic
            response = client.messages.create(
                model=settings.ANTHROPIC.ANTHROPIC_MODEL,
                max_tokens=settings.ANTHROPIC.MAX_TOKENS,
                temperature=0.2,  # Temperatura baja para respuestas más deterministas
                messages=[{"role": "user", "content": query}],
                system=system_prompt
            )
            
            # Extraer respuesta JSON
            ai_response = response.content[0].text
            
            # Limpiar respuesta (eliminar backticks y otros caracteres)
            json_text = ai_response.strip()
            if json_text.startswith('```') and json_text.endswith('```'):
                json_text = json_text[3:-3].strip()
            elif '```' in json_text:
                # Extraer contenido entre las primeras comillas de código triple
                match = re.search(r'```(?:json)?(.*?)```', json_text, re.DOTALL)
                if match:
                    json_text = match.group(1).strip()
                    
            if json_text.startswith('json'):
                json_text = json_text[4:].strip()
            
            # Parsear JSON
            query_data = json.loads(json_text)
            
            # Usar colección específica si se proporcionó
            if collection_name:
                query_data["collection"] = collection_name
            
            # Crear objeto MongoDBQuery
            mongo_query = MongoDBQuery(
                collection=query_data["collection"],
                operation=query_data["operation"],
                query=query_data.get("query"),
                pipeline=query_data.get("pipeline"),
                projection=query_data.get("projection"),
                sort=query_data.get("sort"),
                limit=query_data.get("limit", 10),
                skip=query_data.get("skip", 0)
            )
            
            return mongo_query
            
        except Exception as e:
            logger.error(f"Error al generar consulta MongoDB: {str(e)}")
            
            # En caso de error, generar una consulta segura y simple
            # Determinar la colección a usar
            if collection_name:
                default_collection = collection_name
            else:
                # Buscar colección en el esquema de la base de datos
                if "tables" in db_info:
                    default_collection = next(iter(db_info.get("tables", {}).keys()), "users")
                else:
                    default_collection = next(iter(db_info.get("collections", {}).keys()), "users")
            
            # Si no hay colecciones disponibles, usar una predeterminada
            if not default_collection:
                default_collection = "users"
            
            return MongoDBQuery(
                collection=default_collection,
                operation="find",
                query={},
                limit=10,
                skip=0
            )
                
    @staticmethod
    async def generate_result_explanation(
        query: str,
        mongo_query: MongoDBQuery,
        result: QueryResult
    ) -> str:
        """
        Genera una explicación en lenguaje natural de los resultados de una consulta MongoDB.
        
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
        
        context_json = json.dumps(context, indent=2, default=str)
        
        # Generar prompt para Anthropic
        system_prompt = """
        Eres un asistente especializado en explicar resultados de consultas a bases de datos MongoDB.
        Debes explicar los resultados de manera clara y concisa, destacando los aspectos más relevantes.
        
        Algunas pautas:
        1. Menciona cuántos resultados se encontraron
        2. Resume los hallazgos principales
        3. Si hay pocos o ningún resultado, sugiere posibles razones
        4. Evita tecnicismos innecesarios
        5. Sé breve y directo
        6. Incluye estadísticas o patrones interesantes si los encuentras
        7. Si se hizo una agregación, explica qué significa el resultado
        
        Estructura tu respuesta así:
        - Primero, un resumen breve de los resultados
        - Luego, los puntos clave o hallazgos
        - Finalmente, cualquier observación adicional relevante
        """
        
        try:
            # Inicializar cliente Anthropic
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC.ANTHROPIC_API_KEY)
            
            # Enviar solicitud a Anthropic
            response = client.messages.create(
                model=settings.ANTHROPIC.ANTHROPIC_MODEL,
                max_tokens=settings.ANTHROPIC.MAX_TOKENS,
                temperature=0.7,  # Un poco más de creatividad para la explicación
                messages=[{"role": "user", "content": f"Explica los siguientes resultados de consulta:\n{context_json}"}],
                system=system_prompt
            )
            
            # Extraer explicación
            explanation = response.content[0].text
            
            return explanation
        
        except Exception as e:
            # En caso de error, generar explicación básica
            logger.error(f"Error al generar explicación: {str(e)}")
            
            if result.count == 0:
                return "No se encontraron resultados para tu consulta."
            else:
                return f"Se encontraron {result.count} resultados en la colección {mongo_query.collection}."

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
        # Limitar resultado para el prompt
        result_sample = result.data[:5]
        
        # Preparar contexto
        context = {
            "original_query": query,
            "sql_query": sql_query,
            "result_count": result.count,
            "result_sample": result_sample,
            "query_time_ms": result.query_time_ms,
            "database_info": result.metadata.get("database", ""),
            "engine": result.metadata.get("engine", "")
        }
        
        context_json = json.dumps(context, indent=2, default=str)
        
        # Generar prompt para Anthropic
        system_prompt = """
        Eres un asistente especializado en explicar resultados de consultas SQL.
        Debes explicar los resultados de manera clara y concisa, destacando los aspectos más relevantes.
        
        Algunas pautas:
        1. Menciona cuántos resultados se encontraron
        2. Resume los hallazgos principales
        3. Si hay pocos o ningún resultado, sugiere posibles razones
        4. Evita tecnicismos innecesarios y no menciones términos SQL como "JOIN" o "GROUP BY"
        5. Sé breve y directo
        6. Incluye estadísticas o patrones interesantes si los encuentras
        7. Si se hizo una agregación, explica qué significa el resultado en términos simples
        
        NO debes mencionar la SQL query específica utilizada en tu explicación.
        Usa un lenguaje más natural y centrado en los datos y su significado.
        """
        
        try:
            # Inicializar cliente Anthropic
            client = anthropic.Anthropic(api_key=settings.ANTHROPIC.ANTHROPIC_API_KEY)
            
            # Enviar solicitud a Anthropic
            response = client.messages.create(
                model=settings.ANTHROPIC.ANTHROPIC_MODEL,
                max_tokens=settings.ANTHROPIC.MAX_TOKENS,
                temperature=0.7,  # Un poco más de creatividad para la explicación
                messages=[{"role": "user", "content": f"Explica los siguientes resultados de consulta SQL:\n{context_json}"}],
                system=system_prompt
            )
            
            # Extraer explicación
            explanation = response.content[0].text
            
            return explanation
        
        except Exception as e:
            # En caso de error, generar explicación básica
            logger.error(f"Error al generar explicación SQL: {str(e)}")
            
            if result.count == 0:
                return "No se encontraron resultados para tu consulta."
            else:
                return f"Se encontraron {result.count} registros en la base de datos."
                
    @staticmethod
    async def process_natural_language_query(
        query: str,
        db_schema: Dict[str, Any],
        collection_name: Optional[str] = None,
        config_id: Optional[str] = None
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
                # Generar consulta MongoDB
                mongo_query = await AIQuery.generate_mongodb_query(query, db_schema, collection_name)
                
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
                
                # Generar explicación
                explanation = await AIQuery.generate_result_explanation(query, mongo_query, result)
                
                # Devolver respuesta completa
                return {
                    "explanation": explanation,
                    "query": mongo_query.model_dump(),
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