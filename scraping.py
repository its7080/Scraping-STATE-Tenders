"""
||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||
Author  : Anupam Manna
Email   : am7059141480@gmail.com
Mobile  : +91 7059141480
Updated : 2025
Description: State tenders automation.
  • Playwright (sync API) replaces Selenium
  • CAPTCHA solved via option_detect() from captcha_ocr_main.py
  • PyInstaller + Windows Task Scheduler compatible
  • Paths always resolved relative to the executable (sys.executable),
    so the .exe works correctly from any working directory
  • All stdout/stderr flushed immediately (for Task Scheduler log capture)
  • Exit code 0 = success, 1 = fatal error  (Task Scheduler reads this)
  • All output xlsx files saved in PROGRAM_FILES_DIR
  • Merged master file attached to the report email
  • 4-thread pool (ThreadPoolExecutor) — always exactly 4 Chromium
    instances running; CNN inference serialised via lock so TensorFlow
    is never called from two threads simultaneously
  • All tuneable constants exposed as module-level variables so the
    GUI settings panel can update them live without restarting
||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||
"""

# =======================
# FROZEN-EXE BOOTSTRAP
# Must be the very first executable lines — before any other import —
# so that PyInstaller's multiprocessing support initialises correctly
# on Windows when the .exe is launched by Task Scheduler.
# =======================
import multiprocessing
multiprocessing.freeze_support()

import base64
import concurrent.futures
import datetime
import json
import logging
import os
import re
import smtplib
import socket
import sys
import threading
import time
import platform

import numpy as np
import pandas as pd
import xlsxwriter

from PIL import Image
from io import BytesIO

from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

# Single entry-point from captcha_ocr_main.py
from captcha_ocr_main import option_detect

from Program_Files.scraping_library import (
    delete_folder,
    packaging,
    create_folder_if_not_exists,
    delete_xlsx_files,
)


# =======================
# BASE DIRECTORY
# =======================
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# =======================
# PATHS
# =======================
PROGRAM_FILES_DIR = os.path.join(BASE_DIR, "Program_Files")
DATA_FILES_DIR    = os.path.join(BASE_DIR, "Data_Files")

TEMP_DIR   = os.path.join(PROGRAM_FILES_DIR, "temp_dir")
OUTPUT_DIR = os.path.join(PROGRAM_FILES_DIR, "Output")
LOG_DIR    = os.path.join(PROGRAM_FILES_DIR, "#log")
CAP_DIR    = os.path.join(PROGRAM_FILES_DIR, "CAP")

for _d in (DATA_FILES_DIR, TEMP_DIR, OUTPUT_DIR, LOG_DIR, CAP_DIR):
    os.makedirs(_d, exist_ok=True)

CONFIG_FILE   = os.path.join(PROGRAM_FILES_DIR, "Configration.json")
CRITERIA_FILE = os.path.join(PROGRAM_FILES_DIR, "search_criteria.json")
ORG_FILE      = os.path.join(PROGRAM_FILES_DIR, "Organization_list.txt")


# =======================
# LOGGING
# =======================
_LOG_FILENAME = os.path.join(
    LOG_DIR,
    f"State-tenders_{datetime.datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.log",
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  [%(threadName)s]  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(_LOG_FILENAME, encoding="utf-8"),
    ],
    force=True,
)
log = logging.getLogger(__name__)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)


# =======================
# CONFIG  — single source of truth: Configration.json
# Every tuneable constant is read from here.  The GUI writes all changes
# back to this file via save_config(), so settings persist across restarts.
# =======================
def load_config() -> dict:
    """Load Configration.json and return the dict."""
    with open(CONFIG_FILE, "r", encoding="utf-8") as _f:
        return json.load(_f)


def save_config(cfg: dict):
    """Write cfg back to Configration.json atomically."""
    with open(CONFIG_FILE, "w", encoding="utf-8") as _f:
        json.dump(cfg, _f, indent=4)


