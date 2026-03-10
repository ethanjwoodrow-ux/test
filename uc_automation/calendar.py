"""Export deadlines to an .ics calendar file (importable by Google/Apple Calendar)."""

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from icalendar import Calendar, Event, vText, vDatetime

from .config import DEADLINES_FILE
from .scraper import Deadline


# ── iCal builder ────────────────────────────────────────────────────────────

def _uid(deadline: Deadline) -> str:
    """Generate a stable UID for a deadline."""
    raw = f"{deadline.course}-{deadline.title}-{deadline.due.isoformat()}"
    return hashlib.md5(raw.encode()).hexdigest() + "@uc-automation"


def deadlines_to_ics(deadlines: list[Deadline], output_path: Path = DEADLINES_FILE) -> Path:
    """
    Write all deadlines to an .ics file.

    Each deadline becomes a VEVENT with:
      - 1-hour duration (or all-day if no time info)
      - 30-min VALARM reminder
      - DESCRIPTION with course + type info
    """
    cal = Calendar()
    cal.add("prodid", "-//UC Learn Automation//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", "UC Learn Deadlines")
    cal.add("x-wr-timezone", "Pacific/Auckland")

    for d in deadlines:
        event = Event()
        event.add("summary", f"[{d.course}] {d.title}")
        event.add("uid", _uid(d))

        # Make deadline timezone-aware (NZ time)
        nzdt = timezone(timedelta(hours=13))  # NZDT UTC+13
        due_aware = d.due.replace(tzinfo=nzdt) if d.due.tzinfo is None else d.due

        event.add("dtstart", due_aware)
        event.add("dtend", due_aware + timedelta(hours=1))
        event.add("dtstamp", datetime.now(tz=timezone.utc))

        desc_parts = [
            f"Course: {d.course}",
            f"Type: {d.type.capitalize()}",
            f"Due: {d.due.strftime('%A, %d %B %Y at %I:%M %p')}",
        ]
        if d.url:
            desc_parts.append(f"Link: {d.url}")
        event.add("description", "\n".join(desc_parts))

        if d.url:
            event.add("url", d.url)

        # 30-minute reminder
        from icalendar import Alarm
        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", f"Due soon: {d.title}")
        alarm.add("trigger", timedelta(minutes=-30))
        event.add_component(alarm)

        cal.add_component(event)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(cal.to_ical())
    print(f"[calendar] Wrote {len(deadlines)} events to {output_path}")
    return output_path
