KurdRoom — full update bundle (all rounds)
==========================================
R1 Admin panel & tools:  "Show more" -> dedicated pages; reworked /admin/stats
   (students by category, top-5 colleges/departments + Other, school stages,
   new users per month); Study Plan -> AI Mind-Map Maker (Plus-only).
R2 Premium expiry:  admin grant "1 month" / "1 year"; auto-expire (instant +
   background); "expired" notification; expiry on /plus; admins = permanent.
R3 Error & offline pages:  modern full-screen error page (friendly reason for
   users, traceback for admins only, EN/AR/KU); animated "You're offline" page
   that auto-reloads on reconnect.
R4/R5 Page transitions:  native horizontal SLIDE like Instagram / Telegram.
   • New page slides in from the side, previous page parallaxes out — the top
     bar and bottom nav stay perfectly fixed.
   • Direction-aware: going BACK slides the opposite way (Navigation API).
   • Mirrored for Arabic / Kurdish (RTL).  No more white fade.
   • Only templates/base.html changed for this.

UPLOAD (replace/add, keep folders):
  app.py
  static/sw.js
  templates/base.html
  templates/admin.html
  templates/admin_stats.html
  templates/notifications.html
  templates/plus.html
  templates/error.html
  templates/admin_users.html      (new)
  templates/admin_feedback.html   (new)
  templates/admin_quotes.html     (new)
  templates/tools_mindmap.html    (new)
  templates/offline.html          (new)
DELETE:
  templates/tools_studyplan.html

No database work needed.  Or: git apply kurdroom_all_changes.patch
Tip: after uploading, close & reopen the installed app once so the service
worker picks up the new version (or it refreshes on the next visit).
