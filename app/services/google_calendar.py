import datetime
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]

# Function to get the Google Calendar service
def get_calendar_service():
    creds = Credentials.from_authorized_user_file(
        "credentials/token.json",
        SCOPES
    )
    service = build("calendar", "v3", credentials=creds)
    return service


# Function to get today's events from Google Calendar
def get_today_events():
    service = get_calendar_service()

    now = datetime.datetime.utcnow().isoformat() + "Z"
    end = (datetime.datetime.utcnow() + datetime.timedelta(days=1)).isoformat() + "Z"

    events_result = service.events().list(
        calendarId="primary",
        timeMin=now,
        timeMax=end,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = events_result.get("items", [])

    for event in events:
        print("Calendar Event:", event.get("summary"), event.get("start"))

    return events


# Function to normalize events into a consistent format
def normalize_events(events):
    normalized = []

    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        end = event["end"].get("dateTime", event["end"].get("date"))

        title = event.get("summary", "No title").strip()
        location = event.get("location", None)
        description = event.get("description", None)

        meeting_link = None

        # Extract link (basic version)
        if description and "http" in description:
            meeting_link = description

        # Detect virtual vs physical
        is_virtual = False
        if meeting_link:
            is_virtual = True
        elif location and ("zoom" in location.lower() or "meet" in location.lower()):
            is_virtual = True

        normalized.append({
            "title": title,
            "start": start,
            "end": end,
            "location": location,
            "is_virtual": is_virtual,
            "meeting_link": meeting_link,
        })

    return normalized


# Function to summarize the day in a human-friendly way
def summarize_day(events):
    if not events:
        return "Hoy no tienes eventos."

    total = len(events)

    # Sort events by start time
    events_sorted = sorted(events, key=lambda x: x["start"])
    first_event_time = events_sorted[0]["start"]
    last_event_time = events_sorted[-1]["start"]

    # Extract hour:minute
    first_time = first_event_time.split("T")[1][:5]
    last_time = last_event_time.split("T")[1][:5]

    if total == 1:
        return f"Tienes 1 evento hoy. Empieza a las {first_time}"

    return f"Tienes {total} eventos hoy. El primero empieza a las {first_time}"


# Function to format events in a detailed way
def format_events_detailed(events):
    detailed_list = []

    for e in events:
        time_part = e["start"].split("T")[1][:5]

        if e["is_virtual"]:
            detail = f"{time_part} — {e['title']} (virtual)"
            if e["meeting_link"]:
                detail += f" → {e['meeting_link']}"
        else:
            location = e["location"] if e["location"] else "sin ubicación"
            detail = f"{time_part} — {e['title']} ({location})"

        detailed_list.append(detail)

    return "\n".join(detailed_list)


# Extra function to describe the next event in a more human-friendly way
def describe_next_event(events):
    if not events:
        return "No tienes nada pendiente."

    events_sorted = sorted(events, key=lambda x: x["start"])
    next_event = events_sorted[0]

    title = next_event["title"]
    time_part = next_event["start"].split("T")[1][:5]

    return f"Hoy tienes {title} a las {time_part}"