# =======================
# SEARCH CRITERIA  — stored in search_criteria.json
#
# Full portal form field reference (from eprocure HTML):
#   TenderType       : 0=Select, 1=Open Tender, 2=Limited Tender
#   OrganisationName : 0=Select, 1-240=specific orgs (0 = all)
#   Department       : 0=Select  (dynamic, depends on org)
#   Division         : 0=Select  (dynamic)
#   SubDivision      : 0=Select  (dynamic)
#   tenderCategory   : 0=Select, 1=Goods, 2=Services, 3=Works
#   ProductCategory  : 0=Select, 1-82=specific categories
#   formContract     : 0=Select, 1=Buy … 19=Works
#   PaymentMode      : 0=Select, 1=Offline, 2=Online, 3=Both, 4=Not Applicable
#   valueCriteria    : 0=Select, 1=EMD, 2=Tender Fee, 3=Processing Fee, 4=ECV
#   valueParameter   : 0=Select, 1=Equal, 2=LessThan, 3=GreaterThan, 4=Between
#   FromValue        : numeric amount
#   ToValue          : numeric (only used when valueParameter=4=Between)
#   dateCriteria     : 0=Select, 1=Published, 2=DocDownloadStart, 3=DocDownloadEnd,
#                      4=BidSubmitStart, 5=BidSubmitEnd
#   fromDate         : dd/MM/yyyy string  (leave "" to skip)
#   toDate           : dd/MM/yyyy string  (leave "" to skip)
#   pinCode          : string (leave "" to skip)
#   workItemTitle    : string (leave "" to skip)
#   tenderId         : string (leave "" to skip)
#   tenderRefNo      : string (leave "" to skip)
#   twoStageAllowed  : bool checkbox
#   ndaAllowed       : bool checkbox
#   prefBidAllowed   : bool checkbox
#   chkGteAllowed    : bool checkbox
#   chkIteAllowed    : bool checkbox
#   chkTfeAllowed    : bool checkbox
#   chkEfeAllowed    : bool checkbox
#
# NOTE: valueParameter mapping confirmed from portal HTML source:
#   1 = Equal   |   2 = LessThan   |   3 = GreaterThan   |   4 = Between
# =======================
DEFAULT_CRITERIA = [
    {
        "label":           "Pass 1 — Open Tender, ECV GreaterThan 0",
        "enabled":         True,
        # Tender type
        "tender_type":     1,        # 1 = Open Tender
        # Organisation / department (0 = all)
        "organisation":    0,
        "department":      0,
        "division":        0,
        "sub_division":    0,
        # Category filters (0 = all)
        "tender_category": 0,
        "product_category": 0,
        "form_contract":   0,
        "payment_mode":    0,
        # Value filter
        "value_criteria":  4,        # 4 = ECV
        "value_param":     3,        # 3 = GreaterThan  ← corrected from portal HTML
        "from_value":      0,
        "to_value":        0,        # only used when value_param=4 (Between)
        # Date filter (empty = skip)
        "date_criteria":   0,
        "from_date":       "",
        "to_date":         "",
        # Free-text filters (empty = skip)
        "pin_code":        "",
        "work_item_title": "",
        "tender_id":       "",
        "tender_ref_no":   "",
        # Checkboxes
        "two_stage":       False,
        "nda":             False,
        "pref_bid":        False,
        "gte":             False,
        "ite":             False,
        "tfe":             False,
        "efe":             False,
    },
    {
        "label":           "Pass 2 — Open Tender, ECV Equal 99999999",
        "enabled":         True,
        "tender_type":     1,
        "organisation":    0,
        "department":      0,
        "division":        0,
        "sub_division":    0,
        "tender_category": 0,
        "product_category": 0,
        "form_contract":   0,
        "payment_mode":    0,
        "value_criteria":  4,        # 4 = ECV
        "value_param":     1,        # 1 = Equal  ← corrected from portal HTML
        "from_value":      99_999_999,
        "to_value":        0,
        "date_criteria":   0,
        "from_date":       "",
        "to_date":         "",
        "pin_code":        "",
        "work_item_title": "",
        "tender_id":       "",
        "tender_ref_no":   "",
        "two_stage":       False,
        "nda":             False,
        "pref_bid":        False,
        "gte":             False,
        "ite":             False,
        "tfe":             False,
        "efe":             False,
    },
]


def load_criteria() -> list[dict]:
    """
    Load search_criteria.json.  If the file does not exist yet, create it
    with DEFAULT_CRITERIA and return those defaults.
    """
    if not os.path.exists(CRITERIA_FILE):
        save_criteria(DEFAULT_CRITERIA)
        return [dict(c) for c in DEFAULT_CRITERIA]
    with open(CRITERIA_FILE, "r", encoding="utf-8") as _f:
        data = json.load(_f)
    # Ensure every criterion has all required keys (forward-compat)
    result = []
    for c in data:
        row = dict(DEFAULT_CRITERIA[0])   # defaults for any missing key
        row.update(c)
        result.append(row)
    return result


def save_criteria(criteria: list[dict]):
    """Write criteria list to search_criteria.json."""
    with open(CRITERIA_FILE, "w", encoding="utf-8") as _f:
        json.dump(criteria, _f, indent=4)


# Module-level criteria list — patched live by the GUI after save
search_criteria: list[dict] = load_criteria()


