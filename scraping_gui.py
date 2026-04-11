"""
||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||
Author  : Anupam Manna
Email   : am7059141480@gmail.com
Mobile  : +91 7059141480
Updated : 2025
Description: Windows 11 GUI for State Tender Scraper.
  • customtkinter — Windows 11 Fluent / Mica aesthetic
  • Scraping engine runs in a background thread — UI never freezes
  • Live log panel: auto-scroll toggle, level filter, export, 1000-line cap
  • Per-portal progress cards with enabled/disabled checkboxes
  • Settings panel: 4 tabs covering every configurable engine constant
      Tab 1 — Email & SMTP (server, port, subject, credentials, test button)
      Tab 2 — Scraping (workers, CAPTCHA retries, timeouts, page-load wait,
               pass-2 threshold, headless toggle, show-browser quick toggle)
      Tab 3 — Output (output folder, dump folder, filename prefix,
               delete-individuals switch, log retention days)
      Tab 4 — Portals (enable/disable each portal, add/remove entries)
  • Stop button: cancels queued futures + sets _stop_requested flag
  • Dark / Light mode toggle
  • Windows toast notification on finish
  • Close confirmation when a scrape is in progress
||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||||
"""

# =======================
# BOOTSTRAP  (must be first)
# =======================
import multiprocessing
multiprocessing.freeze_support()

import concurrent.futures
import ctypes
import datetime
import json
import logging
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, filedialog

import customtkinter as ctk

import scraping as engine
from Program_Files.validation_utils import is_valid_portal_name, is_valid_portal_url


# =======================
# WINDOWS 11 DPI
# =======================
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Fonts ─────────────────────────────────────────────────────────────
FONT_TITLE  = ("Segoe UI Variable Display", 22, "bold")
FONT_HEADER = ("Segoe UI Variable Text",    13, "bold")
FONT_BODY   = ("Segoe UI Variable Text",    12)
FONT_SMALL  = ("Segoe UI Variable Small",   11)
FONT_MONO   = ("Cascadia Code",             11)

# ── Colours ───────────────────────────────────────────────────────────
ACCENT       = "#0078D4"
ACCENT_HOVER = "#106EBE"
SUCCESS      = "#0E7A0D"
WARNING      = "#CA5010"
ERROR_CLR    = "#C42B1C"


# =======================
# GUI LOG HANDLER
# =======================
class _QueueHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord):
        self.q.put(self.format(record))


# =======================
# PORTAL STATUS CARD
# =======================
class PortalCard(ctk.CTkFrame):
    _STATUS_COLORS = {
        "idle":      ("#888888", "#555555"),
        "running":   (ACCENT,    "#1557A0"),
        "done":      (SUCCESS,   "#0A5C0A"),
        "error":     (ERROR_CLR, "#8B1C13"),
        "no data":   (WARNING,   "#8B3800"),
        "cancelled": ("#888888", "#3A3A3A"),
        "skipped":   ("#666666", "#2A2A2A"),
    }

    def __init__(self, parent, name: str, enabled: bool = True, **kwargs):
        super().__init__(parent, corner_radius=8, **kwargs)
        self.name    = name
        self.enabled = tk.BooleanVar(value=enabled)
        self.grid_columnconfigure(2, weight=1)

        self._cb = ctk.CTkCheckBox(
            self, text="", variable=self.enabled,
            width=20, checkbox_width=16, checkbox_height=16,
            command=self._on_toggle)
        self._cb.grid(row=0, column=0, padx=(8, 4), pady=6)

        self._dot = ctk.CTkLabel(self, text="●", font=("Segoe UI", 13),
                                  text_color="#888888", width=16)
        self._dot.grid(row=0, column=1, padx=(0, 4))

        ctk.CTkLabel(self, text=name, font=FONT_BODY, anchor="w").grid(
            row=0, column=2, sticky="w", padx=2)

        self._count_lbl = ctk.CTkLabel(self, text="—", font=FONT_SMALL,
                                        text_color="#666666")
        self._count_lbl.grid(row=0, column=3, padx=6)

        self._badge = ctk.CTkLabel(self, text="idle", font=FONT_SMALL,
                                    fg_color="#555555", corner_radius=6,
                                    text_color="#CCCCCC", width=72)
        self._badge.grid(row=0, column=4, padx=(0, 8))

    def _on_toggle(self):
        alpha = 1.0 if self.enabled.get() else 0.45
        self._dot.configure(text_color=f"#{int(0x88*alpha):02X}"
                            f"{int(0x88*alpha):02X}{int(0x88*alpha):02X}")

    def set_status(self, status: str, count: int = 0):
        light, dark = self._STATUS_COLORS.get(status, self._STATUS_COLORS["idle"])

        def _update():
            self._dot.configure(text_color=light)
            self._badge.configure(text=status, fg_color=dark, text_color=light)
            if count:
                self._count_lbl.configure(
                    text=f"{count} tender{'s' if count != 1 else ''}",
                    text_color="#AAAAAA")
        self.after(0, _update)


# =======================
# HELPERS — labelled row and section separator
# =======================
def _lbl_row(parent, row, label, widget_factory, **grid_kw):
    """Place a right-aligned label + widget in a 2-column grid row."""
    ctk.CTkLabel(parent, text=label, font=FONT_BODY, anchor="e").grid(
        row=row, column=0, padx=(16, 8), pady=6, sticky="e")
    w = widget_factory(parent)
    w.grid(row=row, column=1, padx=(0, 16), pady=6, sticky="ew", **grid_kw)
    return w


def _section(parent, row, text):
    ctk.CTkLabel(parent, text=text, font=FONT_HEADER,
                  text_color="#888888").grid(
        row=row, column=0, columnspan=2, padx=16, pady=(14, 2), sticky="w")


