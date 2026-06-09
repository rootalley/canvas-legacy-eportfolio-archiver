# Canvas Legacy ePortfolio Archiver

A Python command-line tool that downloads and organizes all Canvas legacy ePortfolios from your institution before they are permanently deleted.

## Why This Tool Exists

Instructure is discontinuing its legacy Canvas ePortfolio feature on June 30, 2026. When this feature gets removed, every ePortfolio in the system will be permanently deleted. This tool provides a fallback mechanism for Canvas administrators to:

- Automate the downloading of every ePortfolio from your Canvas instance (including, optionally, previously deleted ePortfolios that are still recoverable)
- Organize the ePortfolios by owner for easy lookup and potential distribution back to their owners
- Run the process incrementally — you can interrupt and resume processing at any time

## Features

- Reads the ePortfolios report CSV directly as exported from Canvas — no editing required
- Deduplicates entries automatically (Canvas reports list each ePortfolio once per login identity)
- Skips already-downloaded files on re-run, so interrupted jobs resume without duplicating work
- Saves a progress log (`archive_log.json`) after each download
- Handles interrupts gracefully — `Ctrl+C` cancels the current download, cleans up any temporarily restored portfolios, saves progress, and exits
- Saves debug screenshots (`login_debug.png`, `masquerade_debug.png`) when automation fails, to help diagnose any issues

## Prerequisites

### Python 3.10 or later

Check whether Python is already installed by opening a terminal and running:

```
python3 --version
```

If that does not print a version number, try:

```
python --version
```