def _apply_config(cfg: dict):
    """
    Push every key from cfg into the matching module-level global.
    Called once at startup and again after every GUI save so that
    all engine code picks up the new values immediately.
    Search criteria are NOT stored here — they live in search_criteria.json
    and are managed via load_criteria() / save_criteria().
    """
    global BROWSER_HEADLESS, DUMP_LOCATION
    global SENDER_EMAIL, SENDER_PASS, NOTIFY_EMAILS
    global SMTP_SERVER, SMTP_PORT, EMAIL_SUBJECT, ATTACH_LOG
    global MERGED_FILE_PREFIX, DELETE_INDIVIDUAL_AFTER_MERGE, LOG_RETENTION_DAYS
    global MAX_WORKERS, CAPTCHA_ATTEMPTS, PAGE_TIMEOUT_SEC
    global NAV_RETRIES, PAGE_LOAD_WAIT_SEC
    global OUTPUT_DIR

    BROWSER_HEADLESS = cfg.get("browser", "0") == "0"
    DUMP_LOCATION    = cfg.get("dump_location", DATA_FILES_DIR)

    # Credentials: env vars first, JSON fallback
    SENDER_EMAIL  = os.environ.get("TENDER_SENDER_EMAIL") or cfg.get("sender_email_id", "")
    SENDER_PASS   = os.environ.get("TENDER_SENDER_PASS")  or cfg.get("sender_email_password", "")
    NOTIFY_EMAILS = cfg.get("notification_emailids", [])

    # SMTP / email
    SMTP_SERVER   = cfg.get("smtp_server",        "smtp.office365.com")
    SMTP_PORT     = int(cfg.get("smtp_port",      587))
    EMAIL_SUBJECT = cfg.get("email_subject",      "State Tender Scraping Report")
    ATTACH_LOG    = bool(cfg.get("attach_log_to_email", True))

    # Output / file management
    MERGED_FILE_PREFIX           = cfg.get("merged_file_prefix",           "merged_State")
    DELETE_INDIVIDUAL_AFTER_MERGE = bool(cfg.get("delete_individual_after_merge", False))
    LOG_RETENTION_DAYS           = int(cfg.get("log_retention_days",       30))

    # Custom output dir (optional — falls back to PROGRAM_FILES_DIR/Output)
    _custom_out = cfg.get("output_dir", "")
    if _custom_out:
        OUTPUT_DIR = _custom_out
        os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Scraping behaviour
    MAX_WORKERS        = int(cfg.get("max_workers",        4))
    CAPTCHA_ATTEMPTS   = int(cfg.get("captcha_attempts",   5))
    PAGE_TIMEOUT_SEC   = int(cfg.get("page_timeout_sec",   240))
    NAV_RETRIES        = int(cfg.get("nav_retries",        3))
    PAGE_LOAD_WAIT_SEC = int(cfg.get("page_load_wait_sec", 5))


# Declare globals first so _apply_config can assign them
BROWSER_HEADLESS              = True
DUMP_LOCATION                 = ""
SENDER_EMAIL                  = ""
SENDER_PASS                   = ""
NOTIFY_EMAILS: list           = []
SMTP_SERVER                   = "smtp.office365.com"
SMTP_PORT                     = 587
EMAIL_SUBJECT                 = "State Tender Scraping Report"
ATTACH_LOG                    = True
MERGED_FILE_PREFIX            = "merged_State"
DELETE_INDIVIDUAL_AFTER_MERGE = False
LOG_RETENTION_DAYS            = 30
MAX_WORKERS                   = 4
CAPTCHA_ATTEMPTS              = 5
PAGE_TIMEOUT_SEC              = 240
NAV_RETRIES                   = 3
PAGE_LOAD_WAIT_SEC            = 5

# Load and apply on startup
_apply_config(load_config())


# =======================
# ORGANISATIONS
# =======================
organizations: list[tuple[str, str]] = []

with open(ORG_FILE, "r", encoding="utf-8") as _f:
    for _line in _f:
        _line = _line.strip()
        if not _line or _line.startswith("#"):
            continue
        _parts = _line.split(": ", 1)
        if len(_parts) == 2:
            organizations.append((_parts[0], _parts[1]))


# =======================
# SHARED STATE
# =======================
_error_lock   = threading.Lock()
error_data:   dict = {}
_captcha_lock = threading.Lock()


