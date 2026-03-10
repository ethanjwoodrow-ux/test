#!/usr/bin/env python3
"""
Script: Login to UC Learn and save session cookies.

Usage:
    python scripts/login.py

Opens a visible browser window so you can handle 2FA/SSO if needed,
then saves session cookies to ~/.uc_session.json for all future runs.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from uc_automation.session import cmd_login

if __name__ == "__main__":
    cmd_login()
