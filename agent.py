import os
import requests
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo
from icalendar import Calendar
import recurring_ical_events

TZ = ZoneInfo("America/Denver")
# Tried in order; the first one that works is used
MODELS = [
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
]
MODE = os.environ.get("MODE", "reminder")  # "reminder", "briefing" or "preview"
now = datetime.now(TZ)

# One calendar link per line. Falls back to the old single-link secret.
raw_urls = os.environ.get("ICS_URLS") or os.environ.get("ICS_URL", "")
urls = [u.strip() for u in raw_urls.splitlines() if u.strip()]

cals = []
for i, url in enumerate(urls, 1):
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        cals.append(Calendar.from_ical(resp.content))
    except Exception as err:
        # Print only the error type, never the link itself
        print(f"Calendar {i} failed to load: {type(err).__name__}")
print(f"Loaded {len(cals)} of {len(urls)} calendars")
if not cals:
    raise SystemExit("No calendars could be loaded")


def events_between(start, end):
    seen = set()
    out = []
    for cal in cals:
        for e in recurring_ical_events.of(cal).between(start, end):
            s = e.decoded("DTSTART")
            if not isinstance(s, datetime):  # skip all-day events
                continue
            end_time = e.decoded("DTEND") if e.get("DTEND") else s
            item = (
                s.astimezone(TZ),
                end_time.astimezone(TZ),
                str(e.get("SUMMARY", "Untitled")),
                str(e.get("LOCATION", "")),
            )
            key = (item[0], item[1], item[2])
            if key in seen:  # skip duplicates across calendars
                continue
            seen.add(key)
            out.append(item)
    return sorted(out)


def notify(title, body):
    requests.post(
        f"https://ntfy.sh/{os.environ['NTFY_TOPIC']}",
        data=body.encode("utf-8"),
        headers={"Title": title, "Priority": "high"},
        timeout=30,
    )


def list_available_models(key):
    try:
        r = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=100",
            headers={"x-goog-api-key": key},
            timeout=30,
        )
        names = [
            m["name"]
            for m in r.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        print("Models your key can use:", ", ".join(names))
    except Exception as err:
        print("Could not list models:", err)


def ask_gemini(event_text, when):
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return ""
    prompt = (
        f"You are a concise personal calendar assistant. Below are my events for {when}. "
        "In under 60 words of plain text (no markdown), point out anything worth my "
        "attention: overlapping events, tight gaps between events, an early start, "
        "events that probably need preparation, or a very busy stretch. "
        "If nothing stands out, say it looks manageable.\n\n" + event_text
    )
    for model in MODELS:
        try:
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=60,
            )
            r.raise_for_status()
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
            print("Gemini worked with model:", model)
            return text
        except Exception as err:
            print(f"Model {model} failed:", err)
    list_available_models(key)
    return ""


def send_summary(title, evs, when):
    lines, warnings, detail = [], [], []
    for i, (s, e, name, loc) in enumerate(evs):
        lines.append(f"{s:%I:%M %p} {name}")
        detail.append(
            f"{s:%a %I:%M %p} to {e:%I:%M %p}: {name}" + (f" (at {loc})" if loc else "")
        )
        if i and s < evs[i - 1][1]:
            warnings.append(f"CONFLICT: {name} overlaps {evs[i - 1][2]}")
    body = "\n".join(lines + warnings)
    insight = ask_gemini("\n".join(detail), when)
    if insight:
        body += "\n\n" + insight
    notify(title, body)


if MODE == "reminder":
    # Events starting 15-30 minutes from now (the job runs every 15 minutes)
    window_start = now + timedelta(minutes=15)
    window_end = now + timedelta(minutes=30)
    for s, e, title, loc in events_between(window_start, window_end):
        if s >= window_start:
            notify(f"Soon: {title}", f"{s:%I:%M %p}" + (f" @ {loc}" if loc else ""))

elif MODE == "preview":
    # Everything between midnight tomorrow and midnight the day after
    tomorrow = (now + timedelta(days=1)).date()
    day_after = tomorrow + timedelta(days=1)
    start = datetime.combine(tomorrow, dtime.min, tzinfo=TZ)
    end = datetime.combine(day_after, dtime.min, tzinfo=TZ)
    evs = events_between(start, end)
    label = f"Tomorrow, {tomorrow:%a %b %d}"
    if not evs:
        notify(label, "Nothing scheduled.")
    else:
        send_summary(label, evs, f"tomorrow ({tomorrow:%A})")

else:
    evs = events_between(now, now + timedelta(hours=24))
    if not evs:
        notify("Today's calendar", "Nothing scheduled.")
    else:
        send_summary("Today's calendar", evs, "the next 24 hours")
