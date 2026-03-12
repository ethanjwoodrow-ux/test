"""Configuration for UC Learn automation."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Base URLs
UC_LEARN_BASE = "https://learn.canterbury.ac.nz"
UC_LEARN_LOGIN = f"{UC_LEARN_BASE}/login/index.php"

# File paths
BASE_DIR = Path(os.getenv("UC_BASE_DIR", str(Path.home() / "Desktop" / "Uni" / "2026")))
SESSION_FILE = Path(os.getenv("UC_SESSION_FILE", str(Path.home() / ".uc_session.json")))
GRADES_FILE = Path(os.getenv("UC_GRADES_FILE", str(BASE_DIR / "grades.xlsx")))
DEADLINES_FILE = Path(os.getenv("UC_DEADLINES_FILE", str(BASE_DIR / "deadlines.ics")))
CONTENT_STATE_FILE = Path(os.getenv("UC_CONTENT_STATE", str(BASE_DIR / ".content_state.json")))

# Credentials (loaded from .env — never hard-coded)
UC_USERNAME = os.getenv("UC_USERNAME", "")
UC_PASSWORD = os.getenv("UC_PASSWORD", "")

# Browser settings
HEADLESS = os.getenv("UC_HEADLESS", "true").lower() == "true"
BROWSER_TIMEOUT = int(os.getenv("UC_BROWSER_TIMEOUT", "30000"))  # ms

# Digest settings
DIGEST_DAYS_AHEAD = int(os.getenv("UC_DIGEST_DAYS", "7"))
