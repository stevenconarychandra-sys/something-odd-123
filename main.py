"""
Telkom Incident Address Scraper
--------------------------------
Reads INC numbers from column B of a Google Sheet, looks each one up on the
internal Telkom incident portal, grabs the "Street Address" field from the
Customer Information tab, and writes it back to column H of the same row.

SETUP (one-time):
1. pip install -r requirements.txt
2. Copy .env.example to .env and fill in the values (see comments in that file)
3. Make sure your Google service account email has "Editor" access on the
   actual spreadsheet (share it like you would with a person).
4. Run: python main.py

The portal login uses SSO + a timed OTP. Selenium can't receive the OTP for
you (it's sent to your phone/email), so the script will:
  - open a real, visible browser window
  - fill in username + password
  - pause and ask YOU to type the OTP into the terminal once you receive it
  - continue automatically for every INC number after that, in the same
    logged-in browser session

You will likely need to tweak the CSS/XPath selectors below (marked with
# ADJUST ME) after inspecting the real page in your browser's DevTools (F12),
since I built these from the screenshot layout, not the live HTML.
"""

import os
import sys
import time
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.common.exceptions import TimeoutException, NoSuchElementException, WebDriverException

import gspread
from oauth2client.service_account import ServiceAccountCredentials

load_dotenv()

# ---------- Config (pulled from .env, nothing sensitive hardcoded) ----------
PORTAL_URL = os.environ["PORTAL_URL"]              # e.g. login page URL
PORTAL_USERNAME = os.environ["PORTAL_USERNAME"]
PORTAL_PASSWORD = os.environ["PORTAL_PASSWORD"]

GOOGLE_CREDS_JSON = os.environ["GOOGLE_CREDS_JSON"]  # path to service account json
SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
SHEET_NAME = os.environ.get("SHEET_NAME", "Sheet1")

INC_COLUMN = "B"      # column with INC numbers
OUTPUT_COLUMN = "H"   # column to write street address into
OUTPUT_COLUMN_2 = "AB"  # column to write customer category into
OUTPUT_COLUMN_3 = "AA"  # header title
OUTPUT_COLUMN_4 = "AC"  # service_id
OUTPUT_COLUMN_5 = "AD"  # description_serviceid
OUTPUT_COLUMN_6 = "AE"  # Service No.
START_ROW = 2         # first data row (assumes row 1 is headers)

DELAY_BETWEEN_LOOKUPS = 2  # seconds, be polite to the internal server


# ---------------------------- Google Sheets setup ----------------------------
def get_worksheet():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(GOOGLE_CREDS_JSON, scope)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID)
    return sheet.worksheet(SHEET_NAME)


def col_letter_to_index(letter):
    """'B' -> 2, 'H' -> 8, etc."""
    result = 0
    for ch in letter.upper():
        result = result * 26 + (ord(ch) - ord("A") + 1)
    return result


# ------------------------------ Browser setup --------------------------------
def start_browser():
    options = webdriver.ChromeOptions()
    # Keep the window visible so you can enter the OTP when prompted.
    # options.add_argument("--headless")  # don't enable headless for this flow
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=options)
    driver.maximize_window()
    return driver


def login(driver):
    """
    Opens the login page and hands control to you (the human) to actually log
    in — username, password, and OTP — directly in the Selenium-controlled
    browser window. Once you're through and see the incident search page,
    come back to this terminal and press Enter to let the script continue.

    This avoids needing exact selectors for the login form / OTP box (which
    vary and can't be automated anyway), while still keeping the session
    authenticated for every automated lookup afterward, since it's the same
    browser window/session throughout.
    """
    driver.get(PORTAL_URL)
    print("\n>>> A Chrome window has opened to the INSERA login page.")
    print(">>> Please log in manually now: username, password, then OTP.")
    input(">>> Once you're logged in and see the incident search page, press Enter here to continue...")
    print(">>> Continuing with automated lookups.\n")


# ------------------------------ Lookup logic ----------------------------------
def search_incident(driver, inc_number):
    wait = WebDriverWait(driver, 15)

    # Confirmed real selector from DevTools: id="findIncidentGlobal"
    search_box = wait.until(
        EC.presence_of_element_located((By.ID, "findIncidentGlobal"))
    )
    search_box.clear()
    search_box.send_keys(inc_number)
    search_box.send_keys(Keys.RETURN)  # this site's search box isn't in a <form>, so .submit() fails

    # Wait for the incident detail page to load: the Customer Information tab
    # button becomes enabled (it's disabled until an incident is loaded)
    wait.until(
        EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(),'Customer Information')]]"))
    )


def extract_header_title(driver):
    """Extracts the big incident summary header (h2#header-title-ticket)."""
    wait = WebDriverWait(driver, 15)
    try:
        header = wait.until(EC.presence_of_element_located((By.ID, "header-title-ticket")))
        return (header.text or "").strip()
    except TimeoutException:
        return ""


def extract_service_id_fields(driver):
    """
    Extracts service_id and description_serviceid. These fields appear to
    populate asynchronously via JS, so we wait for a non-empty value rather
    than just presence, with a shorter timeout since they may legitimately
    stay empty for some incidents.
    """
    service_id_val = ""
    description_val = ""
    try:
        field = WebDriverWait(driver, 8).until(
            EC.presence_of_element_located((By.ID, "service_id"))
        )
        service_id_val = (field.get_attribute("value") or "").strip()
    except TimeoutException:
        pass
    try:
        field = driver.find_element(By.ID, "description_serviceid")
        description_val = (field.get_attribute("value") or "").strip()
    except NoSuchElementException:
        pass
    return service_id_val, description_val


