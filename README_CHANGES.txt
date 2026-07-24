KurdRoom — full update bundle (all rounds)
==========================================
R1 Admin panel & tools · R2 Premium expiry · R3 Error & offline pages
R4/R5 Page transitions (this round refined):
   • Horizontal SLIDE like Instagram/Telegram; top bar + bottom nav stay fixed.
   • FIXED the "combined/ghosting pages" bug — each page now has an opaque
     background so the incoming page fully covers the outgoing one.
   • FASTER: transition shortened to 0.24s.
   • SWIPE navigation: swipe left / right on the 5 main tabs (Home, Focus,
     University, Groups, Notifications) to move between them — with the correct
     slide direction, mirrored for Arabic/Kurdish. Swipes are ignored on inputs,
     chats and horizontally-scrolling areas so nothing is hijacked.
   • Only templates/base.html changed this round.

UPLOAD (replace/add, keep folders):
  app.py, static/sw.js, templates/base.html, templates/admin.html,
  templates/admin_stats.html, templates/notifications.html, templates/plus.html,
  templates/error.html, templates/admin_users.html (new),
  templates/admin_feedback.html (new), templates/admin_quotes.html (new),
  templates/tools_mindmap.html (new), templates/offline.html (new)
DELETE: templates/tools_studyplan.html

No database work needed.  Or: git apply kurdroom_all_changes.patch
After uploading, reopen the app once so the new version loads.
