#!/usr/bin/env python3
"""Flask web interface for the flight price tracker."""

import os
import sqlite3
from datetime import date

import requests as http
from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, request, url_for

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "dev-only-change-in-prod")

SERPAPI_KEY       = os.getenv("SERPAPI_KEY")
DB_PATH           = "prices.db"
DEFAULT_THRESHOLD = 400

# ─── Database ─────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prices (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                departure_date TEXT    NOT NULL,
                return_date    TEXT    NOT NULL DEFAULT '',
                price          REAL    NOT NULL,
                airline        TEXT    NOT NULL,
                flight_number  TEXT    NOT NULL,
                origin         TEXT    NOT NULL DEFAULT 'IAD',
                destination    TEXT    NOT NULL DEFAULT 'STT',
                scraped_at     TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        # Migrate existing table: add origin/destination columns if missing
        for col in ("origin TEXT NOT NULL DEFAULT 'IAD'",
                    "destination TEXT NOT NULL DEFAULT 'STT'"):
            try:
                conn.execute(f"ALTER TABLE prices ADD COLUMN {col}")
            except Exception:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS alert_settings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT    NOT NULL,
                origin      TEXT    NOT NULL DEFAULT 'IAD',
                destination TEXT    NOT NULL DEFAULT 'STT',
                max_price   REAL    NOT NULL DEFAULT 400,
                active      INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)

# ─── SerpApi ──────────────────────────────────────────────────────────────────

