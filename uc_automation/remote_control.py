"""Claude-powered remote control — natural language interface for UC Learn automation."""

import json
import subprocess
import sys
from pathlib import Path

import anthropic

from .config import BASE_DIR

# ── Tool definitions ─────────────────────────────────────────────────────────

TOOLS = [
    {
        "name": "login",
        "description": (
            "Log in to UC Learn and save session cookies. "
            "Run this first if commands fail due to an expired session."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "daily_digest",
        "description": (
            "Show the daily digest: upcoming deadlines, recent announcements, "
            "and newly posted course files. Optionally save a plain-text copy."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "How many days ahead to look for deadlines (default: 7).",
                },
                "save_text": {
                    "type": "string",
                    "description": "File path to also save a plain-text copy of the digest.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "check_grades",
        "description": "Fetch the latest grades from UC Learn and save them to the Excel spreadsheet.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "download_materials",
        "description": (
            "Download course materials (lecture slides, readings, etc.) "
            "into the organised folder structure on disk."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "build_calendar",
        "description": (
            "Build an iCalendar (.ics) file of all upcoming deadlines "
            "so they can be imported into Google Calendar, Outlook, etc."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "check_new_content",
        "description": "Check for newly posted files and announcements since the last run.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

# ── Tool execution ────────────────────────────────────────────────────────────

_SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"


def _run_script(script: str, extra_args: list[str] | None = None) -> str:
    """Run a scripts/*.py file and return combined stdout/stderr."""
    cmd = [sys.executable, str(_SCRIPTS_DIR / script)] + (extra_args or [])
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stdout
    if result.stderr:
        output += "\n[stderr]\n" + result.stderr
    return output.strip() or "(no output)"


def execute_tool(name: str, tool_input: dict) -> str:
    """Dispatch a tool call to the corresponding automation script."""
    if name == "login":
        return _run_script("login.py")

    if name == "daily_digest":
        args = []
        if "days" in tool_input:
            args += ["--days", str(tool_input["days"])]
        if "save_text" in tool_input:
            args += ["--save-text", tool_input["save_text"]]
        return _run_script("daily_digest.py", args)

    if name == "check_grades":
        return _run_script("check_grades.py")

    if name == "download_materials":
        return _run_script("download_materials.py")

    if name == "build_calendar":
        return _run_script("build_calendar.py")

    if name == "check_new_content":
        return _run_script("check_new_content.py")

    return f"Error: unknown tool '{name}'"


# ── Remote control session ────────────────────────────────────────────────────

SYSTEM_PROMPT = f"""You are a helpful assistant that controls a UC Learn (Moodle) automation toolkit.

The user will give you natural-language instructions. Use the available tools to fulfil them.
Tools map directly to automation scripts; each one opens a browser, interacts with UC Learn,
and returns text output.

Available data directories:
- Course materials: {BASE_DIR}

Guidelines:
- Run 'login' first only if a tool returns an authentication/session error.
- For "what's due?" or "any deadlines?" → use daily_digest.
- For "any new posts/files/announcements?" → use check_new_content.
- For "get/fetch/update grades" → use check_grades.
- For "download files/materials/resources" → use download_materials.
- For "build/export/create calendar" → use build_calendar.
- Summarise tool output concisely; don't echo raw terminal output verbatim unless asked.
- If a tool fails, explain what went wrong and suggest a fix (e.g. re-running login).
"""


def run(user_prompt: str, verbose: bool = False) -> str:
    """
    Send *user_prompt* to Claude and execute any requested tools.

    Returns Claude's final text reply.
    """
    client = anthropic.Anthropic()
    messages: list[dict] = [{"role": "user", "content": user_prompt}]

    while True:
        with client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        if verbose:
            for block in response.content:
                if block.type == "thinking":
                    print(f"[thinking] {block.thinking[:200]}…\n", flush=True)

        # No tool calls → we're done; return the text reply
        if response.stop_reason == "end_turn":
            text_blocks = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_blocks)

        # Execute tool calls
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_use_blocks:
            text_blocks = [b.text for b in response.content if b.type == "text"]
            return "\n".join(text_blocks)

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for tool in tool_use_blocks:
            if verbose:
                print(f"[tool] {tool.name}({json.dumps(tool.input)})", flush=True)
            result = execute_tool(tool.name, tool.input)
            if verbose:
                print(f"[result] {result[:300]}…\n" if len(result) > 300 else f"[result] {result}\n", flush=True)
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": tool.id,
                "content": result,
            })

        messages.append({"role": "user", "content": tool_results})
