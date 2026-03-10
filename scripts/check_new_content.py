#!/usr/bin/env python3
"""
Script: Detect new files and announcements since last run.

Usage:
    python scripts/check_new_content.py

Compares current UC Learn content against a saved snapshot (.content_state.json).
Reports exactly what was added since your last check.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from playwright.sync_api import sync_playwright
from rich.console import Console

from uc_automation.session import get_authenticated_page
from uc_automation.scraper import get_courses, get_course_files, get_announcements
from uc_automation.diff import find_new_files, find_new_announcements, save_current_state, last_run_time

console = Console()


def main() -> None:
    with sync_playwright() as p:
        browser, context, page = get_authenticated_page(p)
        try:
            last = last_run_time()
            if last:
                console.print(f"[dim]Last checked: {last.strftime('%A %d %B at %H:%M')}[/dim]\n")
            else:
                console.print("[dim]First run — establishing baseline.[/dim]\n")

            courses = get_courses(page)

            all_files = []
            for course in courses:
                all_files.extend(get_course_files(page, course))

            announcements = get_announcements(page, courses)

            new_files = find_new_files(all_files)
            new_ann = find_new_announcements(announcements)

            if new_files:
                console.print(f"[bold green]{len(new_files)} new file(s) posted:[/bold green]")
                for f in new_files:
                    console.print(f"  [{f.course}] {f.name}  [dim]({f.section})[/dim]")
            else:
                console.print("[dim]No new files.[/dim]")

            console.print()

            if new_ann:
                console.print(f"[bold blue]{len(new_ann)} new announcement(s):[/bold blue]")
                for a in new_ann:
                    console.print(f"  [{a.course}] {a.title}  [dim]({a.posted.strftime('%d %b %H:%M')})[/dim]")
            else:
                console.print("[dim]No new announcements.[/dim]")

            save_current_state(all_files, announcements)

        finally:
            browser.close()


if __name__ == "__main__":
    main()
