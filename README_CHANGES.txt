KurdRoom — full update bundle (all rounds)
==========================================

R1 Admin panel & tools
  • "Show more" -> dedicated pages (/admin/users, /admin/feedback, /admin/quotes)
  • Reworked /admin/stats (students by category, top-5 colleges/departments + Other,
    school stages, new users per month)
  • Study Plan Generator replaced by AI Mind-Map Maker (Plus-only)

R2 Premium (Plus) expiry
  • Admin grant buttons: "1 month" / "1 year"; auto-expires; instant + background enforce
  • "Plus expired" notification; expiry shown on /plus; admins = permanent premium

R3 Modern error & offline pages
  • Full-screen error page (404/403/500/502...) with a friendly reason for the visitor,
    in EN/AR/KU; traceback shown to admins only
  • "You're offline" page (PWA) with animated icon + auto-reload on reconnect

R4 Native-app page transitions  (this round)
  • Smooth View-Transitions between pages: the content lifts + fades while the top bar
    and bottom nav stay perfectly still — the way professional native apps feel
  • Consolidated two conflicting transition blocks into one; respects reduced-motion
  • Works in modern browsers (Chrome, Edge, Safari); older browsers simply navigate
    normally with no visual change. Only templates/base.html changed for this.

WHAT TO UPLOAD (replace/add, keep folders):
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

No database work needed. Or apply everything at once:
  git apply kurdroom_all_changes.patch
