# Putting FocusPlan on plan.aikurd.com — step by step

## Part 1 — Put the code on GitHub (5 min)

1. Create a free account at https://github.com if you don't have one
2. Click **New repository** → name it `focusplan` → Private → Create
3. Upload ALL files from this folder (drag & drop works on the GitHub page:
   `app.py`, `requirements.txt`, `render.yaml`, `templates/`, `static/`, etc.)

## Part 2 — Deploy on Render (10 min)

1. Create an account at https://render.com (sign in with GitHub)
2. Click **New +** → **Blueprint** → select your `focusplan` repository
3. Render reads `render.yaml` automatically → click **Apply**
4. Wait ~3 minutes. Your site is live at something like
   `https://focusplan.onrender.com` — test it, log in as sharo!

Notes:
- The blueprint uses the **Starter plan ($7/month)** with a 1 GB persistent
  disk — your database, photos, and files survive every update. This is the
  right choice for a real site.
- Want to test 100% free first? Create it as a normal **Web Service** on the
  Free plan instead (build: `pip install -r requirements.txt`, start:
  `gunicorn -w 2 -b 0.0.0.0:$PORT app:app`). BUT on the free plan the site
  sleeps when idle and **data is erased on every redeploy** — fine for a demo,
  not for real users.

## Part 3 — Connect plan.aikurd.com (5 min)

1. In Render: your service → **Settings** → **Custom Domains** →
   **Add Custom Domain** → type `plan.aikurd.com`
2. Render shows you a CNAME target like `focusplan.onrender.com`
3. Go to the website where you manage aikurd's DNS
   (Namecheap / GoDaddy / Cloudflare — wherever you bought it)
   → DNS settings → **Add record**:
   - Type: `CNAME`
   - Host / Name: `plan`
   - Value / Target: `focusplan.onrender.com` (what Render showed you)
   - TTL: automatic
4. Back in Render, click **Verify**. Within minutes–hours:
   ✅ https://plan.aikurd.com is live, with free HTTPS automatically.
   Your existing aikurd.com website is completely unaffected.

## After it's live

- HTTPS is automatic → the "Add to Home Screen" phone-app feature now works
- Add your AI API key in Admin Panel → Site settings when you're ready
- Updating the site later = upload the new files to GitHub → Render redeploys
  automatically. Your data is safe on the disk.
- Backup: Render dashboard → Disks → snapshots, or download planner.db
  occasionally.
