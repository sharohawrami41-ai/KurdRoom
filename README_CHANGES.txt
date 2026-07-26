KurdRoom — NEW: Sketch → Render (module 2, premium images via Replicate)
========================================================================

WHAT THIS IS
The premium visual module. In the menu: "🖼 Sketch → Render".
Two modes:
  • From my sketch  — the student uploads their OWN hand sketch or floor plan and
    gets a professional render that follows their drawing. A "how closely to follow
    your sketch" slider controls how faithful vs. creative the result is.
  • From text only  — generate an image from a description.
Shows a loading animation, then the image with Download + "Render again".

SETUP (2 minutes, one-time)  ← IMPORTANT
1. Open the app → Admin → Settings.
2. In "🖼 Replicate API key" paste your token (starts with r8_...).
3. In "Image quality (text-to-image)" pick a tier:
      Fast     — Flux Schnell  (~$0.003/image, ~300 per $1)
      Standard — Flux Dev      (~$0.025/image)
      Premium  — Flux 1.1 Pro  (~$0.04/image)
   (Sketch→render always uses Flux Dev so it can follow the uploaded drawing.)
4. Save. Make sure your Replicate account has credit (you already added some).

SECURITY
- The token is stored in your app settings (like your Anthropic key) and is used
  ONLY server-side — it is never exposed to the students' browsers.
- You pasted the token in chat, so please REGENERATE it on replicate.com
  (API tokens → delete old, create new) and put the fresh one in Admin → Settings.

FILES CHANGED (complete bundle — replace all)
  app.py                        (render engine, /api/render, /tools/render, settings, i18n)
  templates/tools_render.html   (NEW — the Sketch → Render page)
  templates/admin.html          (Replicate key + quality fields)
  templates/base.html           (menu link)
  templates/tools_plusai.html   (from module 1 — included for completeness)
  templates/tools_ai.html       (from earlier update — included for completeness)
  static/sw.js                  (cache -> v16)

NOTE ON TESTING
  I built this exactly to Replicate's documented API and tested every path with a
  simulated Replicate response (success, no-key, no-image all handled). I could NOT
  test a real render from my side because my sandbox can't reach replicate.com — you
  will be the first to run it live. If the very first render errors, tell me the exact
  message shown and I'll adjust the model inputs.

STILL TO COME
  Module 3: Standards & Calculations (uses your Anthropic key — I can build next).
  Module 4: Board / Report builder.