If either returns `Python 3.10.x` or higher, you are ready — note which command worked, as you will use it consistently throughout this guide. If neither works, download and install Python from [python.org/downloads](https://www.python.org/downloads/). Accept all defaults during installation. On Windows, check **"Add Python to PATH"** before clicking Install.

### A Terminal or Command Prompt

- **macOS**: Open **Terminal** (search for it in Spotlight with `⌘-Space`)
- **Windows**: Open **Command Prompt** or **PowerShell** (search in the Start menu)
- **Linux**: Open your system terminal

All commands in this guide will be typed into the terminal.

## Canvas Setup

### Generating a Canvas API Token

1. Log in to Canvas as the admin account you will use to run the script. We recommend using a dedicated service account for this process.
2. Click the account avatar in the top-left corner, then click **Settings**.
3. Scroll down to the **Approved Integrations** section and click **+ New Access Token**.
4. Enter a purpose (e.g. "ePortfolio Archiver") and an optional expiration date (July 1, 2026 is recommended).
5. Click **Generate Token**.
6. **Copy the token immediately** — Canvas will never show it again. Paste it into your `.env` file as the `EPORTFOLIO_ARCHIVER_CANVAS_API_TOKEN` value.

### Downloading the ePortfolio Report(s) from Canvas

1. Log in to Canvas as a root account admin.
2. Navigate to **Admin** → your institution name.
3. Click **Settings**.
4. Click the **Reports** tab.
5. Find the **ePortfolios** report and click **Configure**. To get a report of active ePortfolios, click **Run Report**. To get a report of deleted ePortfolios, click the **Only include ePortfolios that have been removed** checkbox, then click **Run Report**.
8. Wait for the report to finish generating (refresh the page if needed).
7. Download the CSV file when the link appears.
6. Save it as `eportfolio_list.csv` in the project folder (or update `EPORTFOLIO_ARCHIVER_CSV_PATH` in your `.env` to point to wherever you saved it).

> [!NOTE]
> The CSV report may contain more than one row per. The script automatically deduplicates rows by ePortfolio ID, so no manual editing is needed.

## Script Setup

### Step 1 — Download the project files

Download the files from GitHub and place them in a folder on your computer. The easiest way is to click the green **Code** button and choose **Download ZIP**, then unzip the folder somewhere convenient (e.g. your Desktop or Documents folder).

Alternatively, if you have Git installed:

```
git clone https://github.com/rootalley/canvas-legacy-eportfolio-archiver.git
```

### Step 2 — Open a terminal in the project folder

In your terminal, navigate to the folder you just downloaded. Replace the path below with wherever you put the folder:

```
cd /path/to/canvas-legacy-eportfolio-archiver
```

For example, if you unzipped it on your Mac desktop:

```
cd ~/Desktop/canvas-legacy-eportfolio-archiver
```

On Windows:

```
cd C:\Users\YourName\Desktop\canvas-legacy-eportfolio-archiver
```

### Step 3 — Create a virtual environment

A virtual environment is an isolated Python workspace that keeps this project's dependencies separate from everything else on your computer. Create one by running:

```
python3 -m venv venv
```

Substitute `python` if that is the command that worked when you checked your version above. This creates a folder named `venv` inside the project folder. You only need to do this once.

### Step 4 — Activate the virtual environment

You must activate the virtual environment each time you open a new terminal session to work with this project.

**macOS / Linux:**

```
source venv/bin/activate
```

**Windows (Command Prompt):**

```
venv\Scripts\activate.bat
```

**Windows (PowerShell):**

```
venv\Scripts\Activate.ps1
```

After activation, your terminal prompt will show `(venv)` at the beginning, confirming the environment is active.

### Step 5 — Install dependencies

```
pip install -r requirements.txt
```

Then install the Chromium browser that the script uses for automation:

```
playwright install chromium
```

This downloads a bundled version of Chromium (~150 MB) used solely by this script. It does not affect your regular browser.

### Step 6 — Create the configuration file

Copy the example configuration file to create your own:

**macOS / Linux:**

```
cp .env.example .env
```

**Windows:**

```
copy .env.example .env
```

Open the `.env` file in any text editor (Notepad, TextEdit, VS Code, etc.) and fill in your values:

```
EPORTFOLIO_ARCHIVER_CANVAS_URL=https://your-institution.instructure.com
EPORTFOLIO_ARCHIVER_CANVAS_USERNAME=your_admin_username
EPORTFOLIO_ARCHIVER_CANVAS_PASSWORD=your_admin_password
EPORTFOLIO_ARCHIVER_CANVAS_API_TOKEN=your_api_token
EPORTFOLIO_ARCHIVER_CSV_PATH=eportfolio_list.csv
EPORTFOLIO_ARCHIVER_EXPORT_DIR=downloads
EPORTFOLIO_ARCHIVER_DOWNLOAD_TIMEOUT_MS=600000
```

- **`CANVAS_URL`** — The base URL of your Canvas instance, without a trailing slash. Example: `https://myschool.instructure.com`
- **`CANVAS_USERNAME`** and **`CANVAS_PASSWORD`** — Credentials for a Canvas admin account. This account will log in and masquerade as each ePortfolio owner to perform the download. Using a dedicated service account is recommended.
- **`CANVAS_API_TOKEN`** — A Canvas API token for the same admin account. Used for all Canvas API calls, including looking up user names and SIS IDs for folder naming (affects every ePortfolio) and restoring and re-deleting soft-deleted ePortfolios (required for `--include-deleted`). Without a token, API calls fall back to browser session authentication, which may not have sufficient privileges — folder names may fall back to the CSV author name and Canvas user ID. See [Generating a Canvas API Token](#generating-a-canvas-api-token).
- **`CSV_PATH`** — Path to the ePortfolios report CSV you downloaded from Canvas. Leave as `eportfolio_list.csv` if the file is in the project folder.
- **`EXPORT_DIR`** — Folder where downloaded ZIPs will be saved. The folder is created automatically. Default: `downloads`
- **`DOWNLOAD_TIMEOUT_MS`** — How long (in milliseconds) to wait for a single ZIP to finish generating before giving up. Default is `600000` (10 minutes). Increase this if you have very large ePortfolios.

> [!WARNING]
> The `.env` file contains your admin password and API access token. This file is listed in `.gitignore` and will not be committed to Git. Do not share your `.env` credentials or commit them to version control.

## Running the Script

Make sure your virtual environment is [activated](#step-4--activate-the-virtual-environment) (you will see `(venv)` in your prompt) before running any of the following commands.

### Download active ePortfolios

```
python archiver.py
```

This processes all ePortfolios with `workflow_state=active`. The script will print its configuration, then work through the list one ePortfolio at a time, showing progress as it goes.

### Download active and deleted ePortfolios

```
python archiver.py --include-deleted
```

Adding `--include-deleted` also processes ePortfolios with `workflow_state=deleted`. For each deleted ePortfolio, the script:

1. Temporarily restores it via the Canvas API
2. Downloads it using browser automation (masquerading as the owner)
3. Re-deletes it immediately after

The API token is required for this workflow.

### Interrupting and resuming

Press **Ctrl+C** at any time to stop the script. The current download will be abandoned, any temporarily restored ePortfolio will be re-deleted, and all completed progress will be saved.

To resume from where you left off, simply run the same command again. The script skips any ePortfolio whose ZIP file is already present on disk, so no work is duplicated.

## Output Structure

Downloads are organized under the `downloads/` folder (or whatever you set `EXPORT_DIR` to):

```
downloads/
├── Endres, Steven M. ID 12345/
│   └── 67890 My EPortfolio.zip
└── ...
```

- Each user gets a folder named `Lastname, Firstname ID {sis_id}`. If no SIS ID is available, `USER {canvas_id}` is used instead.
- Each ZIP is named `{eportfolio_id} {eportfolio_name}.zip`.
- A supplementary progress log is written to `archive_log.json` after each download. This file can be deleted safely — the script will rebuild it from the files already on disk on the next run.

## Troubleshooting

**`python3: command not found` or `python: command not found`**
Python is not installed or not on your PATH. Re-install Python from [python.org/downloads](https://www.python.org/downloads/) and make sure "Add Python to PATH" is checked during installation on Windows.

**`Missing required environment variables`**
Your `.env` file is missing or one of the required values is blank. Open `.env` and verify that `EPORTFOLIO_ARCHIVER_CANVAS_URL`, `EPORTFOLIO_ARCHIVER_CANVAS_USERNAME`, and `EPORTFOLIO_ARCHIVER_CANVAS_PASSWORD` are all set.

**Login failed / `login_debug.png` was saved**
The script could not find the login form. Open `login_debug.png` to see what the browser actually loaded. Common causes: wrong `CANVAS_URL`, an SSO redirect intercepting `/login/canvas`, or Canvas showing a maintenance page.

**Masquerade failed / `masquerade_debug.png` was saved**
The script could not find the masquerade confirmation button. This may mean the admin account does not have masquerade permission, or the Canvas UI changed. Open `masquerade_debug.png` for a screenshot of the page at the moment of failure.

**Download timed out**
Canvas took longer than `DOWNLOAD_TIMEOUT_MS` to generate the ZIP. Increase the value in your `.env` (e.g. `1800000` for 30 minutes) and re-run. The failed ePortfolio will be retried automatically.

**Restore API returned HTTP 401 or HTTP 403**
The API token is missing or does not have admin privileges. Regenerate the token from the admin account and update `EPORTFOLIO_ARCHIVER_CANVAS_API_TOKEN` in your `.env`.

## Frequently Asked Questions

### How long does it take to run?

It depends on how much data each ePortfolio contains. In one run of approximately 2,500 ePortfolios on a hosted Canvas instance, the archiver averaged about 26 seconds per item. ZIP generation appears to be the bottleneck.

### Why does the tool update the `updated_at` timestamp on ePortfolios?

Canvas treats generating an export ZIP as an "update," which causes the `updated_at` timestamp to change. This is debatable behavior, but it can work in your favor: run the archiver once as an early snapshot, then re-run the ePortfolio report as your end-of-life date approaches. Any ePortfolios with an `updated_at` timestamp after your initial run were genuinely changed by their owners and may warrant a fresh download. Remove or relocate the original ZIP files for those items (or set a different `EXPORT_DIR`) so the tool does not skip them on the follow-up run.

## Contributing

Contributions, bug reports, and pull requests are welcome. If you encounter a Canvas configuration this script does not handle — a different SSO setup, an unusual login page, a Canvas version with different selectors — please open an issue or submit a pull request with a fix.

1. Fork the repository
2. Create a branch: `git checkout -b my-fix`
3. Make your changes
4. Open a pull request describing what you changed and why

This project is released under the [MIT License](LICENSE).