def search_flights(origin, destination, outbound_date,
                   return_date=None, trip_type="1", stops="0"):
    if not SERPAPI_KEY:
        raise ValueError("SERPAPI_KEY is not set in your .env file.")

    params = {
        "engine":        "google_flights",
        "departure_id":  origin,
        "arrival_id":    destination,
        "outbound_date": outbound_date,
        "type":          trip_type,
        "stops":         stops,
        "currency":      "USD",
        "hl":            "en",
        "api_key":       SERPAPI_KEY,
    }
    if return_date:
        params["return_date"] = return_date

    resp = http.get("https://serpapi.com/search.json", params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def parse_flights(data, airline_filter=""):
    results = []
    for section in ("best_flights", "other_flights"):
        for itin in data.get(section, []):
            legs  = itin.get("flights", [])
            price = itin.get("price")
            if price is None or not legs:
                continue

            if airline_filter:
                names = [l.get("airline", "") for l in legs]
                if not any(airline_filter.lower() in n.lower() for n in names):
                    continue

            parsed_legs = []
            for leg in legs:
                dep = leg.get("departure_airport", {})
                arr = leg.get("arrival_airport", {})

                def fmt_time(raw):
                    return raw.split(" ")[-1][:5] if " " in raw else raw[:5]

                parsed_legs.append({
                    "airline":       leg.get("airline", ""),
                    "airline_logo":  leg.get("airline_logo", ""),
                    "flight_number": leg.get("flight_number", ""),
                    "dep_airport":   dep.get("name", ""),
                    "dep_id":        dep.get("id", ""),
                    "dep_time":      fmt_time(dep.get("time", "")),
                    "arr_airport":   arr.get("name", ""),
                    "arr_id":        arr.get("id", ""),
                    "arr_time":      fmt_time(arr.get("time", "")),
                    "duration":      leg.get("duration", 0),
                })

            layovers = itin.get("layovers", [])
            results.append({
                "price":          price,
                "total_duration": itin.get("total_duration", 0),
                "legs":           parsed_legs,
                "layovers":       layovers,
                "is_nonstop":     len(layovers) == 0,
                "airline":        parsed_legs[0]["airline"],
                "airline_logo":   parsed_legs[0]["airline_logo"],
                "flight_numbers": " / ".join(
                    l["flight_number"] for l in parsed_legs if l["flight_number"]
                ),
            })

    results.sort(key=lambda x: x["price"])
    return results


def active_threshold():
    with get_db() as conn:
        row = conn.execute(
            "SELECT max_price FROM alert_settings WHERE active=1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
    return row["max_price"] if row else DEFAULT_THRESHOLD

# ─── Template filters ─────────────────────────────────────────────────────────

@app.template_filter("dur")
def dur_filter(minutes):
    if not minutes:
        return "—"
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m"

# ─── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html", today=date.today().isoformat())


@app.route("/search", methods=["POST"])
def search():
    origin      = request.form.get("origin", "").strip().upper()
    destination = request.form.get("destination", "").strip().upper()
    outbound    = request.form.get("outbound_date", "")
    return_d    = request.form.get("return_date", "")
    trip_type   = request.form.get("trip_type", "1")
    stops       = request.form.get("stops", "0")
    airline     = request.form.get("airline", "").strip()

    errors = []
    if len(origin) != 3:
        errors.append("Enter a valid 3-letter origin airport code (e.g. IAD).")
    if len(destination) != 3:
        errors.append("Enter a valid 3-letter destination airport code (e.g. STT).")
    if not outbound:
        errors.append("Select a departure date.")
    if trip_type == "1" and not return_d:
        errors.append("Select a return date for round trips.")

    if errors:
        for e in errors:
            flash(e, "danger")
        return redirect(url_for("index"))

    try:
        data    = search_flights(origin, destination, outbound,
                                 return_d if trip_type == "1" else None,
                                 trip_type, stops)
        flights = parse_flights(data, airline)
        threshold = active_threshold()

        return render_template("results.html",
            flights=flights,
            origin=origin,
            destination=destination,
            outbound=outbound,
            return_d=return_d,
            trip_type=trip_type,
            airline=airline,
            threshold=threshold,
            count=len(flights),
        )
    except Exception as exc:
        flash(f"Search error: {exc}", "danger")
        return redirect(url_for("index"))


@app.route("/track", methods=["POST"])
def track():
    dep     = request.form.get("departure_date", "")
    ret     = request.form.get("return_date", "")
    price   = request.form.get("price", "")
    airline = request.form.get("airline", "")
    fnum    = request.form.get("flight_number", "")
    origin  = request.form.get("origin", "IAD")
    dest    = request.form.get("destination", "STT")

    if not all([dep, price, airline]):
        flash("Missing data — could not save this flight.", "danger")
        return redirect(url_for("tracked"))

    with get_db() as conn:
        conn.execute(
            "INSERT INTO prices "
            "(departure_date, return_date, price, airline, flight_number, origin, destination) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (dep, ret, float(price), airline, fnum, origin, dest),
        )
    flash(f"Saved: {origin}→{dest} on {dep} @ ${float(price):.2f}", "success")
    return redirect(url_for("tracked"))


@app.route("/tracked")
def tracked():
    with get_db() as conn:
        rows = [dict(r) for r in conn.execute("""
            SELECT id, departure_date, return_date, price, airline,
                   flight_number, origin, destination, scraped_at
            FROM prices
            ORDER BY departure_date, scraped_at DESC
        """).fetchall()]
    return render_template("tracked.html", rows=rows, threshold=active_threshold())


@app.route("/tracked/delete/<int:row_id>", methods=["POST"])
def delete_tracked(row_id):
    with get_db() as conn:
        conn.execute("DELETE FROM prices WHERE id=?", (row_id,))
    flash("Entry removed.", "info")
    return redirect(url_for("tracked"))


@app.route("/alerts", methods=["GET", "POST"])
def alerts():
    if request.method == "POST":
        email     = request.form.get("email", "").strip()
        origin    = request.form.get("origin", "IAD").strip().upper()
        dest      = request.form.get("destination", "STT").strip().upper()
        max_price = request.form.get("max_price", "400")

        if not email or "@" not in email:
            flash("Enter a valid email address.", "danger")
        else:
            try:
                with get_db() as conn:
                    conn.execute(
                        "INSERT INTO alert_settings (email, origin, destination, max_price) "
                        "VALUES (?, ?, ?, ?)",
                        (email, origin, dest, float(max_price)),
                    )
                flash(
                    f"Alert set: email {email} when {origin}→{dest} drops below "
                    f"${float(max_price):.0f}.",
                    "success",
                )
            except Exception as exc:
                flash(f"Could not save alert: {exc}", "danger")
        return redirect(url_for("alerts"))

    with get_db() as conn:
        alert_list = [dict(r) for r in conn.execute(
            "SELECT * FROM alert_settings ORDER BY created_at DESC"
        ).fetchall()]
    return render_template("alerts.html", alerts=alert_list)


@app.route("/alerts/delete/<int:alert_id>", methods=["POST"])
def delete_alert(alert_id):
    with get_db() as conn:
        conn.execute("DELETE FROM alert_settings WHERE id=?", (alert_id,))
    flash("Alert deleted.", "info")
    return redirect(url_for("alerts"))


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=8080, host="0.0.0.0", use_reloader=False)
