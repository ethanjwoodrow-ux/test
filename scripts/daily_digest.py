#!/usr/bin/env python3
"""
Script: Morning digest — deadlines, announcements, new content.

Usage:
    python scripts/daily_digest.py [--days N] [--save-text PATH]

Run this each morning (via cron or Task Scheduler) for a clean briefing.

Cron example (7am daily):
    0 7 * * * cd /path/to/project && python scripts/daily_digest.py
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright

from uc_automation.session import get_authenticated_page
from uc_automation.scraper import get_courses, get_deadlines, get_announcements, get_course_files
from uc_automation.digest import print_digest, format_digest_text
from uc_automation.diff import find_new_files, find_new_announcements, save_current_state
from uc_automation.config import DIGEST_DAYS_AHEAD


def main() -> None:
    parser = argparse.ArgumentParser(description="UC Learn daily digest.")
    parser.add_argument("--days", type=int, default=DIGEST_DAYS_AHEAD, help="Days to look ahead")
    parser.add_argument("--save-text", help="Also save plain-text digest to this file", default=None)
    args = parser.parse_args()

    with sync_playwright() as p:
        browser, context, page = get_authenticated_page(p)
        try:
            courses = get_courses(page)
            deadlines = get_deadlines(page, courses)

            # Collect all files for diff
            all_files = []
            for course in courses:
                all_files.extend(get_course_files(page, course))

            announcements = get_announcements(page, courses)

            new_files = find_new_files(all_files)
            new_announcements = find_new_announcements(announcements)

            # Print rich digest to terminal
            print_digest(deadlines, announcements, new_files, args.days)

            # Optionally save plain text
            if args.save_text:
                text = format_digest_text(deadlines, announcements, new_files, args.days)
                Path(args.save_text).write_text(text)
                print(f"\n[digest] Text version saved to {args.save_text}")

            # Update content state for next diff
            save_current_state(all_files, announcements)

        finally:
            browser.close()


if __name__ == "__main__":
    main()
