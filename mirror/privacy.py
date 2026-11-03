"""The privacy notice page. It describes what the code actually does, so keep it in step
with the code when behaviour changes. The operator's name and contact come from the
environment: they are facts about whoever runs the service, not something to invent.

This is a plain-language notice, not legal advice. Have it reviewed before launch,
especially for Korea (PIPA), which expects a notice in Korean.
"""

from __future__ import annotations

import html
import os

UPDATED = "2026-11-03"


def render() -> str:
    operator = os.environ.get("MIRROR_OPERATOR_NAME", "").strip()
    contact = os.environ.get("MIRROR_CONTACT_EMAIL", "").strip()
    e = html.escape
    who = (f"<p>This service is run by <b>{e(operator)}</b>."
           + (f" Contact: <b>{e(contact)}</b>." if contact else "") + "</p>"
           if operator else
           "<p>The operator of this service has not published their name or contact "
           "details yet.</p>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mirror privacy notice</title>
<style>
:root {{ --bg:#f6f8f7; --ink:#14201e; --muted:#566663; --line:#dbe3e1; --accent:#0f766e; color-scheme: light; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0e1514; --ink:#e6efed; --muted:#9db0ac; --line:#243230; --accent:#2dd4bf; color-scheme: dark; }} }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif; }}
main {{ max-width:720px; margin:0 auto; padding:40px 20px 80px; }}
h1 {{ font-size:28px; margin:0 0 4px; letter-spacing:-.02em; }}
h2 {{ font-size:18px; margin:32px 0 6px; }}
p, li {{ color:var(--ink); }} .muted {{ color:var(--muted); font-size:14px; }}
ul {{ padding-left:20px; }} a {{ color:var(--accent); }}
table {{ border-collapse:collapse; width:100%; font-size:14px; }}
td, th {{ text-align:left; padding:8px 10px; border-top:1px solid var(--line); vertical-align:top; }}
</style></head><body><main>
<h1>Privacy notice</h1>
<p class="muted">Last updated {UPDATED}. <a href="/">Back to Mirror</a></p>
{who}

<h2>What Mirror stores about you</h2>
<ul>
<li><b>Your account:</b> the identifier and email address given to us by the sign-in you choose (Google, Meta or an email link). If you continue without an account, Mirror creates an anonymous account tied to your browser and stores no email address; clearing your browser's site data makes it unreachable.</li>
<li><b>What you add:</b> the text of every memory you save, or that a tool you connected saves for you, with its date.</li>
<li><b>What Mirror works out from it:</b> things, places and situations that appear in your memories, preferences, decision criteria, writing style, and hypotheses (patterns that repeat), each linked to the memories that support it. Hypotheses about relationships and about self-understanding are marked internal-only.</li>
<li><b>Your feedback:</b> corrections you make, and understandings you delete so they do not come back.</li>
<li><b>API tokens you create:</b> only a one-way hash and the first characters, plus when it was last used.</li>
</ul>
<p>Mirror does not label your personality or diagnose anything. Everything it shows you comes with the memories behind it.</p>

<h2>Why, and who else sees it</h2>
<p>Your data is used to show you your own map, answer your questions, and give your connected tools context that you asked for. It is not sold and not shown to other users.</p>
<table>
<tr><th>Service</th><th>What it handles</th></tr>
<tr><td>Supabase</td><td>Database and sign-in. All of the data above is stored there.</td></tr>
<tr><td>Server host</td><td>Runs the Mirror application.</td></tr>
<tr><td>Meta (Model API)</td><td>Receives the text of a memory, or a question you ask, so a language model can extract patterns or write an answer. Meta handles it under its own terms, which we do not control.</td></tr>
<tr><td>Google or Meta sign-in</td><td>Confirms who you are when you sign in with them.</td></tr>
</table>

<h2>In your browser</h2>
<p>Your sign-in session is kept in your browser's local storage so you stay signed in. Mirror sets no advertising or tracking cookies.</p>
<p>The page loads its fonts, icons, graph drawing and sign-in code from public content networks (jsDelivr and unpkg). Like any website you visit, they can see your IP address and browser details when your browser fetches those files.</p>

<h2>Your choices</h2>
<ul>
<li><b>See and take your data:</b> choose <i>Download my data</i> in the menu to get everything in one JSON file.</li>
<li><b>Correct it:</b> mark any hypothesis as wrong, delete a memory, or delete an understanding. Deleted understandings are remembered by a fingerprint only, so Mirror does not rebuild them.</li>
<li><b>Delete it:</b> choose <i>Delete my data</i> in the menu. Your memories, patterns, tokens and waiting jobs are erased immediately. When the server is set up to do so, your account is removed too. Copies in the database provider's backups can remain for a limited time before they expire.</li>
<li><b>Stop a tool:</b> revoke its token on the Connect page and it stops working at once.</li>
</ul>

<h2>How long we keep it</h2>
<p>Until you delete it. Nothing is deleted automatically while your account exists.</p>
</main></body></html>"""
