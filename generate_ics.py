"""Holt den Stundenplan aus DaVinci und schreibt ihn als stundenplan.ics."""
import hashlib
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://sbsz-hsp.com"
USERNAME = os.environ.get("DAVINCI_USER", "H-FI25-1B")
PASSWORD = os.environ.get("DAVINCI_PASS", "")  # kommt aus GitHub Secrets
TZ = ZoneInfo("Europe/Berlin")
OUTFILE = "stundenplan.ics"


def fetch_lessons():
    """Liefert eine Liste von Dicts:
    {"start": datetime, "end": datetime, "title": str,
     "room": str, "teacher": str, "cancelled": bool}

    TODO: Dieser Teil hängt vom Aufbau der DaVinci-Seite ab.
    Er wird angepasst, sobald das Seitenformat bekannt ist.
    """
    session = requests.Session()
    # Beispiel (muss angepasst werden):
    # r = session.get(f"{BASE_URL}/...", auth=(USERNAME, PASSWORD), timeout=30)
    # r.raise_for_status()
    # ... r.text / r.json() auswerten ...
    raise NotImplementedError("fetch_lessons() muss noch an DaVinci angepasst werden")


def esc(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))


def fmt(dt):
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def build_ics(lessons):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Stundenplan//DE",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:Stundenplan {USERNAME}",
        "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for l in lessons:
        uid = hashlib.sha1(
            f"{l['start'].isoformat()}{l['title']}{l.get('room','')}".encode()
        ).hexdigest() + "@stundenplan"
        title = ("ENTFÄLLT: " if l.get("cancelled") else "") + l["title"]
        desc = f"Lehrkraft: {l.get('teacher', '')}"
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{fmt(l['start'])}",  # fix, damit sich die Datei nur bei echten Änderungen ändert
            f"DTSTART:{fmt(l['start'])}",
            f"DTEND:{fmt(l['end'])}",
            f"SUMMARY:{esc(title)}",
            f"LOCATION:{esc(l.get('room', ''))}",
            f"DESCRIPTION:{esc(desc)}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def main():
    lessons = fetch_lessons()
    if not lessons:
        print("Keine Stunden gefunden, Datei wird nicht überschrieben.")
        sys.exit(1)
    with open(OUTFILE, "w", encoding="utf-8", newline="") as f:
        f.write(build_ics(lessons))
    print(f"{len(lessons)} Stunden nach {OUTFILE} geschrieben.")


if __name__ == "__main__":
    main()
