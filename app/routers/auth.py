from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import get_password_hash, hash_password, set_password_hash, verify_password
from ..deps import get_db, render

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/", error: str = ""):
    if request.session.get("authenticated"):
        return RedirectResponse("/", status_code=303)
    return render("login.html", request, next=next, error=error)


@router.post("/login", response_class=HTMLResponse)
def login_submit(
    request: Request,
    password: str = Form(""),
    next: str = Form("/"),
    conn: sqlite3.Connection = Depends(get_db),
):
    stored_hash = get_password_hash(conn)
    if not stored_hash:
        return RedirectResponse("/setup", status_code=303)

    if not password or not verify_password(password, stored_hash):
        return render("login.html", request, next=next, error="Incorrect password.")

    request.session["authenticated"] = True
    # Guard against open redirects — only allow relative paths.
    safe_next = next if next.startswith("/") else "/"
    return RedirectResponse(safe_next, status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, conn: sqlite3.Connection = Depends(get_db)):
    if get_password_hash(conn):
        return RedirectResponse("/login", status_code=303)
    return render("setup.html", request, error="")


@router.get("/settings/password", response_class=HTMLResponse)
def change_password_page(request: Request):
    return render("change_password.html", request, error="", success="")


@router.post("/settings/password", response_class=HTMLResponse)
def change_password_submit(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    stored_hash = get_password_hash(conn)
    if not stored_hash:
        return RedirectResponse("/setup", status_code=303)

    if not current_password or not verify_password(current_password, stored_hash):
        return render("change_password.html", request, error="Current password is incorrect.", success="")
    if not new_password:
        return render("change_password.html", request, error="New password cannot be empty.", success="")
    if len(new_password) < 8:
        return render("change_password.html", request, error="New password must be at least 8 characters.", success="")
    if new_password != confirm:
        return render("change_password.html", request, error="New passwords do not match.", success="")

    set_password_hash(conn, hash_password(new_password))
    return render("change_password.html", request, error="", success="Password updated successfully.")


@router.post("/setup", response_class=HTMLResponse)
def setup_submit(
    request: Request,
    password: str = Form(""),
    confirm: str = Form(""),
    conn: sqlite3.Connection = Depends(get_db),
):
    if get_password_hash(conn):
        return RedirectResponse("/login", status_code=303)

    if not password:
        return render("setup.html", request, error="Password cannot be empty.")
    if len(password) < 8:
        return render("setup.html", request, error="Password must be at least 8 characters.")
    if password != confirm:
        return render("setup.html", request, error="Passwords do not match.")

    set_password_hash(conn, hash_password(password))
    return RedirectResponse("/login?setup=1", status_code=303)
