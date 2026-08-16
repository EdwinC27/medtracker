"""HTML pages.

The templates only carry the page skeleton (with `data-i18n` attributes); all
data and all labels are filled in by the JavaScript from the JSON API, which
keeps a single source of truth for translations.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import APP_VERSION, TEMPLATES_DIR
from app.routes.deps import get_language

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _render(request: Request, template: str, language: str, **context) -> HTMLResponse:
    return templates.TemplateResponse(
        request=request,
        name=template,
        context={"language": language, "version": APP_VERSION, **context},
    )


@router.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request, language: str = Depends(get_language)):
    return _render(request, "dashboard.html", language, page="dashboard")


@router.get("/medications", response_class=HTMLResponse)
def medications_page(request: Request, language: str = Depends(get_language)):
    return _render(request, "medications.html", language, page="medications")


@router.get("/medications/{medication_id}", response_class=HTMLResponse)
def medication_detail_page(
    medication_id: int, request: Request, language: str = Depends(get_language)
):
    return _render(
        request,
        "medication_detail.html",
        language,
        page="medications",
        medication_id=medication_id,
    )


@router.get("/appointments", response_class=HTMLResponse)
def appointments_page(request: Request, language: str = Depends(get_language)):
    return _render(request, "appointments.html", language, page="appointments")


@router.get("/appointments/{appointment_id}", response_class=HTMLResponse)
def appointment_detail_page(
    appointment_id: int, request: Request, language: str = Depends(get_language)
):
    return _render(
        request,
        "appointment_detail.html",
        language,
        page="appointments",
        appointment_id=appointment_id,
    )


@router.get("/history", response_class=HTMLResponse)
def history_page(request: Request, language: str = Depends(get_language)):
    return _render(request, "history.html", language, page="history")


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, language: str = Depends(get_language)):
    return _render(request, "settings.html", language, page="settings")
