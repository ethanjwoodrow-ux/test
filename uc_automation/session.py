"""Playwright session management — login once, reuse cookies forever."""

import json
import sys
from pathlib import Path
from typing import Optional

from playwright.sync_api import sync_playwright, BrowserContext, Page

from .config import (
    UC_LEARN_BASE,
    UC_LEARN_LOGIN,
    SESSION_FILE,
    UC_USERNAME,
    UC_PASSWORD,
    HEADLESS,
    BROWSER_TIMEOUT,
)


def save_session(context: BrowserContext, path: Path = SESSION_FILE) -> None:
    """Persist browser cookies and storage to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    storage = context.storage_state()
    with open(path, "w") as f:
        json.dump(storage, f, indent=2)
    print(f"[session] Saved session to {path}")


def load_session(context: BrowserContext, path: Path = SESSION_FILE) -> bool:
    """Load a saved session from disk. Returns True if loaded successfully."""
    if not path.exists():
        return False
    with open(path) as f:
        state = json.load(f)
    # Playwright storage_state is applied at context creation, not after.
    # This function is kept for reference; use create_context() instead.
    return True


def is_logged_in(page: Page) -> bool:
    """Check whether the current page shows a logged-in UC Learn session."""
    return "login" not in page.url and page.locator("a[href*='logout']").count() > 0


def login(page: Page, username: str = UC_USERNAME, password: str = UC_PASSWORD) -> None:
    """Perform a fresh login to UC Learn."""
    if not username or not password:
        raise ValueError(
            "UC credentials not set. Add UC_USERNAME and UC_PASSWORD to your .env file."
        )
    print("[session] Logging in to UC Learn...")
    page.goto(UC_LEARN_LOGIN, timeout=BROWSER_TIMEOUT)
    page.fill("#username", username)
    page.fill("#password", password)
    page.click("#loginbtn")
    page.wait_for_url(f"{UC_LEARN_BASE}/**", timeout=BROWSER_TIMEOUT)
    if "login" in page.url:
        raise RuntimeError("Login failed — check your UC_USERNAME and UC_PASSWORD.")
    print("[session] Login successful.")


def create_context(playwright, session_path: Path = SESSION_FILE):
    """Launch a browser context, restoring a saved session if available."""
    browser = playwright.chromium.launch(headless=HEADLESS)
    if session_path.exists():
        print(f"[session] Restoring session from {session_path}")
        context = browser.new_context(storage_state=str(session_path))
    else:
        context = browser.new_context()
    context.set_default_timeout(BROWSER_TIMEOUT)
    return browser, context


def get_authenticated_page(
    playwright, session_path: Path = SESSION_FILE
) -> tuple:
    """
    Return (browser, context, page) with a guaranteed logged-in session.
    Attempts to reuse saved cookies; falls back to fresh login if needed.
    """
    browser, context = create_context(playwright, session_path)
    page = context.new_page()

    # Quick check — navigate to dashboard
    page.goto(f"{UC_LEARN_BASE}/my/", timeout=BROWSER_TIMEOUT)

    if not is_logged_in(page):
        print("[session] Session expired or missing — performing fresh login.")
        login(page)
        save_session(context, session_path)

    return browser, context, page


# ── CLI entry point ──────────────────────────────────────────────────────────

def cmd_login():
    """Interactive login: saves fresh session cookies to disk."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # visible so you can handle 2FA
        context = browser.new_context()
        page = context.new_page()
        login(page)
        save_session(context, SESSION_FILE)
        browser.close()
    print("[session] Done. Session saved — future runs will reuse these cookies.")


if __name__ == "__main__":
    cmd_login()