# =======================
# SETTINGS PANEL  (tabbed)
# =======================
class SettingsPanel(ctk.CTkToplevel):

    def __init__(self, parent: "App"):
        super().__init__(parent)
        self._app = parent
        self.title("Settings")
        self.geometry("640x600")
        self.minsize(580, 520)
        self.grab_set()

        # Load fresh from disk so the panel always shows persisted values
        self._cfg = engine.load_config()

        self.grid_rowconfigure(0, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # ── Tab view ──────────────────────────────────────────────────
        tabs = ctk.CTkTabview(self, corner_radius=10)
        tabs.grid(row=0, column=0, sticky="nsew", padx=12, pady=(12, 0))

        self._t_email    = tabs.add("📧  Email")
        self._t_scraping = tabs.add("⚙  Scraping")
        self._t_output   = tabs.add("📁  Output")
        self._t_criteria = tabs.add("🔍  Criteria")
        self._t_portals  = tabs.add("🌐  Portals")

        for tab in (self._t_email, self._t_scraping,
                    self._t_output, self._t_criteria, self._t_portals):
            tab.grid_columnconfigure(1, weight=1)

        self._build_email_tab()
        self._build_scraping_tab()
        self._build_output_tab()
        self._build_criteria_tab()
        self._build_portals_tab()

        # ── Bottom buttons ────────────────────────────────────────────
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.grid(row=1, column=0, pady=10)
        ctk.CTkButton(btn_row, text="Save all", width=110, font=FONT_BODY,
                       fg_color=ACCENT, hover_color=ACCENT_HOVER,
                       command=self._save_all).pack(side="left", padx=8)
        ctk.CTkButton(btn_row, text="Cancel", width=90, font=FONT_BODY,
                       fg_color="transparent", border_width=1,
                       command=self.destroy).pack(side="left", padx=8)

    # ── Tab 1 — Email ─────────────────────────────────────────────────
    def _build_email_tab(self):
        t = self._t_email
        _section(t, 0, "SMTP server")
        self._smtp_server = _lbl_row(t, 1, "Server",
            lambda p: ctk.CTkEntry(p, placeholder_text="smtp.office365.com"))
        self._smtp_server.insert(0, self._cfg.get("smtp_server", "smtp.office365.com"))

        self._smtp_port_var = tk.StringVar(value=str(self._cfg.get("smtp_port", 587)))
        self._smtp_port = _lbl_row(t, 2, "Port",
            lambda p: ctk.CTkOptionMenu(p, values=["587", "465", "25"],
                                         variable=self._smtp_port_var, width=100))

        _section(t, 3, "Credentials")
        self._sender = _lbl_row(t, 4, "Sender email",
            lambda p: ctk.CTkEntry(p))
        self._sender.insert(0, self._cfg.get("sender_email_id", ""))

        self._password = _lbl_row(t, 5, "Password",
            lambda p: ctk.CTkEntry(p, show="●"))
        self._password.insert(0, self._cfg.get("sender_email_password", ""))

        _section(t, 6, "Message")
        self._subject = _lbl_row(t, 7, "Subject",
            lambda p: ctk.CTkEntry(p))
        self._subject.insert(0, self._cfg.get("email_subject",
                                               "State Tender Scraping Report"))

        self._attach_log_var = tk.BooleanVar(
            value=self._cfg.get("attach_log_to_email", True))
        _lbl_row(t, 8, "Attach log file",
            lambda p: ctk.CTkCheckBox(p, text="", variable=self._attach_log_var,
                                       checkbox_width=18, checkbox_height=18))

        ctk.CTkLabel(t, text="Notify emails (comma-separated)",
                      font=FONT_SMALL, text_color="#888888",
                      anchor="w").grid(row=9, column=0, columnspan=2,
                                       padx=16, pady=(10, 2), sticky="w")
        self._emails_box = ctk.CTkTextbox(t, height=56, font=FONT_MONO)
        self._emails_box.grid(row=10, column=0, columnspan=2,
                               padx=16, pady=(0, 6), sticky="ew")
        self._emails_box.insert("1.0",
            ", ".join(self._cfg.get("notification_emailids", [])))

        test_btn = ctk.CTkButton(t, text="Send test email", width=130,
                                  font=FONT_SMALL, fg_color="transparent",
                                  border_width=1, command=self._send_test_email)
        test_btn.grid(row=11, column=1, padx=(0, 16), pady=4, sticky="e")

    # ── Tab 2 — Scraping ──────────────────────────────────────────────
    def _build_scraping_tab(self):
        t = self._t_scraping
        _section(t, 0, "Parallelism")

        self._workers_var = tk.StringVar(value=str(engine.MAX_WORKERS))
        _lbl_row(t, 1, "Workers (1–8)",
            lambda p: ctk.CTkEntry(p, textvariable=self._workers_var, width=70))

        _section(t, 2, "CAPTCHA")
        self._cap_attempts_var = tk.StringVar(value=str(engine.CAPTCHA_ATTEMPTS))
        _lbl_row(t, 3, "Max attempts (1–15)",
            lambda p: ctk.CTkEntry(p, textvariable=self._cap_attempts_var, width=70))

        _section(t, 4, "Timeouts")
        self._page_timeout_var = tk.StringVar(value=str(engine.PAGE_TIMEOUT_SEC))
        _lbl_row(t, 5, "Page timeout (sec)",
            lambda p: ctk.CTkEntry(p, textvariable=self._page_timeout_var, width=80))

        self._nav_retries_var = tk.StringVar(value=str(engine.NAV_RETRIES))
        _lbl_row(t, 6, "Nav retries (1–10)",
            lambda p: ctk.CTkEntry(p, textvariable=self._nav_retries_var, width=70))

        self._wait_var = tk.StringVar(value=str(engine.PAGE_LOAD_WAIT_SEC))
        _lbl_row(t, 7, "Page-load wait (sec)",
            lambda p: ctk.CTkEntry(p, textvariable=self._wait_var, width=70))

        _section(t, 8, "Browser")
        self._headless_var = tk.BooleanVar(value=engine.BROWSER_HEADLESS)
        _lbl_row(t, 9, "Headless (no window)",
            lambda p: ctk.CTkCheckBox(p, text="", variable=self._headless_var,
                                       checkbox_width=18, checkbox_height=18))

        ctk.CTkLabel(t, text="Changes take effect on next Start press.",
                      font=FONT_SMALL, text_color="#666666").grid(
            row=10, column=0, columnspan=2, padx=16, pady=(10, 0), sticky="w")

    # ── Tab 3 — Output ────────────────────────────────────────────────
    def _build_output_tab(self):
        t = self._t_output
        _section(t, 0, "Folders")

        self._output_dir_var = tk.StringVar(value=engine.OUTPUT_DIR)
        row_out = ctk.CTkFrame(t, fg_color="transparent")
        row_out.grid(row=1, column=0, columnspan=2, padx=16, pady=6, sticky="ew")
        row_out.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row_out, text="Output folder", font=FONT_BODY,
                      anchor="e").grid(row=0, column=0, padx=(0, 8), sticky="e")
        ctk.CTkEntry(row_out, textvariable=self._output_dir_var).grid(
            row=0, column=1, sticky="ew")
        ctk.CTkButton(row_out, text="Browse", width=68, font=FONT_SMALL,
                       fg_color="transparent", border_width=1,
                       command=lambda: self._browse(self._output_dir_var)
                       ).grid(row=0, column=2, padx=(6, 0))

        self._dump_var = tk.StringVar(value=engine.DUMP_LOCATION)
        row_dump = ctk.CTkFrame(t, fg_color="transparent")
        row_dump.grid(row=2, column=0, columnspan=2, padx=16, pady=6, sticky="ew")
        row_dump.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(row_dump, text="Dump folder", font=FONT_BODY,
                      anchor="e").grid(row=0, column=0, padx=(0, 8), sticky="e")
        ctk.CTkEntry(row_dump, textvariable=self._dump_var).grid(
            row=0, column=1, sticky="ew")
        ctk.CTkButton(row_dump, text="Browse", width=68, font=FONT_SMALL,
                       fg_color="transparent", border_width=1,
                       command=lambda: self._browse(self._dump_var)
                       ).grid(row=0, column=2, padx=(6, 0))

        _section(t, 3, "Files")
        self._prefix_var = tk.StringVar(
            value=self._cfg.get("merged_file_prefix", "merged_State"))
        _lbl_row(t, 4, "Merged filename prefix",
            lambda p: ctk.CTkEntry(p, textvariable=self._prefix_var))

        self._del_indiv_var = tk.BooleanVar(
            value=self._cfg.get("delete_individual_after_merge", False))
        _lbl_row(t, 5, "Delete individual files after merge",
            lambda p: ctk.CTkCheckBox(p, text="", variable=self._del_indiv_var,
                                       checkbox_width=18, checkbox_height=18))

        _section(t, 6, "Log retention")
        self._log_days_var = tk.StringVar(
            value=str(self._cfg.get("log_retention_days", 30)))
        _lbl_row(t, 7, "Keep logs for (days, 0=forever)",
            lambda p: ctk.CTkEntry(p, textvariable=self._log_days_var, width=80))

    # ── Tab 4 — Search Criteria ───────────────────────────────────────
    def _build_criteria_tab(self):
        t = self._t_criteria
        t.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(
            t,
            text=(
                "Two search passes run per portal in sequence.\n"
                "Uncheck Enabled to skip a pass.  Saved to search_criteria.json\n"
                "valueParameter: 1=Equal  2=LessThan  3=GreaterThan  4=Between"
            ),
            font=FONT_SMALL, text_color="#888888", justify="left",
        ).grid(row=0, column=0, columnspan=2, padx=16, pady=(10, 6), sticky="w")

        scroll = ctk.CTkScrollableFrame(t, corner_radius=8)
        scroll.grid(row=1, column=0, columnspan=2, sticky="nsew",
                     padx=12, pady=(0, 8))
        scroll.grid_columnconfigure(1, weight=1)

        self._criteria_rows: list[dict] = []
        criteria = engine.load_criteria()
        for idx, c in enumerate(criteria):
            self._add_criterion_card(scroll, idx, c)

    def _add_criterion_card(self, parent, idx: int, c: dict):
        """Render one criterion as a fully-populated card."""

        # ── Option maps (matching portal HTML exactly) ─────────────────
        TENDER_TYPE_OPTS = ["0 — Select", "1 — Open Tender", "2 — Limited Tender"]
        VALUE_CRIT_OPTS  = ["0 — Select", "1 — EMD", "2 — Tender Fee",
                             "3 — Processing Fee", "4 — ECV"]
        VALUE_PARAM_OPTS = ["0 — Select", "1 — Equal", "2 — LessThan",
                             "3 — GreaterThan", "4 — Between"]
        TENDER_CAT_OPTS  = ["0 — All", "1 — Goods", "2 — Services", "3 — Works"]
        PAYMENT_OPTS     = ["0 — All", "1 — Offline", "2 — Online",
                             "3 — Both(Online/Offline)", "4 — Not Applicable"]
        DATE_CRIT_OPTS   = ["0 — None", "1 — Published Date",
                             "2 — Doc Download Start", "3 — Doc Download End",
                             "4 — Bid Submit Start",   "5 — Bid Submit End"]
        CONTRACT_OPTS    = [
            "0 — All", "1 — Buy", "2 — Empanelment", "3 — EOI",
            "4 — EPC Contract", "5 — Fixed-rate", "6 — Item Rate",
            "7 — Lump-sum", "8 — Multi-stage", "9 — Percentage",
            "10 — Piece-work", "11 — PPP-BoT-HAM", "12 — PPP-BoT-ToT",
            "13 — PPP-DBFOT", "14 — QCBS", "15 — Sale", "16 — Supply",
            "17 — Tender cum Auction", "18 — Turn-key", "19 — Works",
        ]
        PRODUCT_CAT_OPTS = [
            "0 — All", "1 — Access Control System", "2 — Advertisement Services",
            "3 — Agricultural or Forestry", "4 — Allotment of Space",
            "5 — AMC/ Maintenance Contracts", "6 — Architecture/Interior Design",
            "7 — Audio-Visual Equipment", "8 — Chemicals/Minerals",
            "9 — Civil Construction Goods", "10 — Civil Works",
            "11 — Civil Works - Bridges", "12 — Civil Works - Buildings",
            "13 — Civil Works - Highways", "14 — Civil Works - Others",
            "15 — Civil Works - Roads", "16 — Civil Works - Water Works",
            "17 — Coal Works", "18 — Computer- Data Processing",
            "19 — Computer- H/W", "20 — Computer- S/W",
            "21 — Construction Works", "22 — Consultancy",
            "23 — Consumables (Hospital / Lab)",
            "24 — Consumables - Paper/Printing", "25 — Consumables- Raw materials",
            "26 — Drilling Works", "27 — Drugs and Pharmaceutical Products",
            "28 — Edible Oils", "29 — Electrical Goods/Equipment",
            "30 — Electrical Works", "31 — Electronic Components",
            "32 — Electronics Equipment", "33 — Equipments (Hospital / Lab)",
            "34 — Facility Management Services", "35 — Financial and Insurance",
            "36 — Food Products", "37 — Furniture/ Fixture",
            "38 — Government Stock/Security", "39 — Hiring of Vehicles",
            "40 — Hotel/ Catering", "41 — Housekeeping/ Cleaning",
            "42 — Information Technology", "43 — Info. Tech. Services",
            "44 — Job Works", "45 — Lab Chemistry Reagents",
            "46 — Laboratory and scientific equipment", "47 — Land/Building",
            "48 — Machineries/ Mechanical Engg Items",
            "49 — Machinery and Machining Tools", "50 — Manpower Supply",
            "51 — Marine Services", "52 — Marine Works",
            "53 — Mechanical Tools and Equipment", "54 — Medical Equipments/Waste",
            "55 — Medicines", "56 — Metal Fabrication", "57 — Metals",
            "58 — Metals - Ferrous", "59 — Miscellaneous Goods",
            "60 — Miscellaneous Services", "61 — Miscellaneous Works",
            "62 — Network /Communication Equipments",
            "63 — Non Consumables (Hospital / Lab)", "64 — OFC Laying Works",
            "65 — Oil/Gas", "66 — Paint / Enamel Works",
            "67 — Pipe Laying Works", "68 — Pipes and Pipe related activities",
            "69 — Plant Protection Input/Equipment Works",
            "70 — Power/Energy Projects/Products", "71 — Publishing/Printing",
            "72 — Pumps/Motors", "73 — Renting out / Licensing out",
            "74 — Repair and Maintenance Services", "75 — Shipping Services",
            "76 — Shipping/ Transportation/ Vehicle", "77 — Stone Works",
            "78 — Supply, Erection and Commissioning",
            "79 — Support/Maintenance Service", "80 — Surveillance Equipments",
            "81 — Survey and Investigation services",
            "82 — Water Equipments/ Meter/ Drilling/ Boring",
        ]

        def _opt(opts: list[str], val: int) -> str:
            """Return the option string matching a numeric value."""
            prefix = f"{val} —"
            for o in opts:
                if o.startswith(prefix):
                    return o
            return opts[0]

        # ── Card container ─────────────────────────────────────────────
        card = ctk.CTkFrame(parent, corner_radius=10, border_width=1)
        card.pack(fill="x", padx=4, pady=8)
        card.grid_columnconfigure(1, weight=1)
        card.grid_columnconfigure(3, weight=1)

        def _row_lbl(r, c, text):
            ctk.CTkLabel(card, text=text, font=FONT_SMALL,
                          text_color="#888888", anchor="e").grid(
                row=r, column=c, padx=(12, 6), pady=3, sticky="e")

        def _row_entry(r, c, var, w=120):
            e = ctk.CTkEntry(card, textvariable=var, font=FONT_MONO, width=w)
            e.grid(row=r, column=c, padx=(0, 12), pady=3, sticky="w")
            return e

        def _row_option(r, c, var, opts, w=220):
            m = ctk.CTkOptionMenu(card, variable=var, values=opts,
                                   font=FONT_SMALL, width=w)
            m.grid(row=r, column=c, padx=(0, 12), pady=3, sticky="w")
            return m

        # ── Header row: Pass N label + Enabled ────────────────────────
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.grid(row=0, column=0, columnspan=4, sticky="ew",
                  padx=12, pady=(10, 4))
        hdr.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(hdr, text=f"Pass {idx + 1}",
                      font=FONT_HEADER).grid(row=0, column=0, sticky="w")
        enabled_var = tk.BooleanVar(value=c.get("enabled", True))
        ctk.CTkCheckBox(hdr, text="Enabled", variable=enabled_var,
                         font=FONT_SMALL, checkbox_width=16,
                         checkbox_height=16).grid(row=0, column=2, sticky="e")

        # ── Label (editable) ──────────────────────────────────────────
        label_var = tk.StringVar(value=c.get("label", f"Pass {idx + 1}"))
        _row_lbl(1, 0, "Label")
        ctk.CTkEntry(card, textvariable=label_var, font=FONT_SMALL).grid(
            row=1, column=1, columnspan=3, padx=(0, 12), pady=3, sticky="ew")

        # ── Section: Tender type ───────────────────────────────────────
        ctk.CTkLabel(card, text="─── Tender ─────────────────",
                      font=FONT_SMALL, text_color="#555555").grid(
            row=2, column=0, columnspan=4, padx=12, pady=(8, 2), sticky="w")

        tt_var = tk.StringVar(value=_opt(TENDER_TYPE_OPTS, c.get("tender_type", 1)))
        _row_lbl(3, 0, "Tender type")
        _row_option(3, 1, tt_var, TENDER_TYPE_OPTS, w=200)

        # ── Section: Value filter ──────────────────────────────────────
        ctk.CTkLabel(card, text="─── Value filter ────────────",
                      font=FONT_SMALL, text_color="#555555").grid(
            row=4, column=0, columnspan=4, padx=12, pady=(8, 2), sticky="w")

        vc_var  = tk.StringVar(value=_opt(VALUE_CRIT_OPTS,  c.get("value_criteria", 4)))
        vp_var  = tk.StringVar(value=_opt(VALUE_PARAM_OPTS, c.get("value_param",    3)))
        fv_var  = tk.StringVar(value=str(c.get("from_value", 0)))
        tv_var  = tk.StringVar(value=str(c.get("to_value",   0)))

        _row_lbl(5, 0, "Value criteria")
        _row_option(5, 1, vc_var, VALUE_CRIT_OPTS, w=180)
        _row_lbl(5, 2, "Comparison")
        _row_option(5, 3, vp_var, VALUE_PARAM_OPTS, w=180)

        _row_lbl(6, 0, "From value (₹)")
        _row_entry(6, 1, fv_var, w=140)
        _row_lbl(6, 2, "To value (₹)  [Between only]")
        _row_entry(6, 3, tv_var, w=140)

        # ── Section: Category filters ──────────────────────────────────
        ctk.CTkLabel(card, text="─── Category filters ────────",
                      font=FONT_SMALL, text_color="#555555").grid(
            row=7, column=0, columnspan=4, padx=12, pady=(8, 2), sticky="w")

        tc_var = tk.StringVar(value=_opt(TENDER_CAT_OPTS,  c.get("tender_category",  0)))
        pc_var = tk.StringVar(value=_opt(PRODUCT_CAT_OPTS, c.get("product_category", 0)))
        fc_var = tk.StringVar(value=_opt(CONTRACT_OPTS,    c.get("form_contract",    0)))
        pm_var = tk.StringVar(value=_opt(PAYMENT_OPTS,     c.get("payment_mode",     0)))

        _row_lbl(8, 0, "Tender category")
        _row_option(8, 1, tc_var, TENDER_CAT_OPTS, w=160)
        _row_lbl(8, 2, "Payment mode")
        _row_option(8, 3, pm_var, PAYMENT_OPTS, w=220)

        _row_lbl(9, 0, "Form of contract")
        _row_option(9, 1, fc_var, CONTRACT_OPTS, w=220)

        _row_lbl(10, 0, "Product category")
        _row_option(10, 1, pc_var, PRODUCT_CAT_OPTS, w=280)

        # ── Section: Date filter ───────────────────────────────────────
        ctk.CTkLabel(card, text="─── Date filter ─────────────",
                      font=FONT_SMALL, text_color="#555555").grid(
            row=11, column=0, columnspan=4, padx=12, pady=(8, 2), sticky="w")

        dc_var  = tk.StringVar(value=_opt(DATE_CRIT_OPTS, c.get("date_criteria", 0)))
        fd_var  = tk.StringVar(value=c.get("from_date", ""))
        td_var  = tk.StringVar(value=c.get("to_date",   ""))

        _row_lbl(12, 0, "Date criteria")
        _row_option(12, 1, dc_var, DATE_CRIT_OPTS, w=220)

        _row_lbl(13, 0, "From date (dd/MM/yyyy)")
        _row_entry(13, 1, fd_var, w=130)
        _row_lbl(13, 2, "To date (dd/MM/yyyy)")
        _row_entry(13, 3, td_var, w=130)

        # ── Section: Free-text filters ────────────────────────────────
        ctk.CTkLabel(card, text="─── Free-text filters ───────",
                      font=FONT_SMALL, text_color="#555555").grid(
            row=14, column=0, columnspan=4, padx=12, pady=(8, 2), sticky="w")

        pin_var = tk.StringVar(value=c.get("pin_code",        ""))
        wit_var = tk.StringVar(value=c.get("work_item_title", ""))
        tid_var = tk.StringVar(value=c.get("tender_id",       ""))
        trn_var = tk.StringVar(value=c.get("tender_ref_no",   ""))

        _row_lbl(15, 0, "PIN code")
        _row_entry(15, 1, pin_var, w=120)
        _row_lbl(15, 2, "Work/Item title")
        _row_entry(15, 3, wit_var, w=200)

        _row_lbl(16, 0, "Tender ID")
        _row_entry(16, 1, tid_var, w=160)
        _row_lbl(16, 2, "Tender Ref No.")
        _row_entry(16, 3, trn_var, w=160)

        # ── Section: Checkboxes ───────────────────────────────────────
        ctk.CTkLabel(card, text="─── Selection criteria ──────",
                      font=FONT_SMALL, text_color="#555555").grid(
            row=17, column=0, columnspan=4, padx=12, pady=(8, 2), sticky="w")

        chk_frame = ctk.CTkFrame(card, fg_color="transparent")
        chk_frame.grid(row=18, column=0, columnspan=4, padx=12,
                        pady=(2, 12), sticky="w")

        two_stage_var = tk.BooleanVar(value=c.get("two_stage", False))
        nda_var       = tk.BooleanVar(value=c.get("nda",       False))
        pref_var      = tk.BooleanVar(value=c.get("pref_bid",  False))
        gte_var       = tk.BooleanVar(value=c.get("gte",       False))
        ite_var       = tk.BooleanVar(value=c.get("ite",       False))
        tfe_var       = tk.BooleanVar(value=c.get("tfe",       False))
        efe_var       = tk.BooleanVar(value=c.get("efe",       False))

        chk_defs = [
            (two_stage_var, "Two Stage Bidding"),
            (nda_var,       "NDA Tenders"),
            (pref_var,      "Preferential Bidding"),
            (gte_var,       "GTE"),
            (ite_var,       "ITE / TPS"),
            (tfe_var,       "Tender Fee Exemption"),
            (efe_var,       "EMD Exemption"),
        ]
        for i, (var, lbl) in enumerate(chk_defs):
            ctk.CTkCheckBox(chk_frame, text=lbl, variable=var,
                             font=FONT_SMALL, checkbox_width=15,
                             checkbox_height=15).grid(
                row=i // 4, column=i % 4, padx=10, pady=2, sticky="w")

        # ── Store all vars for _save_all ──────────────────────────────
        self._criteria_rows.append({
            "label":           label_var,
            "enabled":         enabled_var,
            "tender_type":     tt_var,
            "value_criteria":  vc_var,
            "value_param":     vp_var,
            "from_value":      fv_var,
            "to_value":        tv_var,
            "tender_category": tc_var,
            "product_category": pc_var,
            "form_contract":   fc_var,
            "payment_mode":    pm_var,
            "date_criteria":   dc_var,
            "from_date":       fd_var,
            "to_date":         td_var,
            "pin_code":        pin_var,
            "work_item_title": wit_var,
            "tender_id":       tid_var,
            "tender_ref_no":   trn_var,
            "two_stage":       two_stage_var,
            "nda":             nda_var,
            "pref_bid":        pref_var,
            "gte":             gte_var,
            "ite":             ite_var,
            "tfe":             tfe_var,
            "efe":             efe_var,
        })

    # ── Tab 5 — Portals ───────────────────────────────────────────────
    def _build_portals_tab(self):
        t = self._t_portals
        t.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(t, text="Check/uncheck to include portals in next run.",
                      font=FONT_SMALL, text_color="#888888").grid(
            row=0, column=0, columnspan=2, padx=16, pady=(8, 4), sticky="w")

        scroll = ctk.CTkScrollableFrame(t, corner_radius=8)
        scroll.grid(row=1, column=0, columnspan=2, sticky="nsew",
                     padx=12, pady=(0, 8))
        scroll.grid_columnconfigure((0, 1, 2), weight=1)

        self._portal_rows: list[dict] = []

        # Headers
        for col, txt in enumerate(["Name", "URL", "Enabled"]):
            ctk.CTkLabel(scroll, text=txt, font=FONT_SMALL,
                          text_color="#888888").grid(
                row=0, column=col, padx=8, pady=(4, 2), sticky="w")

        # One row per portal from engine.organizations
        # Enabled state is determined by whether the name is NOT commented
        # out in Organization_list.txt — we re-read the raw file to preserve order
        raw_lines = []
        try:
            with open(engine.ORG_FILE, "r", encoding="utf-8") as f:
                raw_lines = f.readlines()
        except Exception:
            pass

        self._all_portal_lines = raw_lines   # preserved for save

        r = 1
        self._portal_rows.clear()
        for line in raw_lines:
            stripped = line.strip()
            if not stripped:
                continue
            is_comment = stripped.startswith("#")
            parts = stripped.lstrip("#").split(": ", 1)
            if len(parts) != 2:
                continue
            name_val = tk.StringVar(value=parts[0].strip())
            url_val  = tk.StringVar(value=parts[1].strip())
            enabled  = tk.BooleanVar(value=not is_comment)

            ctk.CTkEntry(scroll, textvariable=name_val, width=110,
                          font=FONT_SMALL).grid(row=r, column=0, padx=6, pady=2, sticky="ew")
            ctk.CTkEntry(scroll, textvariable=url_val,
                          font=FONT_SMALL).grid(row=r, column=1, padx=6, pady=2, sticky="ew")
            ctk.CTkCheckBox(scroll, text="", variable=enabled,
                             checkbox_width=16, checkbox_height=16).grid(
                row=r, column=2, padx=6, pady=2)

            self._portal_rows.append({"name": name_val, "url": url_val,
                                       "enabled": enabled})
            r += 1

        # Add / Remove buttons
        btn_frame = ctk.CTkFrame(t, fg_color="transparent")
        btn_frame.grid(row=2, column=0, columnspan=2, pady=(0, 6))
        ctk.CTkButton(btn_frame, text="+ Add portal", width=110,
                       font=FONT_SMALL, fg_color="transparent", border_width=1,
                       command=lambda: self._add_portal_row(scroll)
                       ).pack(side="left", padx=6)

    def _add_portal_row(self, scroll):
        r = len(self._portal_rows) + 1
        name_val = tk.StringVar(value="NewPortal")
        url_val  = tk.StringVar(value="https://")
        enabled  = tk.BooleanVar(value=True)
        ctk.CTkEntry(scroll, textvariable=name_val, width=110,
                      font=FONT_SMALL).grid(row=r, column=0, padx=6, pady=2, sticky="ew")
        ctk.CTkEntry(scroll, textvariable=url_val,
                      font=FONT_SMALL).grid(row=r, column=1, padx=6, pady=2, sticky="ew")
        ctk.CTkCheckBox(scroll, text="", variable=enabled,
                         checkbox_width=16, checkbox_height=16).grid(
            row=r, column=2, padx=6, pady=2)
        self._portal_rows.append({"name": name_val, "url": url_val,
                                   "enabled": enabled})

    # ── Save all ──────────────────────────────────────────────────────
    def _save_all(self):
        """
        Collect every setting from all four tabs, write them ALL into
        Configration.json, then call engine._apply_config() so every
        engine global is refreshed from the file immediately.
        No setting is held only in memory — restart-safe by design.
        """
        cfg = engine.load_config()   # start from current file state

        # ── Tab 1: Email ──────────────────────────────────────────────
        cfg["smtp_server"]           = self._smtp_server.get().strip()
        cfg["smtp_port"]             = int(self._smtp_port_var.get())
        cfg["sender_email_id"]       = self._sender.get().strip()
        cfg["sender_email_password"] = self._password.get().strip()
        cfg["email_subject"]         = self._subject.get().strip()
        cfg["attach_log_to_email"]   = self._attach_log_var.get()
        cfg["notification_emailids"] = [
            e.strip() for e in
            self._emails_box.get("1.0", "end").strip().split(",")
            if e.strip()
        ]

        # ── Tab 2: Scraping ───────────────────────────────────────────
        def _int(var, lo, hi, default):
            try:
                return max(lo, min(hi, int(var.get())))
            except ValueError:
                return default

        cfg["max_workers"]         = _int(self._workers_var,     1,  8,   4)
        cfg["captcha_attempts"]    = _int(self._cap_attempts_var, 1,  15,  5)
        cfg["page_timeout_sec"]    = _int(self._page_timeout_var, 30, 600, 240)
        cfg["nav_retries"]         = _int(self._nav_retries_var,  1,  10,  3)
        cfg["page_load_wait_sec"]  = _int(self._wait_var,         1,  30,  5)
        cfg["browser"]             = "0" if self._headless_var.get() else "1"

        # ── Tab 4: Search Criteria → search_criteria.json ─────────────
        def _opt_int(var: tk.StringVar) -> int:
            """Extract leading integer from an option string like '3 — GreaterThan'."""
            try:
                return int(var.get().split(" —")[0].strip())
            except (ValueError, AttributeError):
                return 0

        new_criteria = []
        for row in self._criteria_rows:
            try:
                fv = int(row["from_value"].get())
            except ValueError:
                fv = 0
            try:
                tv = int(row["to_value"].get())
            except ValueError:
                tv = 0
            new_criteria.append({
                "label":            row["label"].get().strip(),
                "enabled":          row["enabled"].get(),
                "tender_type":      _opt_int(row["tender_type"]),
                "value_criteria":   _opt_int(row["value_criteria"]),
                "value_param":      _opt_int(row["value_param"]),
                "from_value":       fv,
                "to_value":         tv,
                "tender_category":  _opt_int(row["tender_category"]),
                "product_category": _opt_int(row["product_category"]),
                "form_contract":    _opt_int(row["form_contract"]),
                "payment_mode":     _opt_int(row["payment_mode"]),
                "date_criteria":    _opt_int(row["date_criteria"]),
                "from_date":        row["from_date"].get().strip(),
                "to_date":          row["to_date"].get().strip(),
                "pin_code":         row["pin_code"].get().strip(),
                "work_item_title":  row["work_item_title"].get().strip(),
                "tender_id":        row["tender_id"].get().strip(),
                "tender_ref_no":    row["tender_ref_no"].get().strip(),
                "two_stage":        row["two_stage"].get(),
                "nda":              row["nda"].get(),
                "pref_bid":         row["pref_bid"].get(),
                "gte":              row["gte"].get(),
                "ite":              row["ite"].get(),
                "tfe":              row["tfe"].get(),
                "efe":              row["efe"].get(),
            })
        engine.save_criteria(new_criteria)
        engine.search_criteria = new_criteria

        # ── Tab 3: Output ─────────────────────────────────────────────
        cfg["output_dir"]                    = self._output_dir_var.get().strip()
        cfg["dump_location"]                 = self._dump_var.get().strip()
        cfg["merged_file_prefix"]            = self._prefix_var.get().strip() or "merged_State"
        cfg["delete_individual_after_merge"] = self._del_indiv_var.get()
        cfg["log_retention_days"]            = _int(self._log_days_var, 0, 9999, 30)

        # ── Tab 4: Portals → Organization_list.txt ────────────────────
        lines_out = []
        enabled_lines = 0
        invalid_portals = []
        for row in self._portal_rows:
            name    = row["name"].get().strip()
            url     = row["url"].get().strip()
            enabled = row["enabled"].get()
            if not name or not url:
                continue
            if not is_valid_portal_name(name):
                invalid_portals.append(
                    f"• {name or '<empty>'}: invalid portal name"
                )
                continue
            if not is_valid_portal_url(url):
                invalid_portals.append(
                    f"• {name}: invalid URL '{url}'"
                )
                continue
            lines_out.append(f"{'# ' if not enabled else ''}{name}: {url}\n")
            if enabled:
                enabled_lines += 1

        if invalid_portals:
            messagebox.showerror(
                "Portal validation error",
                "Please fix invalid portal entries before saving:\n\n"
                + "\n".join(invalid_portals),
                parent=self,
            )
            return

        if enabled_lines == 0:
            messagebox.showerror(
                "Portal validation error",
                "At least one portal must be enabled before saving.",
                parent=self,
            )
            return

        try:
            with open(engine.ORG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines_out)
            engine.organizations.clear()
            for line in lines_out:
                s = line.strip()
                if s and not s.startswith("#"):
                    parts = s.split(": ", 1)
                    if len(parts) == 2:
                        engine.organizations.append((parts[0], parts[1]))
            self._app._populate_portal_cards()
            self._app._stat_portals.configure(text=str(len(engine.organizations)))
        except Exception as exc:
            messagebox.showerror("Portal save error", str(exc), parent=self)
            return

        # ── Write JSON then reload ALL engine globals in one call ─────
        engine.save_config(cfg)
        engine._apply_config(cfg)

        # Sync UI widgets that reflect newly saved values
        self._app._stat_workers.configure(text=str(engine.MAX_WORKERS))
        self._app._headless_var_quick.set(engine.BROWSER_HEADLESS)

        messagebox.showinfo(
            "Saved", "All settings saved to Configration.json.", parent=self)
        self.destroy()

    # ── Helpers ───────────────────────────────────────────────────────
    def _browse(self, var: tk.StringVar):
        folder = filedialog.askdirectory(title="Select folder")
        if folder:
            var.set(folder)

    def _send_test_email(self):
        """Send a quick test email using current (unsaved) SMTP settings."""
        import smtplib
        from email.mime.text import MIMEText as _MIMEText
        server   = self._smtp_server.get().strip() or engine.SMTP_SERVER
        port     = int(self._smtp_port_var.get())
        sender   = self._sender.get().strip()   or engine.SENDER_EMAIL
        password = self._password.get().strip() or engine.SENDER_PASS
        subject  = self._subject.get().strip()  or engine.EMAIL_SUBJECT
        emails   = [e.strip() for e in
                    self._emails_box.get("1.0", "end").strip().split(",")
                    if e.strip()] or engine.NOTIFY_EMAILS
        try:
            msg = MIMEMultipart_import()
            msg["From"]    = sender
            msg["To"]      = ", ".join(emails)
            msg["Subject"] = f"[TEST] {subject}"
            msg.attach(_MIMEText(
                "This is a test email from State Tender Scraper.\n"
                "If you received this, your SMTP settings are correct.", "plain"))
            srv = smtplib.SMTP(server, port)
            srv.starttls()
            srv.login(sender, password)
            srv.sendmail(sender, emails, msg.as_string())
            srv.quit()
            messagebox.showinfo("Test email", "Test email sent successfully!",
                                parent=self)
        except Exception as exc:
            messagebox.showerror("Test email failed", str(exc), parent=self)


# Helper import alias so MIMEMultipart is available inside the method
from email.mime.multipart import MIMEMultipart as MIMEMultipart_import


# =======================
# MAIN APPLICATION WINDOW
# =======================
class App(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("State Tender Scraper")
        self.geometry("1080x740")
        self.minsize(880, 600)
        self._set_win11_title_bar()

        # State
        self._running         = False
        self._start_time      = 0.0
        self._log_queue       = queue.Queue()
        self._portal_cards:   dict[str, PortalCard] = {}
        self._total_scraped   = 0
        self._log_line_count  = 0
        self._engine_patched  = False
        self._executor        = None
        self._stopped_by_user = False

        self._setup_log_handler()
        self._build_ui()
        self._populate_portal_cards()
        self._poll_logs()
        self._poll_timer()

    # ── Windows 11 Mica title bar ─────────────────────────────────────
    def _set_win11_title_bar(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 38, ctypes.byref(ctypes.c_int(2)), ctypes.sizeof(ctypes.c_int))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(ctypes.c_int(1)), ctypes.sizeof(ctypes.c_int))
        except Exception:
            pass

    # ── Log handler ───────────────────────────────────────────────────
    def _setup_log_handler(self):
        handler = _QueueHandler(self._log_queue)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  [%(threadName)s]  %(message)s",
            datefmt="%H:%M:%S"))
        logging.getLogger().addHandler(handler)

    # ── UI construction ───────────────────────────────────────────────
    def _build_ui(self):
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Top bar
        top = ctk.CTkFrame(self, height=56, corner_radius=0,
                            fg_color=("#E5E5E5", "#1A1A1A"))
        top.grid(row=0, column=0, sticky="ew")
        top.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(top, text="State Tender Scraper",
                      font=FONT_TITLE).grid(row=0, column=0, padx=20, pady=12, sticky="w")

        self._headless_var_quick = tk.BooleanVar(value=engine.BROWSER_HEADLESS)
        ctk.CTkCheckBox(top, text="Headless", variable=self._headless_var_quick,
                         font=FONT_SMALL, checkbox_width=16, checkbox_height=16,
                         command=self._quick_headless_toggle).grid(
            row=0, column=3, padx=8, pady=12)

        self._theme_btn = ctk.CTkButton(
            top, text="☀  Light", width=88, height=30, font=FONT_SMALL,
            fg_color="transparent", border_width=1, command=self._toggle_theme)
        self._theme_btn.grid(row=0, column=4, padx=6, pady=12)

        ctk.CTkButton(top, text="⚙  Settings", width=100, height=30,
                       font=FONT_SMALL, fg_color="transparent", border_width=1,
                       command=lambda: SettingsPanel(self)).grid(
            row=0, column=5, padx=6, pady=12)

        ctk.CTkButton(top, text="📁  Output", width=88, height=30,
                       font=FONT_SMALL, fg_color="transparent", border_width=1,
                       command=self._open_output).grid(
            row=0, column=6, padx=(6, 16), pady=12)

        # Content area
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.grid(row=1, column=0, sticky="nsew", padx=12, pady=(6, 0))
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=0)
        content.grid_columnconfigure(1, weight=1)

        # ── Left panel ────────────────────────────────────────────────
        left = ctk.CTkFrame(content, width=340, corner_radius=12)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 8))
        left.grid_rowconfigure(2, weight=1)
        left.grid_columnconfigure(0, weight=1)
        left.grid_propagate(False)

        stats = ctk.CTkFrame(left, fg_color="transparent")
        stats.grid(row=0, column=0, sticky="ew", padx=12, pady=(14, 4))
        stats.grid_columnconfigure((0, 1, 2, 3), weight=1)

        self._stat_portals = self._make_stat(stats, "Portals",
                                              str(len(engine.organizations)), 0)
        self._stat_scraped = self._make_stat(stats, "Scraped", "0", 1)
        self._stat_elapsed = self._make_stat(stats, "Elapsed", "00:00", 2)
        self._stat_workers = self._make_stat(stats, "Workers",
                                              str(engine.MAX_WORKERS), 3)

        self._progress = ctk.CTkProgressBar(left, height=6, corner_radius=3)
        self._progress.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))
        self._progress.set(0)

        cards_outer = ctk.CTkScrollableFrame(left, label_text="Portals",
                                              label_font=FONT_HEADER,
                                              corner_radius=8)
        cards_outer.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self._cards_frame = cards_outer

        btn_row = ctk.CTkFrame(left, fg_color="transparent")
        btn_row.grid(row=3, column=0, pady=(0, 14), padx=12)

        self._start_btn = ctk.CTkButton(
            btn_row, text="▶  Start Scraping", width=150, height=38,
            font=FONT_HEADER, fg_color=ACCENT, hover_color=ACCENT_HOVER,
            command=self._start_scraping)
        self._start_btn.pack(side="left", padx=(0, 8))

        self._stop_btn = ctk.CTkButton(
            btn_row, text="■  Stop", width=80, height=38,
            font=FONT_HEADER, fg_color="#555555", hover_color="#3A3A3A",
            state="disabled", command=self._stop_scraping)
        self._stop_btn.pack(side="left")

        # ── Right panel — log ─────────────────────────────────────────
        right = ctk.CTkFrame(content, corner_radius=12)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        log_hdr = ctk.CTkFrame(right, fg_color="transparent", height=36)
        log_hdr.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
        log_hdr.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(log_hdr, text="Live Log",
                      font=FONT_HEADER, anchor="w").grid(row=0, column=0, sticky="w")

        self._autoscroll = tk.BooleanVar(value=True)
        ctk.CTkCheckBox(log_hdr, text="Auto-scroll", variable=self._autoscroll,
                         font=FONT_SMALL, width=100, height=26,
                         checkbox_width=16, checkbox_height=16).grid(
            row=0, column=1, padx=(0, 4))

        # Log level filter
        self._log_filter = tk.StringVar(value="All")
        ctk.CTkOptionMenu(log_hdr, variable=self._log_filter,
                           values=["All", "Warnings & Errors", "Errors only"],
                           width=148, height=26, font=FONT_SMALL).grid(
            row=0, column=2, padx=(0, 6))

        ctk.CTkButton(log_hdr, text="Clear", width=56, height=26,
                       font=FONT_SMALL, fg_color="transparent", border_width=1,
                       command=self._clear_log).grid(row=0, column=3)

        ctk.CTkButton(log_hdr, text="Export", width=62, height=26,
                       font=FONT_SMALL, fg_color="transparent", border_width=1,
                       command=self._export_log).grid(row=0, column=4, padx=(6, 0))

        self._log_box = ctk.CTkTextbox(
            right, font=FONT_MONO, wrap="word", state="disabled",
            corner_radius=8, border_width=0,
            fg_color=("#F0F0F0", "#161616"),
            text_color=("#1A1A1A", "#D4D4D4"))
        self._log_box.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        self._apply_log_tags()

        # Status bar
        self._status_bar = ctk.CTkLabel(
            self, text="Ready", font=FONT_SMALL, anchor="w", height=24,
            fg_color=("#DCDCDC", "#141414"), text_color=("#555555", "#888888"))
        self._status_bar.grid(row=2, column=0, sticky="ew")

    def _make_stat(self, parent, label, value, col):
        f = ctk.CTkFrame(parent, corner_radius=8)
        f.grid(row=0, column=col, padx=4, pady=2, sticky="ew")
        ctk.CTkLabel(f, text=label, font=FONT_SMALL,
                      text_color="#888888").pack(pady=(6, 0))
        lbl = ctk.CTkLabel(f, text=value, font=FONT_HEADER)
        lbl.pack(pady=(0, 6))
        return lbl

    def _populate_portal_cards(self):
        for w in self._cards_frame.winfo_children():
            w.destroy()
        self._portal_cards.clear()
        for name, _ in engine.organizations:
            card = PortalCard(self._cards_frame, name, enabled=True)
            card.pack(fill="x", padx=4, pady=3)
            self._portal_cards[name] = card

    # ── Quick headless toggle (top bar) ───────────────────────────────
    def _quick_headless_toggle(self):
        engine.BROWSER_HEADLESS = self._headless_var_quick.get()

    # ── Scraping control ──────────────────────────────────────────────
    def _start_scraping(self):
        if self._running:
            return

        # Only scrape portals whose card checkbox is enabled
        selected = [p for p in engine.organizations
                    if self._portal_cards.get(p[0]) is not None
                    and self._portal_cards[p[0]].enabled.get()]
        if not selected:
            messagebox.showwarning("No portals selected",
                                   "Enable at least one portal to start.",
                                   parent=self)
            return

        self._selected_portals = selected
        self._running          = True
        self._total_scraped    = 0
        self._start_time       = time.time()

        for card in self._portal_cards.values():
            if card.enabled.get():
                card.set_status("idle")
            else:
                card.set_status("skipped")

        self._progress.set(0)
        self._stat_scraped.configure(text="0")
        self._stat_workers.configure(text=str(engine.MAX_WORKERS))
        engine.error_data.clear()
        engine._stop_requested = False
        self._stopped_by_user  = False

        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal", text="■  Stop",
                                  fg_color="#555555", hover_color="#3A3A3A")
        self._set_status("Scraping in progress…")
        self._append_log("=== Scrape started ===", "INFO")

        if not self._engine_patched:
            self._patch_engine()
            self._engine_patched = True

        t = threading.Thread(target=self._bg_scrape, name="GUI-scrape", daemon=True)
        t.start()

    def _stop_scraping(self):
        engine._stop_requested = True
        self._stopped_by_user  = True
        ex = self._executor
        if ex is not None:
            try:
                ex.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                ex.shutdown(wait=False)
        self._stop_btn.configure(state="disabled", text="⏳  Stopping…",
                                  fg_color=WARNING, hover_color=WARNING)
        self._set_status("Stop requested — finishing active portals…")
        self._append_log("Stop requested by user — draining active workers.", "WARNING")

    def _bg_scrape(self):
        try:
            engine.run_scraping()
        except Exception as exc:
            logging.getLogger().error("GUI bg_scrape error: %s", exc)
        finally:
            self.after(0, self._on_scrape_done)

    def _on_scrape_done(self):
        self._running  = False
        self._executor = None
        engine._stop_requested = False

        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled", text="■  Stop",
                                  fg_color="#555555", hover_color="#3A3A3A")

        errors = len(engine.error_data)
        done   = sum(1 for c in self._portal_cards.values()
                     if c._badge.cget("text") in ("done", "error", "no data"))
        total  = max(len(self._portal_cards), 1)
        self._progress.set(done / total)

        prefix = "Stopped" if self._stopped_by_user else "Done"
        msg = (f"{prefix} — {self._total_scraped} tenders scraped"
               f"{f', {errors} portal(s) failed' if errors else ''}.")
        self._set_status(msg)
        self._append_log(f"=== {msg} ===", "INFO")
        self._toast("Scraping stopped" if self._stopped_by_user else "Scraping complete",
                    f"{self._total_scraped} tenders collected."
                    f"{f' {errors} portals failed.' if errors else ' Report emailed.'}")

    # ── Engine patching ───────────────────────────────────────────────
    def _patch_engine(self):
        app_ref   = self
        _orig_run = engine.Extr.run

        def _patched_run(self_extr):
            if engine._stop_requested:
                logging.getLogger().warning(
                    "[%s] Skipped — stop requested.", self_extr.name)
                card = app_ref._portal_cards.get(self_extr.name)
                if card:
                    card.set_status("cancelled")
                return 0
            card = app_ref._portal_cards.get(self_extr.name)
            if card:
                card.set_status("running")
            result = _orig_run(self_extr)
            if card:
                status = "done" if result > 0 else (
                    "error" if self_extr.name in engine.error_data else "no data")
                card.set_status(status, result)
            app_ref._total_scraped += result
            app_ref.after(0, app_ref._refresh_stats)
            return result

        engine.Extr.run = _patched_run

        _orig_pool = engine.run_all_portals_threaded

        def _patched_pool():
            portals_to_run = getattr(app_ref, "_selected_portals",
                                     engine.organizations)
            logging.getLogger().info(
                "Starting pool: %d workers for %d portals",
                engine.MAX_WORKERS, len(portals_to_run))
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=engine.MAX_WORKERS,
                thread_name_prefix="scraper",
            ) as pool:
                app_ref._executor = pool
                futures = {
                    pool.submit(engine.process_portal, portal): portal[0]
                    for portal in portals_to_run
                }
                for future in concurrent.futures.as_completed(futures):
                    name = futures[future]
                    try:
                        future.result()
                        logging.getLogger().info("[%s] Worker finished.", name)
                    except concurrent.futures.CancelledError:
                        logging.getLogger().warning(
                            "[%s] Cancelled by stop request.", name)
                        card = app_ref._portal_cards.get(name)
                        if card:
                            card.set_status("cancelled")
                    except Exception as exc:
                        logging.getLogger().error(
                            "[%s] Worker error: %s", name, exc)
                        with engine._error_lock:
                            engine.error_data[name] = {
                                "Status": "Not Successfully Run",
                                "Total Tenders Scraped": 0,
                                "Error": str(exc),
                            }
            app_ref._executor = None
            logging.getLogger().info("All portals complete.")

        engine.run_all_portals_threaded = _patched_pool

    def _refresh_stats(self):
        self._stat_scraped.configure(text=str(self._total_scraped))
        done  = sum(1 for c in self._portal_cards.values()
                    if c._badge.cget("text") in ("done", "error", "no data"))
        total = max(len(self._portal_cards), 1)
        self._progress.set(done / total)

    # ── Log panel ─────────────────────────────────────────────────────
    def _poll_logs(self):
        try:
            while True:
                msg = self._log_queue.get_nowait()
                upper = msg.upper()
                if "ERROR" in upper or "CRITICAL" in upper:
                    tag = "ERROR"
                elif "WARNING" in upper:
                    tag = "WARNING"
                else:
                    tag = "INFO"

                # Apply log level filter
                filt = self._log_filter.get()
                if filt == "Warnings & Errors" and tag == "INFO":
                    continue
                if filt == "Errors only" and tag != "ERROR":
                    continue

                self._append_log(msg, tag)
        except queue.Empty:
            pass
        self.after(120, self._poll_logs)

    def _append_log(self, text: str, tag: str = "INFO"):
        self._log_box.configure(state="normal")
        self._log_box.insert("end", text.rstrip() + "\n", tag)
        self._log_line_count += 1
        if self._log_line_count > 1000:
            self._log_box.delete("1.0", "2.0")
            self._log_line_count -= 1
        if self._autoscroll.get():
            self._log_box.see("end")
        self._log_box.configure(state="disabled")

    def _clear_log(self):
        self._log_box.configure(state="normal")
        self._log_box.delete("1.0", "end")
        self._log_box.configure(state="disabled")
        self._log_line_count = 0

    def _export_log(self):
        path = filedialog.asksaveasfilename(
            title="Export log", defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
            initialfile=f"scraper_log_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._log_box.get("1.0", "end"))
            self._set_status(f"Log exported → {os.path.basename(path)}")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc), parent=self)

    # ── Timer ─────────────────────────────────────────────────────────
    def _poll_timer(self):
        if self._running and self._start_time:
            e   = int(time.time() - self._start_time)
            m, s = divmod(e, 60)
            h, m = divmod(m, 60)
            self._stat_elapsed.configure(
                text=f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}")
        self.after(1000, self._poll_timer)

    # ── Helpers ───────────────────────────────────────────────────────
    def _apply_log_tags(self):
        dark    = ctk.get_appearance_mode() == "Dark"
        info_fg = "#D4D4D4" if dark else "#1A1A1A"
        self._log_box.tag_config("ERROR",   foreground=ERROR_CLR)
        self._log_box.tag_config("WARNING", foreground=WARNING)
        self._log_box.tag_config("INFO",    foreground=info_fg)

    def _toggle_theme(self):
        new = "light" if ctk.get_appearance_mode() == "Dark" else "dark"
        ctk.set_appearance_mode(new)
        self._theme_btn.configure(
            text="🌙  Dark" if new == "light" else "☀  Light")
        self._apply_log_tags()

    def _quick_headless_toggle(self):
        engine.BROWSER_HEADLESS = self._headless_var_quick.get()

    def _on_close(self):
        if self._running:
            if not messagebox.askyesno(
                "Scraping in progress",
                "A scrape is currently running.\n\n"
                "Close anyway? Active workers will finish their current portal "
                "but no email report will be sent.",
                icon="warning", parent=self):
                return
            engine._stop_requested = True
        self.destroy()

    def _set_status(self, text: str):
        self._status_bar.configure(text=f"  {text}")

    def _open_output(self):
        path = engine.OUTPUT_DIR
        os.makedirs(path, exist_ok=True)
        try:
            os.startfile(path)
        except Exception:
            subprocess.Popen(["explorer", path])

    def _toast(self, title: str, message: str):
        try:
            from ctypes import wintypes

            class NOTIFYICONDATA(ctypes.Structure):
                _fields_ = [
                    ("cbSize",           wintypes.DWORD),
                    ("hWnd",             wintypes.HWND),
                    ("uID",              wintypes.UINT),
                    ("uFlags",           wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT),
                    ("hIcon",            wintypes.HICON),
                    ("szTip",            ctypes.c_wchar * 128),
                    ("dwState",          wintypes.DWORD),
                    ("dwStateMask",      wintypes.DWORD),
                    ("szInfo",           ctypes.c_wchar * 256),
                    ("uTimeout",         wintypes.UINT),
                    ("szInfoTitle",      ctypes.c_wchar * 64),
                    ("dwInfoFlags",      wintypes.DWORD),
                ]

            shell32         = ctypes.windll.shell32
            nid             = NOTIFYICONDATA()
            nid.cbSize      = ctypes.sizeof(NOTIFYICONDATA)
            nid.hWnd        = self.winfo_id()
            nid.uID         = 1
            nid.uFlags      = 0x01 | 0x02 | 0x04 | 0x10
            nid.szTip       = "State Tender Scraper"
            nid.szInfoTitle = title[:63]
            nid.szInfo      = message[:255]
            nid.dwInfoFlags = 0x01
            shell32.Shell_NotifyIconW(0x00, ctypes.byref(nid))
            self.after(6000, lambda: shell32.Shell_NotifyIconW(
                0x02, ctypes.byref(nid)))
        except Exception:
            pass