# =======================
# CAPTCHA HELPER
# =======================
def solve_captcha(img_b64: str, name: str) -> str | None:
    img_b64 = (
        img_b64
        .replace("\\n", "")
        .replace("\\r", "")
        .replace("%0A", "")
    )

    cap_path = os.path.join(CAP_DIR, f"{name}.png")

    try:
        image_data = base64.b64decode(img_b64)
        img = Image.open(BytesIO(image_data))
        if img.mode in ("RGBA", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
        img.save(cap_path, "PNG")
    except Exception as exc:
        log.error("[%s] Failed to save CAPTCHA image: %s", name, exc)
        return None

    try:
        with _captcha_lock:
            result = option_detect(image_path=cap_path)
        log.info("[%s] CAPTCHA raw result: %s", name, result)
    except Exception as exc:
        log.error("[%s] option_detect raised: %s", name, exc)
        return None

    if result and re.match(r"^[A-Za-z0-9]{6}$", str(result).strip()):
        return str(result).strip()

    log.warning("[%s] CAPTCHA prediction '%s' failed validation.", name, result)
    return None


# =======================
# TENDER EXTRACTOR
# =======================
class Extr:
    """
    Scrapes one portal for tenders matching one search criterion dict.
    The criterion carries all form fields defined in search_criteria.json.
    Behaviour constants (timeouts, retries) are snapshotted from module
    globals at construction time so the GUI can change them between runs.
    """

    def __init__(
        self,
        name: str,
        url: str,
        temp_dir: str,
        criterion: dict,
    ):
        self.name      = name
        self.url       = url
        self.temp_dir  = temp_dir
        self.criterion = criterion   # full criterion dict from search_criteria.json
        # Snapshot module constants
        self._max_captcha = CAPTCHA_ATTEMPTS
        self._timeout_ms  = PAGE_TIMEOUT_SEC * 1000
        self._nav_retries = NAV_RETRIES
        self._wait_ms     = PAGE_LOAD_WAIT_SEC * 1000

    def run(self) -> int:
        no_scraped = 0
        workbook   = None
        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(
                    headless=BROWSER_HEADLESS,
                    args=[
                        "--disable-extensions",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-application-cache",
                        "--ignore-certificate-errors",
                        "--disable-dev-shm-usage",
                    ],
                )
                context = browser.new_context(ignore_https_errors=True)
                page    = context.new_page()
                page.set_default_timeout(self._timeout_ms)
                try:
                    no_scraped, workbook = self._scrape(page)
                finally:
                    context.close()
                    browser.close()
        except Exception as exc:
            log.error("[%s] Playwright session failed: %s", self.name, exc)
            if workbook is not None:
                try:
                    workbook.close()
                except Exception:
                    pass
            with _error_lock:
                error_data[self.name] = {
                    "Status": "Not Successfully Run",
                    "Total Tenders Scraped": no_scraped,
                    "Error": str(exc),
                }
        return no_scraped

    def _navigate(self, page) -> bool:
        for attempt in range(1, self._nav_retries + 1):
            try:
                page.goto(self.url, wait_until="domcontentloaded",
                          timeout=self._timeout_ms)
                return True
            except PlaywrightTimeout:
                log.warning("[%s] Navigation timeout attempt %d/%d.",
                            self.name, attempt, self._nav_retries)
                time.sleep(2)
        log.error("[%s] Navigation failed after %d attempts.",
                  self.name, self._nav_retries)
        return False

    def _get_captcha_b64(self, page) -> str | None:
        el = page.query_selector("img#captchaImage")
        if not el:
            log.warning("[%s] captchaImage not found on page.", self.name)
            return None
        src = el.get_attribute("src") or ""
        return src.split(",", 1)[-1] if "," in src else src

    def _submit_form(self, page, captcha_text: str):
        """
        Populate every search form field from self.criterion then submit.
        Fields not present in the criterion default to their 'all / none'
        values so no accidental filter is applied.
        """
        c = self.criterion   # shorthand

        def _js_set(field_id: str, value) -> str:
            """Return a JS statement that sets a field value."""
            safe = str(value).replace("'", "\\'")
            return f"document.getElementById('{field_id}').value = '{safe}';"

        def _js_check(field_id: str, checked: bool) -> str:
            state = "true" if checked else "false"
            return f"document.getElementById('{field_id}').checked = {state};"

        js = "\n".join([
            # CAPTCHA
            _js_set("captchaText",       captcha_text),
            # Tender type / org / department hierarchy
            _js_set("TenderType",        c.get("tender_type",      1)),
            _js_set("OrganisationName",  c.get("organisation",     0)),
            _js_set("Department",        c.get("department",       0)),
            _js_set("Division",          c.get("division",         0)),
            _js_set("SubDivision",       c.get("sub_division",     0)),
            # Category filters
            _js_set("tenderCategory",    c.get("tender_category",  0)),
            _js_set("ProductCategory",   c.get("product_category", 0)),
            _js_set("formContract",      c.get("form_contract",    0)),
            _js_set("PaymentMode",       c.get("payment_mode",     0)),
            # Value filter
            _js_set("valueCriteria",     c.get("value_criteria",   4)),
            _js_set("valueParameter",    c.get("value_param",      3)),
            _js_set("FromValue",         c.get("from_value",       0)),
            _js_set("ToValue",           c.get("to_value",         0)),
            # Date filter
            _js_set("dateCriteria",      c.get("date_criteria",    0)),
            _js_set("fromDate",          c.get("from_date",        "")),
            _js_set("toDate",            c.get("to_date",          "")),
            # Free-text filters
            _js_set("pinCode",           c.get("pin_code",         "")),
            _js_set("workItemTitle",     c.get("work_item_title",  "")),
            _js_set("tenderId",          c.get("tender_id",        "")),
            _js_set("tenderRefNo",       c.get("tender_ref_no",    "")),
            # Checkboxes
            _js_check("twoStageAllowed", c.get("two_stage",  False)),
            _js_check("ndaAllowed",      c.get("nda",        False)),
            _js_check("prefBidAllowed",  c.get("pref_bid",   False)),
            _js_check("chkGteAllowed",   c.get("gte",        False)),
            _js_check("chkIteAllowed",   c.get("ite",        False)),
            _js_check("chkTfeAllowed",   c.get("tfe",        False)),
            _js_check("chkEfeAllowed",   c.get("efe",        False)),
        ])

        page.evaluate(f"() => {{ {js} }}")
        page.click("#submit")
        page.wait_for_load_state("domcontentloaded")

    def _get_text(self, page, xpath: str, wait: bool = False) -> str | None:
        try:
            if wait:
                page.locator(f"xpath={xpath}").first.wait_for(
                    state="attached", timeout=20_000)
            el = page.query_selector(f"xpath={xpath}")
            return el.inner_text().strip() if el else None
        except Exception:
            return None

    def _scrape(self, page) -> tuple[int, "xlsxwriter.Workbook | None"]:
        links_count = 0
        for attempt in range(1, self._max_captcha + 1):
            log.info("[%s] CAPTCHA attempt %d/%d",
                     self.name, attempt, self._max_captcha)
            if not self._navigate(page):
                return 0, None
            page.wait_for_timeout(self._wait_ms)
            page.reload()
            page.wait_for_timeout(self._wait_ms)

            img_b64 = self._get_captcha_b64(page)
            if not img_b64:
                continue
            captcha_text = solve_captcha(img_b64, self.name)
            if not captcha_text:
                log.warning("[%s] CAPTCHA solve failed, retrying.", self.name)
                continue
            log.info("[%s] CAPTCHA solved: %s", self.name, captcha_text)
            try:
                self._submit_form(page, captcha_text)
            except Exception as exc:
                log.warning("[%s] Form submit error: %s", self.name, exc)
                continue
            if "No Tenders found." in page.content():
                log.info("[%s] Portal returned no tenders.", self.name)
                return 0, None
            links = page.query_selector_all(
                "xpath=//td/a[starts-with(@id,'DirectLink_0')]")
            links_count = len(links)
            if links_count > 0:
                log.info("[%s] %d result links found.", self.name, links_count)
                break
            log.warning("[%s] No result links after submit.", self.name)

        if links_count == 0:
            log.error("[%s] No results after %d CAPTCHA attempts.",
                      self.name, self._max_captcha)
            return 0, None

        ts        = datetime.datetime.now().strftime("%d-%m-%Y %H_%M_%S")
        file_path = os.path.join(self.temp_dir, f"{self.name}_Tenders_{ts}.xlsx")
        workbook  = xlsxwriter.Workbook(file_path)
        ws        = workbook.add_worksheet("ListOfTenders")

        for col, h in enumerate([
            "Organisation Chain", "Tender Reference Number", "Tender ID",
            "EMD Amount in Rs", "Title", "Work Description",
            "Tender Value in Rs", "Pre Bid Meeting Date",
            "Bid Submission End Date", "Published Date",
            "Tender Type", "Tender Category", "Tender Fee",
            "Location", "Period Of Work(Days)",
            "Document Download / Sale End Date", "URL", "GET",
        ]):
            ws.write(0, col, h)

        no_scraped = 0
        page_num   = 0
        today_str  = datetime.datetime.now().date().strftime("%d/%m/%Y")
        url_label  = f"{self.name}_Tenders"

        def g(xpath, wait=False):
            return self._get_text(page, xpath, wait=wait)

        while True:
            for j in range(1, links_count + 1):
                if j in (7, 14, 20):
                    page.wait_for_timeout(self._wait_ms)
                    page.reload()
                    page.wait_for_timeout(2_000)

                elements = page.query_selector_all(
                    "xpath=//a[starts-with(@id,'DirectLink_0')]")
                if j > len(elements):
                    break
                try:
                    elements[j - 1].click()
                except Exception as exc:
                    log.warning("[%s] Click error item %d: %s", self.name, j, exc)
                    break

                page.wait_for_selector(
                    "xpath=//*[text()='Organisation Chain']",
                    state="attached", timeout=20_000)

                row = (page_num * 20) + j
                ws.write(row,  0, g("//*[text()='Organisation Chain']/parent::*/following-sibling::td[1]", wait=True))
                ws.write(row,  1, g("//*[text()='Tender Reference Number']/parent::*/following-sibling::td[1]"))
                ws.write(row,  2, g("//*[text()='Tender ID']/parent::*/following-sibling::td[1]", wait=True))
                ws.write(row,  3, g("//*[contains(text(),'EMD Amount in')]/following-sibling::td[1]"))
                ws.write(row,  4, g("//*[text()='Title']/parent::*/following-sibling::td[1]"))
                ws.write(row,  5, g("//*[text()='Work Description']/parent::*/following-sibling::td[1]"))
                ws.write(row,  6, g("//*[contains(text(),'Tender Value in')]/following-sibling::td[1]"))
                ws.write(row,  7, g("//*[text()='Pre Bid Meeting Date']/parent::*/following-sibling::td[1]"))
                ws.write(row,  8, g("//*[text()='Bid Submission End Date']/parent::*/following-sibling::td[1]"))
                ws.write(row,  9, g("//*[text()='Published Date']/parent::*/following-sibling::td[1]"))
                ws.write(row, 10, g("//*[contains(text(),'Tender Type')]/following-sibling::td[1]"))
                ws.write(row, 11, g("//*[contains(text(),'Tender Category')]/following-sibling::td[1]", wait=True))
                if self.name not in ("Coal_India", "IOCL", "West_Bengal"):
                    ws.write(row, 12, g("//*[contains(text(),'Tender Fee in')]/following-sibling::td[1]", wait=True))
                else:
                    ws.write(row, 12, None)
                ws.write(row, 13, g("//*[text()='Location']/parent::*/following-sibling::td[1]"))
                ws.write(row, 14, g("//*[text()='Period Of Work(Days)']/parent::*/following-sibling::td[1]"))
                ws.write(row, 15, g("//*[text()='Document Download / Sale End Date']/parent::*/following-sibling::td[1]"))
                ws.write(row, 16, url_label)
                ws.write(row, 17, today_str)

                back = page.query_selector(
                    "xpath=//a[@id='DirectLink_11' and text()='Back']")
                if back:
                    back.click()
                    page.wait_for_timeout(1_000)

                no_scraped += 1
                log.info("[%s] p%d item%d → total %d",
                         self.name, page_num + 1, j, no_scraped)

            page_num += 1
            next_btn = page.query_selector("xpath=.//a[@id='linkFwd']")
            if not next_btn:
                break
            next_btn.click()
            page.wait_for_timeout(2_000)
            new_links = page.query_selector_all(
                "xpath=//a[starts-with(@id,'DirectLink_0')]")
            links_count = len(new_links)
            if links_count == 0:
                break

        workbook.close()
        packaging()
        log.info("[%s] Done. %d tenders scraped.", self.name, no_scraped)
        return no_scraped, None


# =======================
# EMAIL
# =======================
def send_mail(merged_file: str | None = None, attach_log: bool = False):
    body = (
        "Automated notification: State tender scraping has completed.\n"
        "Please verify the results.\n\n"
        f"System    : {platform.system()}\n"
        f"Hostname  : {platform.node()}\n"
        f"IP        : {socket.gethostbyname(socket.gethostname())}\n"
        f"Directory : {BASE_DIR}\n"
        f"OS        : {platform.version()}\n\n"
        + json.dumps(error_data, indent=4)
    )

    msg = MIMEMultipart()
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = ", ".join(NOTIFY_EMAILS)
    msg["Subject"] = EMAIL_SUBJECT
    msg.attach(MIMEText(body, "plain"))

    if merged_file and os.path.exists(merged_file) and os.path.getsize(merged_file) > 0:
        with open(merged_file, "rb") as att:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(att.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition",
                            f"attachment; filename={os.path.basename(merged_file)}")
            msg.attach(part)
        log.info("Attaching merged file: %s", os.path.basename(merged_file))
    else:
        log.warning("Merged file not found or empty — skipping attachment.")

    if attach_log and ATTACH_LOG and os.path.exists(_LOG_FILENAME) and os.path.getsize(_LOG_FILENAME) > 0:
        with open(_LOG_FILENAME, "rb") as att:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(att.read())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition",
                            f"attachment; filename={os.path.basename(_LOG_FILENAME)}")
            msg.attach(part)

    server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT)
    try:
        server.starttls()
        server.login(SENDER_EMAIL, SENDER_PASS)
        server.sendmail(SENDER_EMAIL, NOTIFY_EMAILS, msg.as_string())
        log.info("Report email sent.")
    except Exception as exc:
        log.error("Email failed: %s", exc)
    finally:
        server.quit()


