#!/usr/bin/env python3
"""
Flight price tracker: IAD → STT (United Airlines nonstop, Sat departure / Wed return)
"""

import argparse
import os
import smtplib
import sqlite3
import sys
from datetime import date, timedelta
from typing import List, Optional, Tuple
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv
from tabulate import tabulate

load_dotenv()

# ─── Config ──────────────────────────────────────────────────────────────────

SERPAPI_KEY     = os.getenv("SERPAPI_KEY")
EMAIL_FROM      = os.getenv("EMAIL_FROM")
EMAIL_TO        = os.getenv("EMAIL_TO")
EMAIL_PASSWORD  = os.getenv("EMAIL_PASSWORD")

ORIGIN          = "IAD"
DESTINATION     = "STT"
AIRLINE_FILTER  = "United"
ALERT_THRESHOLD = 400
DB_PATH         = "prices.db"

# ANSI colours
GREEN = "\033[92m"
BOLD  = "\033[1m"
RESET = "\033[0m"

# ─── Database ─────────────────────────────────────────────────────────────────

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                departure_date TEXT    NOT NULL,
                return_date    TEXT    NOT NULL,
                price          REAL    NOT NULL,
                airline        TEXT    NOT NULL,
                flight_number  TEXT    NOT NULL,
                scraped_at     TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)


