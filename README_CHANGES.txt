KurdRoom — Small fix: Design Studio focus buttons now highlight when tapped
===========================================================================

WHAT WAS WRONG
On AI Design Studio, tapping "Space planning / Technical / Studio critique" didn't
visually highlight, so it looked like nothing happened. (The selection actually
worked, but you couldn't see which one was chosen.)

FIX
The selected focus button now clearly highlights in your accent colour the moment
you tap it.

REMINDER — how the focus buttons work
They don't change the page; they change the ANSWER you get after pressing Run:
  • Full concept   — the whole design (concept, site, spaces, form, materials…)
  • Space planning — room-by-room areas, zoning, circulation
  • Technical       — structure, wall/roof build-ups, environment, services
  • Studio critique — critiques the design you describe (strengths, weak points…)
So: pick a focus, type your brief, press Run — the difference shows in the reply.

FILES CHANGED (2)
  templates/tools_plusai.html   (focus highlight fix)
  static/sw.js                  (cache -> v17)

APPLY
  Replace these 2 files, restart, reopen the app once.
