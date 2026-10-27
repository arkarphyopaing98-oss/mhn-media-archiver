"""Run ONCE on your own computer to get GOOGLE_REFRESH_TOKEN.

1. Google Cloud Console -> APIs & Services: enable "Google Drive API" and "Google Sheets API".
2. OAuth consent screen: External, add your Gmail as a test user, then PUBLISH the app
   (in "Testing" mode refresh tokens expire after 7 days).
3. Credentials -> Create OAuth client ID -> "Desktop app" -> download JSON as client_secret.json.
4. pip install google-auth-oauthlib && python scripts/google_token.py
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
print("\nGOOGLE_CLIENT_ID=" + creds.client_id)
print("GOOGLE_CLIENT_SECRET=" + creds.client_secret)
print("GOOGLE_REFRESH_TOKEN=" + creds.refresh_token)
