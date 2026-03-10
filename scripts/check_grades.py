#!/usr/bin/env python3
"""
Script: Fetch current grades from all courses and log to grades.xlsx.

Usage:
    python scripts/check_grades.py [--course COURSE] [--no-save]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright

from uc_automation.session import get_authenticated_page
from uc_automation.scraper import get_courses, get_grades
from uc_automation.grades import update_grade_spreadsheet, print_grade_summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Check grades across all UC Learn courses.")
    parser.add_argument("--course", help="Limit to a specific course short name", default=None)
    parser.add_argument("--no-save", action="store_true", help="Print only, don't update spreadsheet")
    args = parser.parse_args()

    with sync_playwright() as p:
        browser, context, page = get_authenticated_page(p)
        try:
            courses = get_courses(page)
            if args.course:
                courses = [c for c in courses if args.course.lower() in c.short_name.lower()]

            grades = get_grades(page, courses)

            print_grade_summary(grades)

            if not args.no_save and grades:
                update_grade_spreadsheet(grades)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
