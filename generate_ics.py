"""Holt den Stundenplan als JSON aus DaVinci und schreibt ihn als stundenplan.ics."""
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import requests
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# --- Konfiguration (kommt aus GitHub Secrets / Umgebungsvariablen) ---
DAVINCI_URL = os.environ.get("DAVINCI_URL", "").strip()
DAVINCI_METHOD = (os.environ.get("DAVINCI_METHOD") or "GET").upper()
DAVINCI_BODY = os.environ.get("DAVINCI_BODY", "")
DAVINCI_CONTENT_TYPE = os.environ.get("DAVINCI_CONTENT_TYPE") or "application/json"
DAVINCI_JSON_FILE = os.environ.get("DAVINCI_JSON_FILE", "")  # nur zum lokalen Testen
CLASS_NAME = os.environ.get("DAVINCI_USER") or "H-FI25-1B"

TZ = ZoneInfo("Europe/Berlin")
OUTFILE = "stundenplan.ics"


# --------------------------------------------------------------- Abruf
def with_fresh_timestamp(url):
    """Ersetzt den Cache-Parameter `_` durch die aktuelle Zeit."""
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "_"]
    query.append(("_", str(int(time.time() * 1000))))
    return urlunsplit(parts._replace(query=urlencode(query)))


def fetch_json():
    if DAVINCI_JSON_FILE:
        with open(DAVINCI_JSON_FILE, encoding="utf-8") as f:
            return json.load(f)
    if not DAVINCI_URL:
        sys.exit("DAVINCI_URL ist nicht gesetzt (GitHub Secret anlegen).")
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:159.0) Gecko/20100101 Firefox/159.0",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Accept-Language": "de,en-US;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": "https://sbsz-hsp.com/davinci-timetable.html?account=private",
    }
    if DAVINCI_METHOD == "POST":
        headers["Content-Type"] = DAVINCI_CONTENT_TYPE
        r = requests.post(DAVINCI_URL, data=DAVINCI_BODY.encode("utf-8"), headers=headers, timeout=30)
    else:
        r = requests.get(with_fresh_timestamp(DAVINCI_URL), headers=headers, timeout=30)
    r.raise_for_status()
    try:
        data = r.json()
    except ValueError:
        sys.exit("Antwort ist kein JSON (Key abgelaufen oder falsch?). Anfang: " + r.text[:200])
    if "result" not in data:
        sys.exit("Unerwartete Antwort von DaVinci: " + str(data)[:200])
    return data


# ------------------------------------------------------------- Parsing
def dt(d, t):
    return datetime.strptime(d + t, "%Y%m%d%H%M").replace(tzinfo=TZ)


def parse_lessons(data):
    res = data["result"]
    sched = res["displaySchedule"]
    teachers = {t["code"]: f'{t.get("firstName", "")} {t.get("lastName", "")}'.strip()
                for t in res.get("teachers", [])}
    subjects = {s["code"]: s.get("description", "") for s in res.get("subjects", [])}
    courses = {c["id"]: c for c in res.get("courses", [])}

    lessons = []
    for lt in sched.get("lessonTimes", []):
        course = courses.get(lt.get("courseRef"), {})
        code = lt.get("subjectCode", "")
        title = lt.get("courseTitle") or code
        if course.get("remarks"):
            title += f" ({course['remarks']})"

        ch = lt.get("changes") or {}
        cancelled = ch.get("cancelled")
        if cancelled == "movedAway":
            prefix = "VERLEGT: "
        elif cancelled:
            prefix = "ENTFÄLLT: "
        elif ch:
            prefix = "Geändert: "
        else:
            prefix = ""

        room = ", ".join(lt.get("roomCodes", []))
        info = []
        if subjects.get(code):
            info.append(f"Fach: {subjects[code]}")
        names = [teachers.get(c, c) for c in lt.get("teacherCodes", [])]
        if names:
            info.append("Lehrkraft: " + ", ".join(names))
        if ch.get("caption"):
            info.append(f"Änderung: {ch['caption']}")
        if ch.get("absentRoomCodes"):
            info.append("Statt Raum: " + ", ".join(ch["absentRoomCodes"]))

        for d in lt.get("dates", []):
            lessons.append({
                "uid_key": f"{lt.get('lessonRef')}{d}{lt['startTime']}",
                "start": dt(d, lt["startTime"]),
                "end": dt(d, lt["endTime"]),
                "title": prefix + title,
                "room": room,
                "description": "\n".join(info),
            })
    return lessons


def parse_events(data, today):
    """Ganztägige/zeitlich begrenzte Termine (Ferien, Feiertage ...), nur für diese Klasse."""
    home = data.get("user", {}).get("homeId")
    out = []
    for ev in data["result"]["displaySchedule"].get("eventTimes", []):
        caption = ev.get("eventCaption")
        if not caption or ev["startDate"] < "19900101":
            continue
        parts = ev.get("participants")
        if parts and home not in parts:
            continue
        if ev["endDate"] < today.strftime("%Y%m%d"):
            continue
        item = {"uid_key": ev["eventRef"] + ev["startDate"], "title": caption,
                "room": "", "description": ""}
        if ev.get("wholeDay"):
            item["all_day"] = (ev["startDate"], ev["endDate"])
        else:
            item["start"] = dt(ev["startDate"], ev["startTime"])
            item["end"] = dt(ev["endDate"], ev["endTime"])
        out.append(item)
    return out


# ----------------------------------------------------------------- ICS
def esc(text):
    return (text.replace("\\", "\\\\").replace(";", "\\;")
                .replace(",", "\\,").replace("\n", "\\n"))


def utc(d):
    return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def fold(line):
    """Zeilen nach RFC 5545 auf max. 75 Byte umbrechen."""
    out, cur, size = [], "", 0
    for ch in line:
        n = len(ch.encode("utf-8"))
        if size + n > 74:
            out.append(cur)
            cur, size = " " + ch, 1 + n
        else:
            cur += ch
            size += n
    out.append(cur)
    return "\r\n".join(out)


def build_ics(items):
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Stundenplan//DE", "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:Stundenplan {CLASS_NAME}", "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H",
    ]
    for it in sorted(items, key=lambda i: i.get("start") or datetime.strptime(i["all_day"][0], "%Y%m%d").replace(tzinfo=TZ)):
        uid = hashlib.sha1(it["uid_key"].encode()).hexdigest() + "@stundenplan"
        lines += ["BEGIN:VEVENT", f"UID:{uid}"]
        if "all_day" in it:
            s, e = it["all_day"]
            lines += [f"DTSTAMP:{s}T000000Z", f"DTSTART;VALUE=DATE:{s}", f"DTEND;VALUE=DATE:{e}"]
        else:
            lines += [f"DTSTAMP:{utc(it['start'])}", f"DTSTART:{utc(it['start'])}", f"DTEND:{utc(it['end'])}"]
        lines.append(f"SUMMARY:{esc(it['title'])}")
        if it["room"]:
            lines.append(f"LOCATION:{esc(it['room'])}")
        if it["description"]:
            lines.append(f"DESCRIPTION:{esc(it['description'])}")
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


def main():
    data = fetch_json()
    lessons = parse_lessons(data)
    if not lessons:
        sys.exit("Keine Stunden gefunden, Datei wird nicht überschrieben.")
    events = parse_events(data, date.today())
    with open(OUTFILE, "w", encoding="utf-8", newline="") as f:
        f.write(build_ics(lessons + events))
    print(f"{len(lessons)} Stunden und {len(events)} Termine nach {OUTFILE} geschrieben.")


if __name__ == "__main__":
    main()
