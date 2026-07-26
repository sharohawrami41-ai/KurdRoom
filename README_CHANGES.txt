KurdRoom — Design image fix (image now ALWAYS appears)
======================================================

THE PROBLEM YOU SAW
The written concept was perfect, but no picture appeared, and the text was cut
off near "7) Precedents". Cause: the design answer is long, and the hidden image
instruction sits at the very end — the answer ran out of room before reaching it,
so no image was ever created.

THE FIX (2 changes)
1) The answer now has more room to finish completely (3200 tokens), so it no
   longer cuts off mid-section.
2) The image is now GUARANTEED: if the model's own image line is missing for any
   reason, the app automatically builds the picture from the student's brief
   instead. So a design image ALWAYS shows for "Full concept" and "Space planning".
   Follow-up tweaks (e.g. "make it modern") are folded into the fallback image too.

FILES CHANGED (bundle is complete — just replace all 5)
  app.py                       (answer length 1600 -> 3200 tokens)
  templates/tools_plusai.html  (guaranteed-image fallback)
  templates/base.html          (streaming + image helpers — from the previous update)
  templates/tools_ai.html      (streaming UI — from the previous update)
  static/sw.js                 (cache bumped to v14 so devices reload the new version)

HOW TO APPLY
  Replace these 5 files, restart the app, then reopen the app once on your phone.
  Test: AI Design Studio -> focus "Full concept" -> type a brief -> Run.
  You'll get the text, then a picture underneath with "New image" and "Download".

REMINDER ABOUT IMAGE QUALITY
  Images use the free Pollinations service (no key, works out of the box). Quality
  is good but can vary — tap "New image" for another version. For top-tier, faster,
  more consistent renders, we can add a premium image model behind an image key.
