from fastapi import APIRouter, Depends, HTTPException, status, Body, Request, Response
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm, APIKeyHeader
from typing import List, Optional, Union
from datetime import datetime, timedelta
from app.core.config import settings
from app.models.api_key import ApiKeyCreate, ApiKeyResponse, ApiKeyUpdate
from app.models.user import UserCreate, UserResponse, UserUpdate
from app.services import auth_service, cli_token_service
from app.core.logging import LogEntry
from app.database import get_database

import string
import random

router = APIRouter()

# Para autenticación basada en JWT (dashboard)
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

# Para autenticación basada en API key (SDK)
API_KEY_HEADER = APIKeyHeader(name=settings.SECURITY.API_KEY_NAME)

"""
Login ways
"""
# 1. Login with username and password
@router.post("/login", response_model=dict)
async def login_with_password(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Obtiene un token JWT de acceso (para dashboard)
    """
    print("Entra a la ruta")
    user = await auth_service.authenticate_user_with_password(form_data.username, form_data.password)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Correo electrónico o contraseña incorrectos",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    access_token = auth_service.create_access_token(user.id)
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user_id": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role
    }

# 2. Login with SSO
@router.post("/sso/token", response_model=dict)
async def login_with_sso_token(request: Request):
    """
    Endpoint para obtener un token JWT de acceso a partir de un token de SSO.
    Este endpoint puede recibir información adicional del usuario desde el frontend.
    """
    try:
        print("Entra en el login_with_sso_token route")
        # Obtener el token de SSO del header de autorización
        auth_header = request.headers.get("Authorization")
        print("Recoge el auth header: ", auth_header)
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token de SSO no proporcionado",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
        sso_token = auth_header.replace("Bearer ", "")
        print("Recoge el sso token: ", sso_token)
        
        # Intentar leer datos adicionales del cuerpo de la solicitud
        user_info = False
        try:
            # Leer el cuerpo JSON si existe
            body_data = await request.json()
            user_info = body_data.get("user_data")
            print("Datos de usuario recibidos del frontend:", user_info)
        except:
            # Si no hay cuerpo JSON o hay un error al parsearlo, continuar sin datos adicionales
            print("No se recibieron datos adicionales del usuario o hubo un error al leer el cuerpo JSON")
        
        if not user_info:
            # Si tenemos datos de usuario del frontend y el token es válido,
            # podríamos considerar crear/actualizar el usuario aquí
            if user_info:
                print("Token SSO válido pero usuario no encontrado. Considerando crear usuario con datos recibidos")
                # Implementar lógica para crear o actualizar usuario si es necesario
                # user = await user_service.create_or_update_user(user_info)
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token de SSO inválido o expirado",
                    headers={"WWW-Authenticate": "Bearer"},
                )
                
        # Intercambios datos del usuario del SSO por datos del usuario en la base de datos
        user_info = await auth_service.get_user_by_email(user_info['email'])
        print("Va al create access token")
        # Si llegamos aquí, tenemos un usuario válido
        access_token = auth_service.create_access_token(user_info.id)
        print("Crea el access token: ", access_token)
        
        # Registrar la creación del token
        LogEntry("api_token_created_from_sso") \
            .set_user_id(user_info.id) \
            .add_data("token_type", "api") \
            .log()
            
        print("Registra la creación del token")
        
        # Añadir tiempo de expiración en la respuesta (por ejemplo, 24 horas)
        expiration = datetime.now() + timedelta(hours=24)
        print("expiration: ", expiration)
        return_data = {
            "access_token": access_token,
            "token_type": "bearer",
            "user_id": user_info.id,
            "email": user_info.email,
            "name": user_info.name if 'name' in user_info or user_info.name else user_info['first_name'] + " " + user_info['last_name'],
            "role": user_info.role if 'role' in user_info or user_info.role else False,
            "active": user_info.active,
            "expires_in": 86400,  # 24 horas en segundos
            "expiration": expiration.isoformat()
        }
        print("Retorna data con success: ", return_data)
        return return_data
    except HTTPException:
        raise
    except Exception as e:
        # Registrar error general
        LogEntry("sso_token_exchange_error", "error") \
            .add_data("error", str(e)) \
            .log()
            
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al procesar el token de SSO"
        )
        

@router.get("/sso/callback")
async def sso_login(
    request: Request, 
    response: Response, 
    code: Optional[str] = None, 
    state: Optional[str] = None,
    redirect_uri: Optional[str] = None
):
    """
    Maneja el callback después del login en el SSO.
    
    Este endpoint es llamado por el proveedor de SSO después de la autenticación exitosa.
    Puede ser utilizado tanto desde el navegador web como desde el CLI.
    
    Args:
        code: Código de autorización proporcionado por el SSO
        state: Estado para validación CSRF
        redirect_uri: URI de redirección opcional (usado para CLI)
    """
    if not code:
        return JSONResponse(
            content={"error": "Código de autorización faltante"}, 
            status_code=400
        )
        
    from app.lib.sso.middleware import GlobodainSSOAuth
    from main import app
    globodain_sso = GlobodainSSOAuth(app)
    
    # Intercambiar código por token
    token_data = await globodain_sso.exchange_code_for_token(code)
    print("token_data: ", token_data)
    if not token_data:
        return JSONResponse(
            content={"error": "Error al intercambiar código por token"}, 
            status_code=400
        )
    
    # Obtener información del usuario
    user_info = await globodain_sso.get_user_info(token_data['access_token'])
    print("user_info: ", user_info)
    if not user_info:
        return JSONResponse(
            content={"error": "Error al obtener información del usuario"}, 
            status_code=400
        )
    
    # Buscar el usuario por email
    db = await get_database()
    print("db: ", db)
    user = await db.users.find_one({
        "email": user_info["email"]
    })
    
    # Si el usuario no existe, lo creamos (auto-registro)
    user_data = None
    if not user:
        try:
            # Create random password with 12 characters
            password = ''.join(random.choices(string.ascii_letters + string.digits, k=12))
            
            user_data = UserCreate(
                email=user_info["email"],
                name=f"{user_info['first_name']} {user_info['last_name']}",
                password=password,
                metadata={
                    "sso_id": user_info.get("id"),
                    "profile": user_info
                }
            )
            
            user = await create_user(user_data)
            print(f"Usuario creado: {user.id} ({user.email})")
            
            # 2. Crear API Key asociada para el CLI
            api_key_data = ApiKeyCreate(
                name="API Key create by Globodain SSO",
                user_id=user.id,
                level="write"
            )
            
            api_key = await auth_service.create_api_key(api_key_data)
            
            print("\n=== ACCESO CREADO EXITOSAMENTE ===")
            print(f"Usuario: {user.email}")
            print(f"ID de usuario: {user.id}")
            print(f"API Key: {api_key.key}")
            print(f"Nivel de acceso: {api_key.level}")
            print("==================================")
                
            # Registrar creación de usuario por SSO
            LogEntry("user_created_by_sso") \
                .set_user_id(str(user["_id"])) \
                .add_data("email", user["email"]) \
                .add_data("provider", "globodain") \
                .log()
            
            return {
                "user": user,
                "api_key": api_key
            }
        
        except Exception as e:
            print(f"Error al crear acceso: {str(e)}")
            return None
    else:
        print("Actualiza el último login usando el user existente identificado por email")
        # Actualizar último login
        user_data = await db.users.find_one_and_update(
            {"_id": user["_id"]},
            {"$set": {"last_login": datetime.now()}}
        )
    
    # Crear payload para el token JWT
    jwt_payload = {
        "sub": str(user['_id']),
        "sso_provider": "globodain",
        "token_source": "sso_exchange",
        "email": user_info["email"]
    }
    
    # Crear token de SSO
    print("jwt_payload: ", jwt_payload)
    print("Pasa a crear el user token")
    access_token, expiration = await cli_token_service.create_user_token(
        user_id=user_data["id"],
        name="tempotal_cli_token"  # Token corto solo para el intercambio
    )
    print("access_token: ", access_token)
    print("expiration: ", expiration)
    

    # Establecer token en header de Authorization
    response.headers["Authorization"] = f"Bearer {access_token}"
    
    # Determinar la URL de redirección
    # Si es desde CLI, usamos la redirect_uri proporcionada
    if redirect_uri and redirect_uri.startswith("http"):
        # Agregamos el token como parámetro de consulta para que el CLI lo capture
        redirect_url = f"{redirect_uri}?access_token={access_token}"
    else:
        # Para web, redirigimos al dashboard
        dashboard_url = f"{settings.APP_URL}/"
        redirect_url = dashboard_url
    
    # Crear respuesta de redirección
    redirect = RedirectResponse(url=redirect_url)
    
    # Establecer cookies solo para web (no para CLI)
    if not redirect_uri or not redirect_uri.startswith("http"):
        redirect.set_cookie(
            key="access_token", 
            value=access_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=3600  # 1 hora
        )
    
    # Registrar éxito
    LogEntry("sso_callback_success") \
        .set_user_id(str(user["_id"])) \
        .add_data("provider", "globodain") \
        .add_data("is_cli", bool(redirect_uri)) \
        .log()
    
    return redirect

##################

@router.post("/users", response_model=dict)
async def create_user(user_data: UserCreate):
    """
    Crea un nuevo usuario
    """
    try:
        print("Entra en el create_user route: ", user_data)
        user = await auth_service.create_user(user_data)

        if user:
            # Create API Key for the user
            api_key_data = ApiKeyCreate(
                name="API Key created by Globodain SSO",
                user_id=user.id,
                level="write"
            )
            print("Create api key: ", api_key_data)
            api_key = await auth_service.create_api_key(api_key_data, user.id)
            print("api_key: ", api_key)
            
            print("\n=== ACCESO CREADO EXITOSAMENTE ===")
            print(f"Usuario: {user.email}")
            print(f"ID de usuario: {user.id}")
            print(f"API Key: {api_key.key}")
            print(f"Nivel de acceso: {api_key.level}")
            print("==================================")
            
            return {
                "user": user,
                "api_key": api_key
            }
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e)
            )
          
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("/users/{user_id}", response_model=UserResponse)
async def get_user(user_id: str):
    """
    Obtiene un usuario por su ID
    """
    try:
        user = await auth_service.get_user(user_id)
        return user
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )

@router.get("/users/{user_email}/email", response_model=Union[UserResponse, bool])
async def get_user_by_email(user_email: str):
    """
    Obtiene un usuario por su ID
    """
    try:
        print("Entra en el get_user_by_email: ", user_email)
        user = await auth_service.get_user_by_email(user_email)
        return user
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )