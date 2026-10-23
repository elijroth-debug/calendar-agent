import os
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from icalendar import Calendar
import recurring_ical_events

TZ = ZoneInfo("America/Denver")
MODE = os.environ.get("MODE", "reminder")  # "reminder" or "briefing"
now = datetime.now(TZ)

cal = Calendar.from_ical(requests.get(os.environ["ICS_URL"], timeout=30).content)


def events_between(start, end):
    out = []
    for e in recurring_ical_events.of(cal).between(start, end):
        s = e.decoded("DTSTART")
        if not isinstance(s, datetime):  # skip all-day events
            continue
        end_time = e.decoded("DTEND") if e.get("DTEND") else s
        out.append((
            s.astimezone(TZ),
            end_time.astimezone(TZ),
            str(e.get("SUMMARY", "Untitled")),
            str(e.get("LOCATION", "")),
        ))
    return sorted(out)


def notify(title, body):
    requests.post(
        f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}",
        data=body.encode("utf-8"),
        headers={"Title": title, "Priority": "high"},
        timeout=30,
    )


if MODE == "reminder":
    # Events starting 15-30 minutes from now (the job runs every 15 minutes)
    window_start = now + timedelta(minutes=15)
    window_end = now + timedelta(minutes=30)
    for s, e, title, loc in events_between(window_start, window_end):
        if s >= window_start:
            notify(f"Soon: {title}", f"{s:%I:%M %p}" + (f" @ {loc}" if loc else ""))

else:
    evs = events_between(now, now + timedelta(hours=24))
    if not evs:
        notify("Today's calendar", "Nothing scheduled.")
    else:
        lines, warnings = [], []
        for i, (s, e, title, loc) in enumerate(evs):
            lines.append(f"{s:%I:%M %p} {title}")
            if i and s < evs[i - 1][1]:
                warnings.append(f"CONFLICT: {title} overlaps {evs[i - 1][2]}")
        notify("Today's calendar", "\n".join(lines + warnings))
