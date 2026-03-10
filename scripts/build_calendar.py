#!/usr/bin/env python3
"""
Script: Scrape all assignment/quiz due dates and export as deadlines.ics.

Usage:
    python scripts/build_calendar.py [--output PATH]

The .ics file can be imported into Google Calendar, Apple Calendar,
or any calendar app that supports iCal format.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright

from uc_automation.session import get_authenticated_page
from uc_automation.scraper import get_courses, get_deadlines
from uc_automation.calendar import deadlines_to_ics
from uc_automation.config import DEADLINES_FILE


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a deadline calendar from UC Learn.")
    parser.add_argument("--output", help="Output .ics file path", default=None)
    args = parser.parse_args()

    output = Path(args.output) if args.output else DEADLINES_FILE

    with sync_playwright() as p:
        browser, context, page = get_authenticated_page(p)
        try:
            courses = get_courses(page)
            deadlines = get_deadlines(page, courses)

            if not deadlines:
                print("[warn] No deadlines found.")
                return

            ics_path = deadlines_to_ics(deadlines, output)
            print(f"\n[done] {len(deadlines)} deadlines exported to {ics_path}")
            print(f"       Import this file into Google/Apple Calendar.")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
