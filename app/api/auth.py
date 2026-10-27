"""Local authentication endpoints for the single-tenant deployment."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

from app.auth.contracts import CurrentUser, TokenResponse
from app.auth.dependencies import get_authentication_service, get_current_user
from app.auth.service import (
    AuthenticationDisabledError,
    AuthenticationService,
    InvalidCredentialsError,
)

router = APIRouter(prefix="/auth")


@router.post("/token", response_model=TokenResponse)
async def issue_token(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    service: Annotated[AuthenticationService, Depends(get_authentication_service)],
) -> TokenResponse:
    try:
        return await service.authenticate(form_data.username, form_data.password)
    except AuthenticationDisabledError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except InvalidCredentialsError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


@router.get("/me", response_model=CurrentUser)
async def read_current_user(
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> CurrentUser:
    return current_user