# =======================
# STOP FLAG ON ENGINE
# =======================
engine._stop_requested = False


# =======================
# ENTRY POINT
# =======================
def show_startup_splash() -> tk.Tk:
    splash = tk.Tk()
    splash.overrideredirect(True)
    splash.attributes("-topmost", True)
    splash.configure(bg="#1F1F1F")

    width, height = 420, 140
    screen_w = splash.winfo_screenwidth()
    screen_h = splash.winfo_screenheight()
    pos_x = (screen_w - width) // 2
    pos_y = (screen_h - height) // 2
    splash.geometry(f"{width}x{height}+{pos_x}+{pos_y}")

    container = tk.Frame(splash, bg="#2B2B2B", bd=0, highlightthickness=1,
                         highlightbackground="#3A3A3A")
    container.pack(fill="both", expand=True, padx=1, pady=1)

    tk.Label(
        container,
        text="State Tender Scraper",
        font=("Segoe UI Variable Display", 15, "bold"),
        fg="#E5E5E5",
        bg="#2B2B2B",
    ).pack(pady=(26, 8))

    tk.Label(
        container,
        text="Loading, please wait...",
        font=("Segoe UI Variable Text", 11),
        fg="#BDBDBD",
        bg="#2B2B2B",
    ).pack()

    splash.update_idletasks()
    splash.update()
    return splash


def main():
    splash = show_startup_splash()
    app = App()
    splash.destroy()
    app.mainloop()


if __name__ == "__main__":
    main()
