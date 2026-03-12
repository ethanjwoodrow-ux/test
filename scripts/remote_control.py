#!/usr/bin/env python3
"""
Script: Claude-powered remote control for UC Learn automation.

Usage:
    # Single command
    python scripts/remote_control.py "what assignments are due this week?"

    # Interactive REPL (no arguments)
    python scripts/remote_control.py

    # Verbose mode (shows tool calls and thinking snippets)
    python scripts/remote_control.py --verbose "download my materials"

Requires ANTHROPIC_API_KEY in your environment or .env file.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from uc_automation.remote_control import run


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Natural-language remote control for UC Learn automation.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "command",
        nargs="?",
        help="Natural-language command (omit for interactive mode).",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Print tool calls and thinking snippets.",
    )
    args = parser.parse_args()

    if args.command:
        reply = run(args.command, verbose=args.verbose)
        print(reply)
        return

    # Interactive REPL
    print("UC Learn Remote Control  (type 'exit' or Ctrl-C to quit)\n")
    while True:
        try:
            prompt = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye!")
            break

        if not prompt:
            continue
        if prompt.lower() in {"exit", "quit", "q"}:
            print("Bye!")
            break

        reply = run(prompt, verbose=args.verbose)
        print(f"\nClaude: {reply}\n")


if __name__ == "__main__":
    main()
