"""UC Learn scraper — courses, files, deadlines, announcements, grades."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from urllib.parse import urljoin

from playwright.sync_api import Page

from .config import UC_LEARN_BASE, BROWSER_TIMEOUT


# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class Course:
    id: str
    name: str
    short_name: str
    url: str


@dataclass
class CourseFile:
    name: str
    url: str
    course: str
    section: str = ""
    file_type: str = ""


@dataclass
class Deadline:
    title: str
    course: str
    due: datetime
    url: str = ""
    type: str = "assignment"  # assignment | quiz | other


@dataclass
class Announcement:
    title: str
    course: str
    posted: datetime
    body: str
    url: str = ""


@dataclass
class GradeEntry:
    course: str
    item: str
    grade: str
    percentage: str
    feedback: str = ""


# ── Scraping helpers ─────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def get_courses(page: Page) -> list[Course]:
    """Scrape all enrolled courses from the My Courses dashboard."""
    print("[scraper] Fetching course list...")
    page.goto(f"{UC_LEARN_BASE}/my/", timeout=BROWSER_TIMEOUT)

    courses: list[Course] = []

    # Moodle renders courses in .coursebox or data-region="course-content"
    items = page.query_selector_all("[data-region='course-content'], .coursebox")
    if not items:
        # Fallback: course links in the nav block
        items = page.query_selector_all("a[href*='/course/view.php']")
        for a in items:
            href = a.get_attribute("href") or ""
            m = re.search(r"id=(\d+)", href)
            if m:
                cid = m.group(1)
                name = _clean(a.inner_text())
                courses.append(Course(id=cid, name=name, short_name=name[:20], url=href))
        return courses

    for item in items:
        link = item.query_selector("a[href*='course/view.php']")
        if not link:
            continue
        href = link.get_attribute("href") or ""
        m = re.search(r"id=(\d+)", href)
        if not m:
            continue
        cid = m.group(1)
        name = _clean(link.inner_text())
        short_el = item.query_selector(".coc-metainfo, .course-contact")
        short = _clean(short_el.inner_text()) if short_el else name[:20]
        courses.append(Course(id=cid, name=name, short_name=short, url=href))

    print(f"[scraper] Found {len(courses)} courses.")
    return courses


def get_course_files(page: Page, course: Course) -> list[CourseFile]:
    """List all downloadable files in a course."""
    print(f"[scraper] Scanning files in {course.short_name}...")
    page.goto(course.url, timeout=BROWSER_TIMEOUT)

    files: list[CourseFile] = []
    current_section = "General"

    # Section headings
    sections = page.query_selector_all(".sectionname, h3.sectionname")
    section_map: dict[str, str] = {}
    for s in sections:
        section_map[s.get_attribute("id") or ""] = _clean(s.inner_text())

    # Resource links (files, folders, URLs)
    resource_links = page.query_selector_all(
        "a[href*='/mod/resource/'], a[href*='/mod/folder/'], "
        "a[href*='/mod/url/'], a[href*='pluginfile.php']"
    )
    for link in resource_links:
        href = link.get_attribute("href") or ""
        name = _clean(link.inner_text()) or href.split("/")[-1]

        # Guess section from DOM ancestry
        parent = link
        for _ in range(8):
            parent = parent.evaluate_handle("el => el.parentElement")
            if parent:
                pid = parent.evaluate("el => el.id") or ""
                if pid in section_map:
                    current_section = section_map[pid]
                    break

        ext = ""
        m = re.search(r"\.([a-z0-9]{2,5})(?:[?#]|$)", href, re.I)
        if m:
            ext = m.group(1).lower()

        files.append(
            CourseFile(
                name=name,
                url=href,
                course=course.short_name,
                section=current_section,
                file_type=ext,
            )
        )

    print(f"[scraper]   {len(files)} files found in {course.short_name}.")
    return files


def get_deadlines(page: Page, courses: list[Course]) -> list[Deadline]:
    """Scrape assignment and quiz due dates from all courses."""
    print("[scraper] Collecting deadlines...")
    deadlines: list[Deadline] = []

    # Try the calendar API first (faster)
    page.goto(
        f"{UC_LEARN_BASE}/calendar/view.php?view=month",
        timeout=BROWSER_TIMEOUT,
    )

    event_links = page.query_selector_all("a[href*='calendar/view.php?view=day']")
    visited_days: set[str] = set()

    for link in event_links:
        day_url = link.get_attribute("href") or ""
        if day_url in visited_days:
            continue
        visited_days.add(day_url)
        page.goto(day_url, timeout=BROWSER_TIMEOUT)
        events = page.query_selector_all(".calendar_event_course, .calendar_event_assignment")
        for ev in events:
            title_el = ev.query_selector("a")
            title = _clean(title_el.inner_text()) if title_el else "Untitled"
            url = title_el.get_attribute("href") if title_el else ""
            time_el = ev.query_selector("time, .timestart")
            due_str = time_el.get_attribute("datetime") if time_el else ""
            try:
                due = datetime.fromisoformat(due_str) if due_str else datetime.now()
            except ValueError:
                due = datetime.now()
            course_el = ev.query_selector(".course-name, .calendar_course_name")
            course_name = _clean(course_el.inner_text()) if course_el else "Unknown"
            event_type = "quiz" if "quiz" in title.lower() else "assignment"
            deadlines.append(
                Deadline(title=title, course=course_name, due=due, url=url or "", type=event_type)
            )

    # Also scrape each course's assignment list directly
    for course in courses:
        page.goto(
            f"{UC_LEARN_BASE}/mod/assign/index.php?id={course.id}",
            timeout=BROWSER_TIMEOUT,
        )
        rows = page.query_selector_all("table.generaltable tbody tr")
        for row in rows:
            cols = row.query_selector_all("td")
            if len(cols) < 3:
                continue
            title = _clean(cols[0].inner_text())
            due_text = _clean(cols[2].inner_text())
            link_el = cols[0].query_selector("a")
            url = link_el.get_attribute("href") if link_el else ""
            try:
                due = _parse_moodle_date(due_text)
            except Exception:
                continue
            deadlines.append(
                Deadline(title=title, course=course.short_name, due=due, url=url or "")
            )

    # Deduplicate by title+course
    seen: set[tuple] = set()
    unique: list[Deadline] = []
    for d in deadlines:
        key = (d.title, d.course, d.due.date())
        if key not in seen:
            seen.add(key)
            unique.append(d)

    unique.sort(key=lambda d: d.due)
    print(f"[scraper] Found {len(unique)} deadlines.")
    return unique


def get_announcements(page: Page, courses: list[Course], since: Optional[datetime] = None) -> list[Announcement]:
    """Collect recent announcements from all courses."""
    print("[scraper] Fetching announcements...")
    announcements: list[Announcement] = []

    for course in courses:
        page.goto(
            f"{UC_LEARN_BASE}/mod/forum/view.php?id={course.id}&f=announcements",
            timeout=BROWSER_TIMEOUT,
        )
        posts = page.query_selector_all(".forumpost, article.forum-post-container")
        for post in posts:
            title_el = post.query_selector("h3 a, .subject a")
            title = _clean(title_el.inner_text()) if title_el else "Untitled"
            url = title_el.get_attribute("href") if title_el else ""
            time_el = post.query_selector("time")
            posted_str = time_el.get_attribute("datetime") if time_el else ""
            try:
                posted = datetime.fromisoformat(posted_str) if posted_str else datetime.now()
            except ValueError:
                posted = datetime.now()
            if since and posted < since:
                continue
            body_el = post.query_selector(".posting, .post-content-container")
            body = _clean(body_el.inner_text())[:500] if body_el else ""
            announcements.append(
                Announcement(title=title, course=course.short_name, posted=posted, body=body, url=url or "")
            )

    announcements.sort(key=lambda a: a.posted, reverse=True)
    print(f"[scraper] Found {len(announcements)} announcements.")
    return announcements


def get_grades(page: Page, courses: list[Course]) -> list[GradeEntry]:
    """Scrape grade overview for all courses."""
    print("[scraper] Fetching grades...")
    grades: list[GradeEntry] = []

    for course in courses:
        page.goto(
            f"{UC_LEARN_BASE}/grade/report/user/index.php?id={course.id}",
            timeout=BROWSER_TIMEOUT,
        )
        rows = page.query_selector_all("table.user-grade tbody tr")
        for row in rows:
            item_el = row.query_selector(".column-itemname")
            grade_el = row.query_selector(".column-grade")
            pct_el = row.query_selector(".column-percentage")
            if not item_el:
                continue
            item = _clean(item_el.inner_text())
            grade = _clean(grade_el.inner_text()) if grade_el else "-"
            pct = _clean(pct_el.inner_text()) if pct_el else "-"
            grades.append(
                GradeEntry(course=course.short_name, item=item, grade=grade, percentage=pct)
            )

    print(f"[scraper] Collected {len(grades)} grade entries.")
    return grades


# ── Date parsing ─────────────────────────────────────────────────────────────

_MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_moodle_date(text: str) -> datetime:
    """Parse Moodle-style date strings like '15 March 2026, 11:55 PM'."""
    text = text.strip()
    # ISO format
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    # Moodle format: "15 March 2026, 11:55 PM"
    m = re.search(
        r"(\d{1,2})\s+(\w+)\s+(\d{4}),?\s+(\d{1,2}):(\d{2})\s*(AM|PM)?",
        text, re.I
    )
    if m:
        day, month_str, year, hour, minute, ampm = m.groups()
        month = _MONTH_MAP.get(month_str.lower(), 1)
        hour = int(hour)
        if ampm and ampm.upper() == "PM" and hour != 12:
            hour += 12
        elif ampm and ampm.upper() == "AM" and hour == 12:
            hour = 0
        return datetime(int(year), month, int(day), hour, int(minute))
    raise ValueError(f"Cannot parse date: {text!r}")
