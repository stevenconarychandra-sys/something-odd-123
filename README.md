#Incident Address Scraper

Pulls the Street Address for each INC number listed in column B of a Google
Sheet, using the internal incident portal, and writes results to
column H of the same row.

## Setup

1. Install Python 3.9+ and Google Chrome.
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in:
   - `PORTAL_URL` — the portal's login page
   - `PORTAL_USERNAME` / `PORTAL_PASSWORD` — your SSO login
   - `GOOGLE_CREDS_JSON` — path to your Google service account key file
   - `SPREADSHEET_ID` — from the sheet's URL
   - `SHEET_NAME` — the tab name
4. Share the Google Sheet with your service account's email (found inside
   the JSON key file, field `client_email`) with Editor access.
5. Run: `python main.py`

## About the OTP step

The portal requires an OTP after username/password, sent to your phone or
email, valid for ~60 seconds. The script cannot receive this for you — it
will pause and print a prompt in the terminal. Watch for the OTP and type it
in when asked. After that one-time step, the same browser session is reused
for every INC number, so you won't be asked again mid-run.

## Selectors will need adjusting

I wrote this from a screenshot of the portal, not its live HTML, so the
exact element IDs/XPaths for the login form, search box, and address field
are best guesses based on the visible layout. Every spot that likely needs
adjustment is marked `# ADJUST ME` in `main.py`. To find the real values:

1. Open the portal in Chrome, right-click the field in question (e.g. the
   username box), and choose "Inspect".
2. Note its `id`, `name`, or a unique surrounding text, and update the
   matching locator in `main.py`.

The most likely one to need tweaking is `extract_street_address()`, since
it depends on how the "Street Address" label and its value box are actually
nested in the DOM.

## Rate limiting

There's a 2-second pause between each incident lookup (`DELAY_BETWEEN_LOOKUPS`
in `main.py`) to avoid hammering an internal server. Adjust as needed, but
keep some delay — this is a real production system your account is
authenticated into.

## Security notes

- `.env` holds your real password — don't commit it to git or share it.
  It's already ignored if you're using the included `.gitignore`... but
  there isn't one yet, so make sure to add `.env` to any `.gitignore` you
  create.
- The service account JSON key is equally sensitive — treat it like a
  password.
