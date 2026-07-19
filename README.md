# FocusPlan — Student Social Planner

A complete student social website: schedule planning, login system, admin panel,
friends & groups with chat, notifications, leaderboard, badges, direct messages,
group file sharing, shared flashcard decks, university tools, and 3 languages
(English / العربية / کوردی). Installable on phones as a PWA (Add to Home Screen).

Data lives in `planner.db` (plus `static/avatars/` for profile photos and
`groupfiles/` for group uploads) — back those up and nothing is ever lost.
Upgrading? Copy those into the new folder; the database migrates itself.

## Your admin account

| Username | Password |
|----------|----------|
| `sharo` | `Sharo@2006` |

This account is created automatically the first time the app runs.
Log in with it and you will see the **Admin Panel** button in the top bar.

## What the admin panel can do (no coding needed)

- See **every user** who registered: username, role, number of plans, date created, last login
- Promote/demote admins, delete users, reset any user's password
- Change the **site name**, **taglines** (in all 3 languages), and the **accent color**
- Turn new registrations on/off
- Add or delete the **motivational quotes** shown to users

## Run it on your computer

1. Install Python 3 (python.org)
2. Open a terminal in this folder and run:
   ```
   pip install -r requirements.txt
   python app.py
   ```
3. Open http://127.0.0.1:5001 in your browser.

(The app uses port 5001 because macOS AirPlay occupies port 5000. To use a
different port: `PORT=8000 python app.py`)

The database (`planner.db`) is created automatically. To start completely fresh,
just delete `planner.db` and run again.

## Put it online for free (PythonAnywhere — easiest)

1. Create a free account at https://www.pythonanywhere.com
2. Go to **Files** and upload this whole folder (or upload the zip and unzip it
   from a Bash console with `unzip planner.zip`)
3. Go to **Web → Add a new web app → Flask → Python 3.x**
4. Set the source code path to your uploaded folder, and edit the WSGI file so
   it imports your app:
   ```python
   import sys
   sys.path.insert(0, "/home/YOURUSERNAME/planner")
   from app import app as application
   ```
5. Click **Reload** — your site is live at `yourusername.pythonanywhere.com`

Other free/cheap options: Render.com, Railway.app, or any VPS
(`pip install gunicorn` then `gunicorn -b 0.0.0.0:8000 app:app`).

## IMPORTANT — before going public

Set a strong secret key so login sessions are secure:

- On Linux/PythonAnywhere: add an environment variable `SECRET_KEY=some-long-random-text`
- Or simply edit the line near the top of `app.py`:
  ```python
  app.secret_key = "put-a-long-random-string-here"
  ```

Passwords are stored **hashed** (never in plain text), so even you as admin
cannot read users' passwords — you can only reset them. That is how it should be.

## Turning it into Android / iPhone apps later

The site is fully mobile-responsive already. When you're ready:

- Wrap it with **Capacitor** (capacitorjs.com) — free, do-it-yourself
- Google Play developer account: **$25 one time**
- Apple Developer Program: **$99 per year**

## Files

```
app.py               ← the whole backend (Flask + SQLite)
requirements.txt
templates/
  base.html          ← design, colors, layout
  login.html
  register.html
  dashboard.html     ← the planner
  admin.html         ← the admin panel
planner.db           ← created automatically (your data — back it up!)
```