def extract_service_no(driver):
    """Extracts the 'Service No.' field on the Incident tab."""
    wait = WebDriverWait(driver, 15)
    try:
        field_input = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[id*='ticketUserInformationAfterRunCrud_service_no']")
            )
        )
        value = field_input.get_attribute("value")
        return (value or "").strip()
    except TimeoutException:
        return ""


def extract_customer_category(driver):
    """
    Extracts the 'Customer Category' field value from the Incident tab
    (the default tab shown right after search, no tab click needed).
    """
    wait = WebDriverWait(driver, 15)
    try:
        field_input = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[id*='ticketUserInformationAfterRunCrud_customer_category']")
            )
        )
        value = field_input.get_attribute("value")
        return (value or "").strip()
    except TimeoutException:
        return ""


def open_customer_information_tab(driver):
    wait = WebDriverWait(driver, 15)
    # Confirmed real markup: <button ... disabled><span>Customer Information</span></button>
    # It's disabled until an incident is loaded, so wait for it to become enabled+clickable.
    tab = wait.until(
        EC.element_to_be_clickable((By.XPATH, "//button[.//span[contains(text(),'Customer Information')]]"))
    )
    tab.click()
    time.sleep(1)  # small buffer for tab content to render


def extract_street_address(driver):
    """
    Looks for the 'Street Address' label and reads the value in the
    adjacent input/box, matching the layout in the screenshot.
    """
    wait = WebDriverWait(driver, 15)
    try:
        # Matches on a stable substring of the id in case the "child_id_3_"
        # prefix varies between incidents/sessions (form-instance numbering).
        address_input = wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "input[id*='customerInformation_street_address']")
            )
        )
        value = address_input.get_attribute("value")
        return (value or "").strip()
    except TimeoutException:
        return ""


# ---------------------------------- Main --------------------------------------
def main():
    ws = get_worksheet()
    inc_col_idx = col_letter_to_index(INC_COLUMN)
    out_col_idx = col_letter_to_index(OUTPUT_COLUMN)
    out_col_idx_2 = col_letter_to_index(OUTPUT_COLUMN_2)
    out_col_idx_3 = col_letter_to_index(OUTPUT_COLUMN_3)
    out_col_idx_4 = col_letter_to_index(OUTPUT_COLUMN_4)
    out_col_idx_5 = col_letter_to_index(OUTPUT_COLUMN_5)
    out_col_idx_6 = col_letter_to_index(OUTPUT_COLUMN_6)

    all_values = ws.get_all_values()
    incidents = []  # list of (row_number, inc_number)
    for row_num, row in enumerate(all_values[START_ROW - 1:], start=START_ROW):
        if len(row) >= inc_col_idx:
            inc_value = row[inc_col_idx - 1].strip()
            if inc_value:
                incidents.append((row_num, inc_value))

    if not incidents:
        print("No INC numbers found in column", INC_COLUMN)
        return

    print(f"Found {len(incidents)} incidents to look up.")

    driver = start_browser()
    try:
        login(driver)

        for row_num, inc_number in incidents:
            print(f"Looking up {inc_number} (row {row_num})...")
            try:
                search_incident(driver, inc_number)

                # Incident tab is the default/first tab, no click needed
                customer_category = extract_customer_category(driver)
                header_title = extract_header_title(driver)
                service_id_val, description_serviceid_val = extract_service_id_fields(driver)
                service_no = extract_service_no(driver)

                open_customer_information_tab(driver)
                address = extract_street_address(driver)

                if address:
                    ws.update_cell(row_num, out_col_idx, address)
                    print(f"  -> address: {address}")
                else:
                    print("  -> No address found, skipping.")

                if customer_category:
                    ws.update_cell(row_num, out_col_idx_2, customer_category)
                    print(f"  -> customer category: {customer_category}")
                else:
                    print("  -> No customer category found, skipping.")

                if header_title:
                    ws.update_cell(row_num, out_col_idx_3, header_title)
                    print(f"  -> header title: {header_title[:60]}...")
                else:
                    print("  -> No header title found, skipping.")

                if service_id_val:
                    ws.update_cell(row_num, out_col_idx_4, service_id_val)
                    print(f"  -> service_id: {service_id_val}")
                else:
                    print("  -> No service_id found, skipping.")

                if description_serviceid_val:
                    ws.update_cell(row_num, out_col_idx_5, description_serviceid_val)
                    print(f"  -> description_serviceid: {description_serviceid_val}")
                else:
                    print("  -> No description_serviceid found, skipping.")

                if service_no:
                    ws.update_cell(row_num, out_col_idx_6, service_no)
                    print(f"  -> service_no: {service_no}")
                else:
                    print("  -> No service_no found, skipping.")

            except (TimeoutException, NoSuchElementException, WebDriverException) as e:
                print(f"  -> Failed to process {inc_number}: {type(e).__name__}: {e}")

            time.sleep(DELAY_BETWEEN_LOOKUPS)

    finally:
        driver.quit()

    print("\nDone.")


if __name__ == "__main__":
    main()