"""
Run this script once to reauthorize Google Calendar OAuth.
A browser window will open for Google consent.
On success, credentials/token.json is overwritten with a fresh refresh token.

Usage:
    python3 app/scripts/reauthorize_calendar.py
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/calendar"]

flow = InstalledAppFlow.from_client_secrets_file(
    "credentials/google_credentials.json", SCOPES
)
creds = flow.run_local_server(port=0)

with open("credentials/token.json", "w") as f:
    f.write(creds.to_json())

print("✅ token.json updated. Calendar OAuth is now working.")
