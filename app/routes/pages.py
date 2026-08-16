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


@router.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request, language: str = Depends(get_language)):
    return _render(request, "calendar.html", language, page="calendar")


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, language: str = Depends(get_language)):
    return _render(request, "search.html", language, page="search")


@router.get("/notifications", response_class=HTMLResponse)
def notifications_page(request: Request, language: str = Depends(get_language)):
    return _render(request, "notification_center.html", language, page="notifications")


@router.get("/doctors", response_class=HTMLResponse)
def doctors_page(request: Request, language: str = Depends(get_language)):
    return _render(request, "doctors.html", language, page="doctors")


@router.get("/doctors/{doctor_id}", response_class=HTMLResponse)
def doctor_detail_page(
    doctor_id: int, request: Request, language: str = Depends(get_language)
):
    return _render(
        request, "doctor_detail.html", language, page="doctors", doctor_id=doctor_id
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