# =======================
# MERGE XLSX
# =======================
def merge_xlsx_files(source_dir: str, TEMP_DIR: str) -> str:
    os.makedirs(TEMP_DIR, exist_ok=True)
    merged = pd.DataFrame()
    individual_files = []

    for fname in os.listdir(source_dir):
        if not fname.endswith(".xlsx") or fname.startswith(MERGED_FILE_PREFIX):
            continue
        fpath = os.path.join(source_dir, fname)
        try:
            df = pd.read_excel(fpath)
            df = df.dropna(how="all", axis=1)
            df.replace("NA", 0.00, inplace=True)
            df["Get Date"] = datetime.datetime.now().strftime("%d/%m/%Y")
            merged = pd.concat([merged, df], ignore_index=True)
            individual_files.append(fpath)
        except Exception as exc:
            log.warning("Could not read %s: %s", fpath, exc)

    ts          = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    output_path = os.path.join(PROGRAM_FILES_DIR, f"{MERGED_FILE_PREFIX}_{ts}.xlsx")
    merged.to_excel(output_path, index=False)
    log.info("Master xlsx → %s", output_path)

    if DELETE_INDIVIDUAL_AFTER_MERGE:
        for fpath in individual_files:
            try:
                os.remove(fpath)
                log.info("Deleted individual file: %s", os.path.basename(fpath))
            except Exception as exc:
                log.warning("Could not delete %s: %s", fpath, exc)

    return output_path


