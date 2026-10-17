# CAD Lite
A small computer-aided dispatch app for volunteer fire departments and small
dispatch teams. Python (Flask) and PostgreSQL.

What it does:
- Dispatch board. Every call shows on one screen. New calls are highlighted.
- Text-to-speech. When a new call comes in, the computer says the call type,
  the address, and the nature of the emergency out loud. A Read button says a
  call again any time. A Voice ON/OFF button turns it off. It uses the free
  speech built into Chrome, Edge, and Safari, so there is no API key and no
  extra cost. The dispatch computer needs its volume up.
- SOP checklists. Built-in step-by-step SOPs for police, fire, and medical
  calls. The dispatcher checks off steps as they go. An admin can edit every
  SOP on the SOPs page (one step per line).
- Sharing. Each call has a Share button that makes a private read-only link.
  There is also a Share whole log button for the full call log. Only someone
  with the link can read it, so treat the link like a password.
- Export CSV for records.
- Unit member logins. The admin adds unit accounts on the Users page and picks
  which call types each unit responds to (police, fire, medical, other). Units
  log in at the same address and get their own screen: active calls for their
  types, recently closed calls, and a Go on duty / Go off duty button.
- Tone and voice on the unit phone. When dispatch sends a call to the unit
  phones, the unit's phone plays a dispatch two-tone alert, then says the
  address and the nature of the emergency. A Silence button stops it, a Read
  button says a call again. New calls are not sent automatically: the new-call
  form has a "Send the tone and voice alert to unit phones now" checkbox
  (checked by default), and any call not sent yet has a Send to unit phones
  button on the board and on the call page. The dispatch computer itself does
  not say "new fire call" and so on; it only speaks when someone presses Read.
- Unit tracking. A unit's phone shares its location only while that unit is on
  duty. Off duty means no location is stored. Dispatch sees on-duty units on
  the Units page on a live map with a list and how long ago each fix came in.

## Recommend units by location

On a call page there is a "Recommend units by location" button. It looks up the
call address on the free OpenStreetMap geocoder (the result is saved on the
call), then lists every on-duty unit that is sharing its location, closest
first, in straight-line miles. "Add" puts the unit's name into the Units
assigned box.

- A unit shows up only while it is on duty AND its phone has sent a location
  (units share location only while on duty).
- The miles are straight-line, not driving miles; the dispatcher still decides
  who to send.

## New-call flash

On the dispatch board a newly arrived call row flashes amber five times. On a
unit phone, a new matching call flashes its card the same way, then the tone
and voice alert plays.

## Maps and address checking
-------------------------
- While you type an address on the New call page, suggestions appear under the box.
  Pick one and the map check runs automatically: the address box fills in, the
  coordinates are saved with the call, and a map preview shows.
- Suggestions use your Google Maps key when one is set (Settings or the GOOGLE_KEY
  environment variable), and fall back to the free OpenStreetMap geocoder.

Type a house number and street, a city, or cross streets like
"Main St & 4th Ave, Opelika" in the address box. Click "Check address on
the map" and the app looks it up, shows the exact address it found with a
map preview, and remembers it on the call. The call page shows the map and
an "Open in Google Maps" link.

Address checks use Google Maps if you paste a Google Maps key on the
Settings page. Without a key they use the free OpenStreetMap service.

## Delete a call (admin only)

On a call page, admins see a Delete call button. It asks to confirm, then
removes the call and its notes and writes it to the audit log. Dispatchers
and units cannot delete calls.

## Deploy on Railway (new project)
1. Make a new empty project (repo) for this code, like your other Railway apps.
2. In the Railway project, add a PostgreSQL database. Railway creates a
   DATABASE_URL variable for it.
3. Link the app service to that Postgres so DATABASE_URL is set, or paste the
   Postgres DATABASE_URL into the app's variables.
4. Add one more variable: SECRET_KEY with any long random text. Write it down
   and do not change it later, or everyone gets logged out.
5. Deploy. On first open the app shows a First time setup page: make the admin
   username and password there. That page disappears forever after the first
   admin exists.
6. Log in, open Users, and add each dispatcher their own account.

## Daily use
1. A call comes in. Click + New call, pick the type, the nature, type the
   address, pick the priority, and click Enter call.
2. The board reads the new call out loud and highlights the row.
3. Open the call. Follow the SOP checklist, type the units assigned, keep
   adding notes as things happen, and set the status: Open, Dispatched, Closed.
4. Send the call to the unit phones with the Send to unit phones button (the
   new-call form does this by default unless you untick the box).
   Duplicate check: if an active call already has the same type and address,
   the form stops and offers two buttons: "Add this caller to call #N as a
   note" (keeps one call, records the second caller on it) or "Enter as a new
   call anyway" (forces the new call through). Matching ignores capital
   letters and extra spaces.
5. Share a call or the log with a partner agency using the Share buttons.

## Notes
- This is a dispatch aid, not a certified emergency dispatch system. Local 911
  and your mutual aid procedures always come first.
- The checklist follows the current SOP text. If an admin edits an SOP, older
  calls show the new wording on their checklist.
