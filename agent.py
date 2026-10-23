import os
import requests
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from icalendar import Calendar
import recurring_ical_events

TZ = ZoneInfo("America/Denver")
MODEL = "gemini-2.5-flash"
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


def ask_gemini(event_text):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return ""
    prompt = (
        "You are a concise personal calendar assistant. Below are my events for "
        "the next 24 hours. In under 60 words of plain text (no markdown), point "
        "out anything worth my attention: overlapping events, tight gaps between "
        "events, events that probably need preparation, or a very busy stretch. "
        "If nothing stands out, say the day looks manageable.\n\n" + event_text
    )
    try:
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
            headers={"x-goog-api-key": key, "Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as err:
        print("Gemini call failed:", err)
        return ""


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
        lines, warnings, detail = [], [], []
        for i, (s, e, title, loc) in enumerate(evs):
            lines.append(f"{s:%I:%M %p} {title}")
            detail.append(
                f"{s:%a %I:%M %p} to {e:%I:%M %p}: {title}" + (f" (at {loc})" if loc else "")
            )
            if i and s < evs[i - 1][1]:
                warnings.append(f"CONFLICT: {title} overlaps {evs[i - 1][2]}")
        body = "\n".join(lines + warnings)
        insight = ask_gemini("\n".join(detail))
        if insight:
            body += "\n\n" + insight
        notify("Today's calendar", body)
