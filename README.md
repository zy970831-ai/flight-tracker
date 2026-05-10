# ✈ Flight Price Tracker — IAD → STT

Automatically tracks United Airlines nonstop round-trip prices from **Washington Dulles (IAD)** to **St. Thomas, USVI (STT)**. Checks every Saturday departure for the next 4 months (returning the following Wednesday) and sends an email alert when any price drops below $400.

Also includes a **Flask web app** for flexible searching on any route and dates.

---

## What it does

| Feature | Detail |
|---|---|
| **Auto price checks** | Every 6 hours via GitHub Actions (or APScheduler locally) |
| **Route** | IAD → STT, United Airlines nonstop |
| **Windows tracked** | Every Saturday departure → following Wednesday, next 4 months |
| **Email alerts** | Gmail SMTP notification when price drops below $400 |
| **Price history** | Stored in SQLite (`prices.db`) |
| **Web UI** | Flask app — search any route, track prices, manage alerts |
| **CLI** | `python tracker.py --check now` for an instant one-shot check |

---

## Project structure

```
flight-tracker/
├── tracker.py              # CLI scheduler + core fetch/alert logic
├── app.py                  # Flask web app
├── templates/              # HTML templates (base, index, results, tracked, alerts)
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable template
└── .github/
    └── workflows/
        └── tracker.yml     # GitHub Actions workflow
```

---

## Running locally

### 1. Clone the repo

```bash
git clone https://github.com/zy970831-ai/flight-tracker.git
cd flight-tracker
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Set up environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in your credentials:

```ini
SERPAPI_KEY=your_serpapi_key        # from serpapi.com
EMAIL_FROM=you@gmail.com
EMAIL_PASSWORD=your_app_password    # Gmail App Password, NOT your Gmail password
EMAIL_TO=you@gmail.com
```

> **Gmail App Password:** Google Account → Security → 2-Step Verification → App passwords → Generate

### 4. Run the CLI tracker (checks + 6-hour scheduler)

```bash
# One-shot manual check
python tracker.py --check now

# Start the 6-hour scheduler (runs until you press Ctrl+C)
python tracker.py
```

### 5. Run the web app

```bash
python app.py
```

Open **http://localhost:8080** in your browser.

---

## GitHub Actions setup

GitHub Actions runs the price check automatically every 6 hours in the cloud — no need to keep your computer on.

### Step 1 — Add your secrets

1. Go to your repo on GitHub → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** for each of the following:

| Secret name | Value |
|---|---|
| `SERPAPI_KEY` | Your SerpApi key from [serpapi.com](https://serpapi.com) |
| `EMAIL_FROM` | Your Gmail address |
| `EMAIL_PASSWORD` | Your Gmail App Password (16-character code) |
| `EMAIL_TO` | Address to receive price alerts |

### Step 2 — Enable Actions

GitHub Actions is enabled by default on new repos. If it isn't:
- Go to **Actions** tab → click **Enable GitHub Actions**

### Step 3 — Verify the workflow runs

After adding secrets, trigger your first run manually (see below). The first scheduled run will happen within 6 hours automatically.

---

## Triggering manually from GitHub

1. Go to your repo → **Actions** tab
2. Click **Flight Price Check** in the left sidebar
3. Click the **Run workflow** button (top right of the runs list)
4. Click the green **Run workflow** button in the dropdown

The run takes ~1–2 minutes. You'll see a green checkmark when it completes.

---

## Downloading prices.db

After each run, the SQLite database is saved as a downloadable artifact:

1. Go to **Actions** tab → click a completed run
2. Scroll to the bottom → **Artifacts**
3. Click **prices-db-run-N** to download the database

You can open it with any SQLite viewer, or query it directly:

```bash
sqlite3 prices.db "SELECT * FROM prices ORDER BY scraped_at DESC LIMIT 20;"
```

---

## Failure notifications

GitHub automatically sends you an email if a workflow run fails (e.g. SerpApi is down, or a secret is missing). Make sure email notifications are enabled:

- GitHub → **Settings** → **Notifications** → check **Actions** under Email

---

## Environment variables reference

| Variable | Required | Description |
|---|---|---|
| `SERPAPI_KEY` | Yes | API key from [serpapi.com](https://serpapi.com) |
| `EMAIL_FROM` | Yes | Gmail address used to send alerts |
| `EMAIL_PASSWORD` | Yes | Gmail App Password (not your Gmail login password) |
| `EMAIL_TO` | Yes | Recipient address for price alert emails |
| `FLASK_SECRET` | No | Secret key for Flask sessions (auto-generated if not set) |

---

## Tech stack

- **Python 3.11**
- **requests** — SerpApi HTTP calls
- **Flask** — web interface
- **APScheduler** — local 6-hour scheduler
- **sqlite3** — price history database
- **smtplib** — Gmail email alerts
- **tabulate** — terminal summary table
- **GitHub Actions** — cloud scheduler (no server needed)