def store_result(departure_date, return_date, price, airline, flight_number):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO prices (departure_date, return_date, price, airline, flight_number)
            VALUES (?, ?, ?, ?, ?)
            """,
            (departure_date, return_date, price, airline, flight_number),
        )


def latest_prices():
    """Return the most-recent price row for every (departure_date, return_date) pair."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT departure_date, return_date, price, airline, flight_number, scraped_at
            FROM prices
            WHERE id IN (
                SELECT MAX(id)
                FROM prices
                GROUP BY departure_date, return_date
            )
            ORDER BY departure_date
        """).fetchall()
    return [dict(r) for r in rows]

# ─── Date helpers ─────────────────────────────────────────────────────────────

def next_saturday(from_date: date) -> date:
    """Return the first Saturday on or after from_date."""
    days_ahead = (5 - from_date.weekday()) % 7  # Monday=0 … Saturday=5
    if days_ahead == 0:
        days_ahead = 7
    return from_date + timedelta(days=days_ahead)


def get_saturday_windows(months: int = 4) -> List[Tuple[str, str]]:
    """
    Return (departure_date, return_date) strings for every Saturday in the
    next `months` months, with the return date being the following Wednesday.
    """
    today    = date.today()
    end_date = today + timedelta(days=months * 30)
    windows  = []
    sat = next_saturday(today + timedelta(days=1))  # start from *next* Saturday
    while sat <= end_date:
        wed = sat + timedelta(days=4)               # Sat → Wed = 4 days
        windows.append((sat.isoformat(), wed.isoformat()))
        sat += timedelta(weeks=1)
    return windows

# ─── SerpApi fetch ────────────────────────────────────────────────────────────

def fetch_flights(departure_date: str, return_date: str) -> Optional[dict]:
    """
    Call SerpApi Google Flights API for a round-trip IAD→STT nonstop search.
    Returns the raw JSON dict, or None on error.
    """
    if not SERPAPI_KEY:
        print("[ERROR] SERPAPI_KEY is not set.", file=sys.stderr)
        return None

    params = {
        "engine":         "google_flights",
        "departure_id":   ORIGIN,
        "arrival_id":     DESTINATION,
        "outbound_date":  departure_date,
        "return_date":    return_date,
        "type":           "1",          # 1 = round trip
        "stops":          "1",          # 1 = nonstop only
        "currency":       "USD",
        "hl":             "en",
        "api_key":        SERPAPI_KEY,
    }

    try:
        resp = requests.get("https://serpapi.com/search.json", params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        print(f"[ERROR] SerpApi request failed for {departure_date}: {exc}", file=sys.stderr)
        return None


def parse_best_united_nonstop(data: dict) -> Optional[Tuple[float, str, str]]:
    """
    Parse SerpApi response and return (price, airline_name, flight_number) for
    the cheapest United Airlines nonstop round trip, or None if none found.

    Checks both 'best_flights' and 'other_flights' arrays.
    """
    candidates = []

    for section in ("best_flights", "other_flights"):
        for itinerary in data.get(section, []):
            legs = itinerary.get("flights", [])

            # Nonstop = single leg each way; the API 'stops=1' already filters,
            # but we double-check layovers are absent.
            if itinerary.get("layovers"):
                continue

            # All legs must be operated by United
            airlines = [leg.get("airline", "") for leg in legs]
            if not all(AIRLINE_FILTER in a for a in airlines):
                continue

            price = itinerary.get("price")
            if price is None:
                continue

            # Collect flight numbers from all legs (typically one outbound leg shown)
            flight_numbers = [leg.get("flight_number", "") for leg in legs]
            flight_number  = " / ".join(fn for fn in flight_numbers if fn) or "N/A"
            airline_name   = legs[0].get("airline", AIRLINE_FILTER) if legs else AIRLINE_FILTER

            candidates.append((price, airline_name, flight_number))

    if not candidates:
        return None

    # Return the cheapest option
    return min(candidates, key=lambda x: x[0])

# ─── Email alert ──────────────────────────────────────────────────────────────

def load_active_alerts() -> list:
    """Return all active alert rows from the DB (set via the web UI)."""
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT email, origin, destination, max_price "
                "FROM alert_settings WHERE active=1"
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def send_alert(to_email: str, departure_date: str, return_date: str,
               origin: str, destination: str, price: float,
               flight_number: str, threshold: float):
    if not all([EMAIL_FROM, EMAIL_PASSWORD]):
        print("[WARN] EMAIL_FROM / EMAIL_PASSWORD not set — skipping alert.", file=sys.stderr)
        return

    subject = f"✈ Price Alert: {origin}→{destination} ${price:.0f} — departs {departure_date}"
    body = (
        f"A flight from {origin} to {destination} has dropped below ${threshold:.0f}!\n\n"
        f"  Departure : {departure_date}\n"
        f"  Return    : {return_date}\n"
        f"  Price     : ${price:.2f}\n"
        f"  Flight    : {flight_number}\n\n"
        f"Book soon before the price rises."
    )

    msg = MIMEMultipart()
    msg["From"]    = EMAIL_FROM
    msg["To"]      = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, to_email, msg.as_string())
        print(f"[ALERT] Email sent to {to_email} — {departure_date} @ ${price:.2f}")
    except smtplib.SMTPException as exc:
        print(f"[ERROR] Failed to send email to {to_email}: {exc}", file=sys.stderr)

# ─── Core check ───────────────────────────────────────────────────────────────

def run_check():
    """Fetch prices for all upcoming Saturday windows and store results."""
    print(f"\n{'─'*60}")
    print(f"  Checking flights: {ORIGIN} → {DESTINATION}  (United nonstop)")
    print(f"{'─'*60}")

    windows = get_saturday_windows(months=4)
    if not windows:
        print("No Saturday windows found in the next 4 months.")
        return

    for dep, ret in windows:
        print(f"  Fetching {dep} → {ret} …", end=" ", flush=True)
        data = fetch_flights(dep, ret)

        if data is None:
            print("FETCH ERROR")
            continue

        result = parse_best_united_nonstop(data)
        if result is None:
            print("no United nonstop found")
            continue

        price, airline, flight_number = result
        store_result(dep, ret, price, airline, flight_number)

        # Check against every active alert in the DB (set via web UI)
        db_alerts = [
            a for a in load_active_alerts()
            if a["origin"] == ORIGIN and a["destination"] == DESTINATION
        ]
        # Fall back to the hardcoded threshold if no DB alerts exist
        thresholds = [a["max_price"] for a in db_alerts] or [ALERT_THRESHOLD]
        min_threshold = min(thresholds)

        tag = f"{GREEN}${price:.0f} ← ALERT{RESET}" if price < min_threshold else f"${price:.0f}"
        print(tag)

        for alert in db_alerts:
            if price < alert["max_price"]:
                send_alert(alert["email"], dep, ret, ORIGIN, DESTINATION,
                           price, flight_number, alert["max_price"])

        # Also send to EMAIL_TO from .env if no DB alerts are set
        if not db_alerts and price < ALERT_THRESHOLD and EMAIL_TO:
            send_alert(EMAIL_TO, dep, ret, ORIGIN, DESTINATION,
                       price, flight_number, ALERT_THRESHOLD)

    print_summary()


def print_summary():
    """Print a formatted table of the latest price for each date window."""
    rows = latest_prices()
    if not rows:
        print("\nNo data in database yet.\n")
        return

    table_data = []
    for r in rows:
        price    = r["price"]
        price_str = f"${price:.2f}"
        if price < ALERT_THRESHOLD:
            price_str   = f"{GREEN}{BOLD}{price_str}{RESET}"
            dep_display = f"{GREEN}{r['departure_date']}{RESET}"
            ret_display = f"{GREEN}{r['return_date']}{RESET}"
        else:
            dep_display = r["departure_date"]
            ret_display = r["return_date"]

        table_data.append([
            dep_display,
            ret_display,
            price_str,
            r["airline"],
            r["flight_number"],
            r["scraped_at"][:16],
        ])

    headers = ["Departure", "Return", "Price", "Airline", "Flight #", "Checked At"]
    print(f"\n{'─'*60}")
    print(f"  Summary — IAD → STT  (prices below ${ALERT_THRESHOLD} highlighted)")
    print(f"{'─'*60}")
    print(tabulate(table_data, headers=headers, tablefmt="simple"))
    print()

# ─── CLI + scheduler entry point ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="IAD→STT United nonstop flight price tracker"
    )
    parser.add_argument(
        "--check",
        metavar="MODE",
        help="Pass 'now' to trigger an immediate check and exit",
    )
    args = parser.parse_args()

    init_db()

    if args.check and args.check.lower() == "now":
        run_check()
        return

    # Scheduled mode: run immediately, then every 6 hours
    print("Starting scheduler — checks every 6 hours (Ctrl+C to stop).")
    print("Tip: run  python tracker.py --check now  for an immediate one-shot check.\n")

    run_check()   # run once on startup

    scheduler = BlockingScheduler()
    scheduler.add_job(run_check, "interval", hours=6, id="flight_check")
    try:
        scheduler.start()
    except KeyboardInterrupt:
        print("\nScheduler stopped.")


if __name__ == "__main__":
    main()
