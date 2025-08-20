from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import pandas as pd

# Scope for read-only access
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

# Authenticate
flow = InstalledAppFlow.from_client_secrets_file('credentials.json', SCOPES)
creds = flow.run_local_server(port=0)

drive_service = build('drive', 'v3', credentials=creds)

folder_id = "1Noyt9HnKig7ZR76FAxY8WgBel1cFNmOC"

# Retrieve all files in folder
files = []
page_token = None
while True:
    response = drive_service.files().list(
        q=f"'{folder_id}' in parents and trashed = false",
        fields="nextPageToken, files(id, name)",
        pageToken=page_token
    ).execute()
    files.extend(response.get('files', []))
    page_token = response.get('nextPageToken')
    if not page_token:
        break

# Build CSV data
data = [[file['name'], f"https://drive.google.com/uc?id={file['id']}"] for file in files]

df = pd.DataFrame(data, columns=['filename', 'url'])
df.to_csv('images.csv', index=False)

print("CSV generated with", len(files), "files:")
df.head()
