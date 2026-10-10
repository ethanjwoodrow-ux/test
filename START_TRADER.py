"""Double-click this file to start the AI Trader dashboard.

First run installs what it needs (takes a minute), then opens the dashboard in your browser.
Keep the black window open while trading; close it to stop.
"""

import importlib.metadata
import os
import subprocess
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
sys.path.insert(0, HERE)

# package to import -> (pip name, minimum version)
NEEDED = {
    "anthropic": ("anthropic", "1.13.0"),
    "ccxt": ("ccxt", "4.5.0"),
    "dotenv": ("python-dotenv", "1.0.0"),
    "rich": ("rich", "13.0.0"),
}


def _version_tuple(v: str) -> tuple:
    return tuple(int(p) for p in v.split(".")[:3] if p.isdigit())


def missing_packages() -> list[str]:
    out = []
    for pip_name, minimum in NEEDED.values():
        try:
            if _version_tuple(importlib.metadata.version(pip_name)) >= _version_tuple(minimum):
                continue
        except importlib.metadata.PackageNotFoundError:
            pass
        out.append(f"{pip_name}>={minimum}")
    return out


def main():
    if sys.version_info < (3, 10):
        print(f"AI Trader needs Python 3.10 or newer (you have {sys.version.split()[0]}).")
        print("Install the latest Python from https://www.python.org/downloads/")
        return False
    need = missing_packages()
    if need:
        print("First run: installing", ", ".join(need), "...\n")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", *need])
        print()
    from ai_trader.dashboard import run
    run()
    return True


if __name__ == "__main__":
    try:
        ok = main()
    except Exception:
        traceback.print_exc()
        ok = False
    if not ok:
        input("\nSomething went wrong (see above). Press Enter to close.")