# =======================
# LOG RETENTION
# =======================
def purge_old_logs():
    """Delete log files older than LOG_RETENTION_DAYS from LOG_DIR."""
    if LOG_RETENTION_DAYS <= 0:
        return
    cutoff = time.time() - LOG_RETENTION_DAYS * 86400
    for fname in os.listdir(LOG_DIR):
        fpath = os.path.join(LOG_DIR, fname)
        try:
            if os.path.isfile(fpath) and os.path.getmtime(fpath) < cutoff:
                os.remove(fpath)
                log.info("Purged old log: %s", fname)
        except Exception as exc:
            log.warning("Could not purge %s: %s", fname, exc)


# =======================
# OPTION-VALUE LABEL LOOKUP  (for readable log output)
# =======================
_OPTION_LABELS: dict[str, dict[int, str]] = {
    "TenderType":     {0: "Select", 1: "Open Tender", 2: "Limited Tender"},
    "valueCriteria":  {0: "Select", 1: "EMD", 2: "Tender Fee",
                       3: "Processing Fee", 4: "ECV"},
    "valueParameter": {0: "Select", 1: "Equal", 2: "LessThan",
                       3: "GreaterThan", 4: "Between"},
    "tenderCategory": {0: "All", 1: "Goods", 2: "Services", 3: "Works"},
    "formContract":   {0: "All", 1: "Buy", 2: "Empanelment", 3: "EOI",
                       4: "EPC Contract", 5: "Fixed-rate", 6: "Item Rate",
                       7: "Lump-sum", 8: "Multi-stage", 9: "Percentage",
                       10: "Piece-work", 11: "PPP-BoT-HAM", 12: "PPP-BoT-ToT",
                       13: "PPP-DBFOT", 14: "QCBS", 15: "Sale", 16: "Supply",
                       17: "Tender cum Auction", 18: "Turn-key", 19: "Works"},
    "PaymentMode":    {0: "All", 1: "Offline", 2: "Online",
                       3: "Both(Online/Offline)", 4: "Not Applicable"},
    "dateCriteria":   {0: "None", 1: "Published Date",
                       2: "Doc Download Start", 3: "Doc Download End",
                       4: "Bid Submit Start", 5: "Bid Submit End"},
    "ProductCategory": {
        0: "All", 1: "Access Control System", 2: "Advertisement Services",
        3: "Agricultural or Forestry", 4: "Allotment of Space",
        5: "AMC/ Maintenance Contracts", 6: "Architecture/Interior Design",
        7: "Audio-Visual Equipment", 8: "Chemicals/Minerals",
        9: "Civil Construction Goods", 10: "Civil Works",
        11: "Civil Works - Bridges", 12: "Civil Works - Buildings",
        13: "Civil Works - Highways", 14: "Civil Works - Others",
        15: "Civil Works - Roads", 16: "Civil Works - Water Works",
        17: "Coal Works", 18: "Computer- Data Processing",
        19: "Computer- H/W", 20: "Computer- S/W",
        21: "Construction Works", 22: "Consultancy",
        23: "Consumables (Hospital / Lab)",
        24: "Consumables - Paper/Printing", 25: "Consumables- Raw materials",
        26: "Drilling Works", 27: "Drugs and Pharmaceutical Products",
        28: "Edible Oils", 29: "Electrical Goods/Equipment",
        30: "Electrical Works", 31: "Electronic Components",
        32: "Electronics Equipment", 33: "Equipments (Hospital / Lab)",
        34: "Facility Management Services", 35: "Financial and Insurance",
        36: "Food Products", 37: "Furniture/ Fixture",
        38: "Government Stock/Security", 39: "Hiring of Vehicles",
        40: "Hotel/ Catering", 41: "Housekeeping/ Cleaning",
        42: "Information Technology", 43: "Info. Tech. Services",
        44: "Job Works", 45: "Lab Chemistry Reagents",
        46: "Laboratory and scientific equipment", 47: "Land/Building",
        48: "Machineries/ Mechanical Engg Items",
        49: "Machinery and Machining Tools", 50: "Manpower Supply",
        51: "Marine Services", 52: "Marine Works",
        53: "Mechanical Tools and Equipment", 54: "Medical Equipments/Waste",
        55: "Medicines", 56: "Metal Fabrication", 57: "Metals",
        58: "Metals - Ferrous", 59: "Miscellaneous Goods",
        60: "Miscellaneous Services", 61: "Miscellaneous Works",
        62: "Network /Communication Equipments",
        63: "Non Consumables (Hospital / Lab)", 64: "OFC Laying Works",
        65: "Oil/Gas", 66: "Paint / Enamel Works", 67: "Pipe Laying Works",
        68: "Pipes and Pipe related activities",
        69: "Plant Protection Input/Equipment Works",
        70: "Power/Energy Projects/Products", 71: "Publishing/Printing",
        72: "Pumps/Motors", 73: "Renting out / Licensing out",
        74: "Repair and Maintenance Services", 75: "Shipping Services",
        76: "Shipping/ Transportation/ Vehicle", 77: "Stone Works",
        78: "Supply, Erection and Commissioning",
        79: "Support/Maintenance Service", 80: "Surveillance Equipments",
        81: "Survey and Investigation services",
        82: "Water Equipments/ Meter/ Drilling/ Boring",
    },
}


