"""Daily morning digest — what's due, what's new, what's been announced."""

from datetime import datetime, timedelta
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from .config import DIGEST_DAYS_AHEAD
from .scraper import Deadline, Announcement, CourseFile

console = Console()


# ── Formatting helpers ───────────────────────────────────────────────────────

def _urgency_color(days_left: float) -> str:
    if days_left <= 1:
        return "bold red"
    if days_left <= 3:
        return "bold yellow"
    return "green"


def _days_label(due: datetime) -> str:
    now = datetime.now()
    delta = (due.date() - now.date()).days
    if delta == 0:
        return "[bold red]TODAY[/bold red]"
    if delta == 1:
        return "[bold red]TOMORROW[/bold red]"
    return f"in {delta} days"


# ── Digest sections ──────────────────────────────────────────────────────────

def print_upcoming_deadlines(deadlines: list[Deadline], days_ahead: int = DIGEST_DAYS_AHEAD) -> None:
    cutoff = datetime.now() + timedelta(days=days_ahead)
    upcoming = [d for d in deadlines if datetime.now() <= d.due <= cutoff]
    upcoming.sort(key=lambda d: d.due)

    table = Table(title=f"Upcoming Deadlines (next {days_ahead} days)", box=box.SIMPLE_HEAD)
    table.add_column("Course", style="cyan", no_wrap=True)
    table.add_column("Task", style="white")
    table.add_column("Type", style="dim")
    table.add_column("Due", style="white")
    table.add_column("When", justify="right")

    for d in upcoming:
        days_left = (d.due - datetime.now()).total_seconds() / 86400
        color = _urgency_color(days_left)
        table.add_row(
            d.course,
            d.title,
            d.type,
            d.due.strftime("%a %d %b %H:%M"),
            f"[{color}]{_days_label(d.due)}[/{color}]",
        )

    if not upcoming:
        console.print("[green]No deadlines in the next {days_ahead} days. Nice![/green]")
    else:
        console.print(table)


def print_announcements(announcements: list[Announcement], since_hours: int = 24) -> None:
    since = datetime.now() - timedelta(hours=since_hours)
    recent = [a for a in announcements if a.posted >= since]

    if not recent:
        console.print(f"[dim]No announcements in the last {since_hours}h.[/dim]")
        return

    console.print(f"\n[bold cyan]New Announcements (last {since_hours}h):[/bold cyan]")
    for a in recent:
        console.print(
            Panel(
                f"[dim]{a.posted.strftime('%a %d %b %H:%M')}[/dim]\n\n{a.body[:300]}{'...' if len(a.body) > 300 else ''}",
                title=f"[bold]{a.course}[/bold] — {a.title}",
                border_style="blue",
            )
        )


def print_new_content(new_files: list[CourseFile]) -> None:
    if not new_files:
        console.print("[dim]No new content since last check.[/dim]")
        return

    table = Table(title="New Content Posted", box=box.SIMPLE_HEAD)
    table.add_column("Course", style="cyan")
    table.add_column("File", style="white")
    table.add_column("Section", style="dim")

    for f in new_files:
        table.add_row(f.course, f.name, f.section)

    console.print(table)


def print_digest(
    deadlines: list[Deadline],
    announcements: list[Announcement],
    new_files: list[CourseFile],
    days_ahead: int = DIGEST_DAYS_AHEAD,
) -> None:
    """Print the full daily digest to the terminal."""
    now = datetime.now()
    console.rule(f"[bold blue]UC Learn Daily Digest — {now.strftime('%A, %d %B %Y')}[/bold blue]")
    console.print()

    print_upcoming_deadlines(deadlines, days_ahead)
    console.print()
    print_announcements(announcements)
    console.print()
    print_new_content(new_files)
    console.print()
    console.rule("[dim]End of digest[/dim]")


def format_digest_text(
    deadlines: list[Deadline],
    announcements: list[Announcement],
    new_files: list[CourseFile],
    days_ahead: int = DIGEST_DAYS_AHEAD,
) -> str:
    """Return a plain-text version of the digest (for email/file output)."""
    now = datetime.now()
    lines = [
        f"UC Learn Daily Digest — {now.strftime('%A, %d %B %Y')}",
        "=" * 50,
        "",
    ]

    cutoff = now + timedelta(days=days_ahead)
    upcoming = sorted(
        [d for d in deadlines if now <= d.due <= cutoff], key=lambda d: d.due
    )

    lines.append(f"UPCOMING DEADLINES (next {days_ahead} days):")
    if upcoming:
        for d in upcoming:
            lines.append(
                f"  [{d.course}] {d.title} — {d.due.strftime('%a %d %b %H:%M')} ({_days_label(d.due).replace('[bold red]', '').replace('[/bold red]', '')})"
            )
    else:
        lines.append("  None — enjoy the break!")

    lines += ["", "RECENT ANNOUNCEMENTS:"]
    since = now - timedelta(hours=24)
    recent = [a for a in announcements if a.posted >= since]
    if recent:
        for a in recent:
            lines.append(f"  [{a.course}] {a.title} ({a.posted.strftime('%H:%M')})")
            lines.append(f"    {a.body[:200]}")
    else:
        lines.append("  None in the last 24h.")

    lines += ["", "NEW FILES:"]
    if new_files:
        for f in new_files:
            lines.append(f"  [{f.course}] {f.name} ({f.section})")
    else:
        lines.append("  No new files since last check.")

    return "\n".join(lines)
