"""Terminal UI helpers: colors, prompts, navigation."""

import os
import re
import sys
from datetime import datetime, timedelta, timezone

# Enable ANSI escape codes on Windows 10+
if sys.platform == "win32":
    os.system("")


class C:
    """ANSI color codes."""
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"


# ── Navigation exceptions ────────────────────────────────────────────────────

class BackToMenu(Exception):
    """User typed 'b' to go back to the main menu."""

class RestartFlow(Exception):
    """User typed 'r' to restart from step 1."""


# ── Output helpers ───────────────────────────────────────────────────────────

def ok(msg: str):
    print(f"  {C.GREEN}✓{C.RESET} {msg}")

def warn(msg: str):
    print(f"  {C.YELLOW}⚠{C.RESET} {msg}")

def err(msg: str):
    print(f"  {C.RED}✗{C.RESET} {msg}")

def heading(msg: str):
    print(f"\n{C.BOLD}{C.CYAN}{msg}{C.RESET}")

def separator():
    print(f"{C.DIM}{'─' * 60}{C.RESET}")

def nav_hint():
    """Print navigation shortcuts below a prompt."""
    print(f"    {C.YELLOW}b{C.RESET}{C.DIM} = back to menu  |  {C.RESET}{C.YELLOW}r{C.RESET}{C.DIM} = restart from step 1  |  {C.RESET}{C.YELLOW}Ctrl+C{C.RESET}{C.DIM} = exit program{C.RESET}")


# ── Input helpers ────────────────────────────────────────────────────────────

def ask(prompt: str) -> str:
    """Prompt for input; raise BackToMenu on 'b', RestartFlow on 'r'."""
    raw = input(prompt).strip()
    if raw.lower() == "b":
        raise BackToMenu()
    if raw.lower() == "r":
        raise RestartFlow()
    return raw


def pick_from_list(items: list[str], prompt: str, last_choice: str = "") -> str:
    """Numbered list picker. Enter accepts the default shown in green."""
    has_default = last_choice and last_choice in items
    if has_default:
        raw = ask(f"  {prompt} [{C.GREEN}{last_choice}{C.RESET}] (Enter = default): ")
        if raw == "":
            return last_choice
    else:
        raw = ask(f"  {prompt}: ")

    while True:
        if raw.isdigit() and 1 <= int(raw) <= len(items):
            return items[int(raw) - 1]
        if raw in items:
            return raw
        if raw.isdigit():
            err(f"Out of range. Pick a number between 1 and {len(items)}.")
        else:
            err(f"Invalid choice. Pick 1-{len(items)}.")
        raw = ask(f"  {prompt}: ")


def print_numbered_list(items: list[str], default: str = ""):
    """Print a numbered list with the default highlighted in green, plus nav hints."""
    for i, item in enumerate(items, 1):
        if item == default:
            print(f"    {C.GREEN}{i}. {item} (default){C.RESET}")
        else:
            print(f"    {i}. {item}")
    nav_hint()


def prompt_datetime(label: str, start_dt: datetime | None = None) -> datetime:
    """Prompt for local UTC+1 datetime; returns timezone-aware UTC datetime.

    Accepted date formats:
      - YYYY-MM-DD / YYYY.MM.DD
      - MM-DD / MM.DD (year defaults to current year)
      - DD (only for end input when a start datetime is passed)

    Accepted time formats:
      - HH:MM
      - HH (minutes default to 00)
      - missing (defaults to 00:00)
    """
    return _prompt_datetime_impl(label, start_dt=start_dt)


def _prompt_datetime_impl(label: str, start_dt: datetime | None = None) -> datetime:
    tz_local = timezone(timedelta(hours=1))

    def parse_parts(raw: str) -> datetime | None:
        parts = raw.strip().split()
        if not parts or len(parts) > 2:
            return None

        date_part = parts[0].replace(".", "-")
        time_part = parts[1] if len(parts) == 2 else ""

        # Time: HH:MM or HH, default 00:00
        hour = 0
        minute = 0
        if time_part:
            if ":" in time_part:
                t = time_part.split(":")
                if len(t) != 2:
                    return None
                try:
                    hour = int(t[0])
                    minute = int(t[1])
                except ValueError:
                    return None
            else:
                try:
                    hour = int(time_part)
                    minute = 0
                except ValueError:
                    return None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None

        # Date with defaults
        now_local = datetime.now(tz_local)
        default_month = now_local.month
        if start_dt is not None:
            start_local = start_dt.astimezone(tz_local)
            default_month = start_local.month

        d = date_part.split("-")
        try:
            if len(d) == 3:
                year = int(d[0])
                month = int(d[1])
                day = int(d[2])
            elif len(d) == 2:
                year = now_local.year
                month = int(d[0])
                day = int(d[1])
            elif len(d) == 1 and start_dt is not None:
                year = now_local.year
                month = default_month
                day = int(d[0])
            else:
                return None
            dt_local = datetime(year, month, day, hour, minute, tzinfo=tz_local)
        except ValueError:
            return None

        return dt_local.astimezone(timezone.utc)

    while True:
        hint = "YYYY-MM-DD HH:MM, UTC+1"
        if start_dt is not None:
            hint = "YYYY-MM-DD HH:MM, UTC+1 (DD HH allowed, month from start)"
        raw = ask(f"  {label} ({hint}): ")
        dt_utc = parse_parts(raw)
        if dt_utc is not None:
            now = datetime.now(timezone.utc)
            if dt_utc.year < 2020:
                warn("Date seems too far in the past. InfluxDB data starts from 2020+.")
            if dt_utc > now + timedelta(days=365):
                warn("Date is more than a year in the future - are you sure?")
            return dt_utc

        err("Invalid datetime format.")
        print(f"    {C.DIM}Examples: 2026-03-01 00:00 | 03.01 00 | 03-01 | 08 (end only){C.RESET}")
    while True:
        raw = ask(f"  {label} (YYYY-MM-DD HH:MM, UTC): ")
        for fmt in ["%Y-%m-%d %H:%M", "%Y-%m-%d"]:
            try:
                dt = datetime.strptime(raw, fmt)
                dt_utc = dt.replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if dt_utc.year < 2020:
                    warn("Date seems too far in the past. InfluxDB data starts from 2020+.")
                if dt_utc > now + timedelta(days=365):
                    warn("Date is more than a year in the future — are you sure?")
                return dt_utc
            except ValueError:
                continue
        err("Invalid format. Use YYYY-MM-DD HH:MM  (e.g. 2026-02-06 00:00)")
        print(f"    {C.DIM}You can also use just a date: 2026-02-06 (assumes 00:00){C.RESET}")


def derive_label(device_name: str) -> str:
    """Derive a short label from device name. 'Leak Detector 00 Cloud' -> 'LD00'."""
    name = device_name.split("(")[0].strip()
    match = re.match(r"^(\S+)\s+(\S+)\s+(\d+)", name)
    if match:
        initials = match.group(1)[0] + match.group(2)[0]
        return f"{initials.upper()}{match.group(3)}"
    match = re.match(r"^([A-Za-z]+)[\s\-]?(\d+)", name)
    if match:
        return f"{match.group(1).upper()}{match.group(2)}"
    return name.split()[0] if name else "DTS"