def _vc_label(field: str, value: int) -> str:
    """Return human-readable label for a form field option value."""
    return _OPTION_LABELS.get(field, {}).get(int(value), str(value))


# =======================
# PORTAL PROCESSING
# =======================
def process_portal(portal: tuple[str, str]):
    """
    Run every enabled search criterion for one portal in sequence.
    Each criterion carries the complete set of form fields to fill.
    """
    name, url = portal
    enabled = [c for c in search_criteria if c.get("enabled", True)]

    for idx, criterion in enumerate(enabled, start=1):
        label = criterion.get("label", f"Pass {idx}")
        log.info(
            "\n|%s> Pass %d for portal: %s\n"
            "|    Label           : %s\n"
            "|    Tender Type     : %s\n"
            "|    Value Criteria  : %s  |  Value Param: %s  |  From: %s  |  To: %s\n"
            "|    Category        : %s  |  Product: %s  |  Contract: %s\n"
            "|    Date Criteria   : %s  |  From: %s  |  To: %s |",
            "-" * (8 + idx), idx, name,
            label,
            _vc_label("TenderType",     criterion.get("tender_type",      1)),
            _vc_label("valueCriteria",  criterion.get("value_criteria",   4)),
            _vc_label("valueParameter", criterion.get("value_param",      3)),
            criterion.get("from_value", 0),
            criterion.get("to_value",   0),
            _vc_label("tenderCategory",  criterion.get("tender_category",  0)),
            _vc_label("ProductCategory", criterion.get("product_category", 0)),
            _vc_label("formContract",    criterion.get("form_contract",    0)),
            _vc_label("dateCriteria",    criterion.get("date_criteria",    0)),
            criterion.get("from_date", "") or "—",
            criterion.get("to_date",   "") or "—",
        )
        Extr(name, url, TEMP_DIR, criterion=criterion).run()


