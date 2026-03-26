from app.services.google_calendar import get_today_events, normalize_events

events = get_today_events()
events = normalize_events(events)

for e in events:
    print(e)

print("\nTotal events", len(events))
