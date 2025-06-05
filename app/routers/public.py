# app/routers/public_auth.py

from fastapi import APIRouter, Request, Depends, HTTPException, status, Query
from fastapi.responses import RedirectResponse
import logging
from typing import Optional

from app.lib.sso.middleware import GlobodainSSOAuth

router = APIRouter()
sso = GlobodainSSOAuth()

@router.get("/login")
async def login():
    """
    Inicia el flujo de autenticación SSO redirigiendo al usuario al servicio de Globodain SSO
    """
    return RedirectResponse(url=sso.get_login_url(), status_code=status.HTTP_303_SEE_OTHER)

@router.get("/auth/callback")
async def auth_callback(request: Request, code: str = Query(...)):
    """
    Callback para el proceso de autenticación SSO
    """
    try:
        # Intercambiar el código por un token
        token_response = await sso.exchange_code_for_token(code)
        if not token_response:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="No se pudo obtener el token de autenticación"
            )
        
        access_token = token_response.get("access_token")
        
        # Obtener información del usuario
        user_info = await sso.get_user_info(access_token)
        if not user_info:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, 
                detail="No se pudo obtener la información del usuario"
            )
        
        # Guardar en la sesión
        request.session["user"] = user_info
        request.session["access_token"] = access_token
        
        # Redirigir al usuario a la página que estaba intentando acceder o a la página de éxito
        next_url = request.session.get("next_url", sso.success_redirect)
        return RedirectResponse(url=next_url, status_code=status.HTTP_303_SEE_OTHER)
    
    except Exception as e:
        logging.error(f"Error en el callback de autenticación: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error en el proceso de autenticación"
        )

@router.get("/logout")
async def logout(request: Request):
    """
    Cierra la sesión del usuario
    """
    # Limpiar la sesión
    request.session.clear()
    
    # Redirigir al usuario a la página principal
    return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

@router.get("/me")
async def get_current_user(request: Request):
    """
    Obtiene la información del usuario actualmente autenticado
    """
    if "user" not in request.session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No autenticado"
        )
    
    return request.session["user"]