def run_all_portals_threaded():
    log.info("Starting pool: %d workers for %d portals",
             MAX_WORKERS, len(organizations))
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=MAX_WORKERS,
        thread_name_prefix="scraper",
    ) as pool:
        futures = {
            pool.submit(process_portal, portal): portal[0]
            for portal in organizations
        }
        for future in concurrent.futures.as_completed(futures):
            name = futures[future]
            try:
                future.result()
                log.info("[%s] Worker finished.", name)
            except Exception as exc:
                log.error("[%s] Worker raised unexpected exception: %s", name, exc)
                with _error_lock:
                    error_data[name] = {
                        "Status": "Not Successfully Run",
                        "Total Tenders Scraped": 0,
                        "Error": str(exc),
                    }
    log.info("All portals complete.")


# =======================
# MAIN PIPELINE
# =======================
def run_scraping():
    purge_old_logs()
    delete_folder(TEMP_DIR)
    create_folder_if_not_exists(TEMP_DIR)

    run_all_portals_threaded()

    create_folder_if_not_exists(DUMP_LOCATION)
    for fname in os.listdir(TEMP_DIR):
        if not fname.endswith(".xlsx"):
            continue
        src = os.path.join(TEMP_DIR, fname)
        try:
            df = pd.read_excel(src)
            df.replace("NA", 0.00, inplace=True)
            df.to_excel(os.path.join(OUTPUT_DIR, fname), index=False)
            if DUMP_LOCATION and DUMP_LOCATION != OUTPUT_DIR:
                create_folder_if_not_exists(DUMP_LOCATION)
                df.to_excel(os.path.join(DUMP_LOCATION, fname), index=False)
        except Exception as exc:
            log.warning("Could not process %s: %s", fname, exc)

    log.info("Individual files saved to %s", OUTPUT_DIR)
    merged_path = merge_xlsx_files(OUTPUT_DIR, OUTPUT_DIR)
    send_mail(merged_file=merged_path, attach_log=True)


# =======================
# ENTRY POINT
# =======================
def main() -> int:
    delete_xlsx_files(PROGRAM_FILES_DIR)
    start = time.time()
    log.info("=== State Tender Scraper started ===")
    log.info("BASE_DIR   : %s", BASE_DIR)
    log.info("OUTPUT_DIR : %s", OUTPUT_DIR)
    log.info("LOG_DIR    : %s", LOG_DIR)
    log.info("Log file   : %s", _LOG_FILENAME)
    log.info("Headless   : %s", BROWSER_HEADLESS)
    log.info("Portals    : %d", len(organizations))
    log.info("Workers    : %d", MAX_WORKERS)

    exit_code = 0
    try:
        run_scraping()
    except Exception as exc:
        log.exception("Fatal error: %s", exc)
        exit_code = 1
    finally:
        h, rem = divmod(int(time.time() - start), 3600)
        m, s   = divmod(rem, 60)
        log.info("=== Finished in %dh %dm %ds  exit=%d ===", h, m, s, exit_code)
        for handler in logging.getLogger().handlers:
            handler.flush()
            handler.close()
        delete_folder(CAP_DIR)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())