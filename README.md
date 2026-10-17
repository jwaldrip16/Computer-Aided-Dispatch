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
- SOP checklists. Built-in step-by-step SOPs for police, fire, medical, and
  traffic accident calls
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

## Unit buttons and tones
----------------------
- Each active call on the unit app has Enroute, On scene, Transport, and Clear
  buttons. Clear opens a reason box (not required); the answer shows on the call
  page in dispatch.
- Navigate opens Google Maps directions from the unit's current location to the
  scene.
- The app records the unit's GPS fix with each button press and shows the miles
  traveled on the call page (straight-line between fixes, so treat it as close
  to, not exactly, road miles).
- Tones differ by call type: police is a two-tone hi/lo, fire is fast beeps,
  medical is three low beeps. A "Traffic accident w/ injuries" call is both a
  police and a medical call: its tone plays the police two-tone and the medical
  beeps, and the voice says "Traffic accident with injuries".
  medical is slow low beeps, other is the original two-tone. An emergency
  priority call sounds one extra round. The spoken announcement plays twice,
  then the voice says the time.

Editing and deleting users
--------------------------
- On the Users page, every user has an Edit link (change the username, set a new
  password, switch the role, and for units change which call types they respond
  to) and a Delete button that asks "Sure?" before it acts. You cannot delete
  your own account or the last admin.

Dispatch can set unit statuses
------------------------------
- On the call page, dispatch can pick a unit and set Enroute, On scene,
  Transport, or Clear (with an optional reason) for them, for when the unit
  calls it in by radio instead of using the app. It saves a note on the call.

Reset call numbers
------------------
- In Settings, admins can reset call numbering so the next call is #1. This
  deletes every existing call and its notes, so use it to clear out training
  calls before going live.

Maps and address checking
-------------------------
- While you type an address on the New call page, suggestions appear under the box.
  Pick one and the map check runs automatically: the address box fills in, the
  coordinates are saved with the call, and a map preview shows.
- Suggestions use your Google Maps key when one is set (the GOOGLE_KEY
  environment variable), and fall back to the free OpenStreetMap geocoder.

Type a house number and street, a city, or cross streets like
"Main St & 4th Ave, Opelika" in the address box. Click "Check address on
the map" and the app looks it up, shows the exact address it found with a
map preview, and remembers it on the call. The call page shows the map and
an "Open in Google Maps" link.

Address checks use Google Maps if the GOOGLE_KEY variable is set on
Railway. Without it
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

## Pending queue

Not every call can go out right away. If no unit is available, the call waits in a
pending queue until dispatch is ready.

- New call form: "Enter call" sends it the normal way; "Send to pending queue" files it
  in the queue instead (no tone plays, nothing goes to unit phones yet).
- The dispatch board has a "Pending queue" section. Open calls and dispatched calls also
  have a Queue button that moves them there.
- Pressing "Send to units" on a call with no units assigned moves the call to the pending
  queue automatically and writes a note ("No units available; call is pending."). Nothing
  is sent to phones until units are assigned.
- Assigning happens in the pending queue. Each pending row has a unit box and an
  Assign button (or open the call and use the units box and the one-tap unit buttons).
  Assigning does not page the phones yet.
- To remove a unit before the call is sent, tap the X next to its name on the
  pending row. Editing the box and pressing Assign also works.
- When you are ready, press "Send to unit phones" (on the call page, or "Send to
  units" on the board row). The call moves to Dispatched and the phones get the tone
  and voice alert. Units never see pending calls.
- On an active (dispatched) call you can add more units: each active row has an
  "Add units" box, and the call page units box still works. You can also remove a
  unit there: tap the X next to its name on the active row. Any unit change on a
  dispatched call re-pages the assigned units automatically (tone + voice again).
  Each active row also has a "Re-page" button that makes every unit on the call
  hear the call again.
- If you remove every unit from an active call, the call goes back to the pending
  queue on its own (a note is written, and unit phones drop the call).
- The units list spells each unit's status out in words next to the name
  (on duty / off duty), so dispatch never has to guess from colors alone.
- On the unit phone, each call shows the status of every unit on the call
  (enroute, on scene, and so on) next to their name, and the Navigate button
  opens Google Maps turn-by-turn directions straight to the call address.
- Response channel: an optional field on every call (police, fire, medical,
  other) for the radio channel the units should respond on. It shows on the
  dispatcher board, the call page, and the unit phone, and the unit voice says
  "Respond on <channel>" after the nature, in both passes. Leave it empty and
  the voice says nothing about it.
- The dispatcher board shows each assigned unit's live response status on the
  call row ("enroute", "on scene", "transporting", "clear") and updates it on
  its own as units report in.
- Every page is also recorded as an MP3 that matches the page the units
  hear: it starts with the same tone-out the phones play (per call type,
  four cycles on emergency priority), then the full spoken wording (type,
  address, nature, response channel, repeat) and the Central time in
  24-hour form. The call page's "Pages to units" list has a play button
  and a Save link for each recording; a page whose recording failed still
  goes out and stays recorded as text.
- Calls in the pending queue can always be assigned by typing unit names
  and sent with "Send to units", whether they were never sent or were
  queued back from dispatched; adding units to a queued call never pages.
- When the app starts it fills in recordings for older pages that have
  none: the full recording, tone-out plus voice, is generated from the
  call's details, so every page in the call history can be played and
  saved. (Up to the newest 200 pages per start.)
- The call page shows its times in Central time: Came in, Sent to unit
  phones, each line of the "Pages to units" list, and the notes.
- Every page to the units is recorded on the call page ("Pages to units"):
  the time and the units that were paged, including automatic re-pages when
  units change. The page voice is spoken by each unit phone, so there is no
  audio file stored, but any unit can tap "Hear page again" on the call card
  to hear the same page spoken again.
- A second call at the same address is caught as a duplicate even when it is
  typed a little differently (extra spaces, no comma, "Second Ave" instead of
  "2nd Ave"): the check compares the address loosely and also compares map
  points. Closed calls are never duplicates. Send anyway from the duplicate
  screen when it really is a new call.
- Type a business name (like a church or a school) in the address box and the
  app looks it up on the map itself. The call gets the map point, and the
  address is stored as the business name plus the street and city, so the
  unit voice says both the name and the address. This works when the map
  knows the business; a street address typed normally is never rewritten.
- Dispatch can put a unit on duty or off duty from the board's units list
  (Put on duty / Send off duty next to each unit). Sending a unit off duty
  clears its location, the same as when the unit does it from its own phone.
- Off-duty units cannot be added to a call, in the pending queue or the active queue.
  The board tells you which unit is off duty. Removing a unit always works, even if
  that unit is now off duty. Nearest only suggests on-duty units.

## Lee County addresses

The dispatch area is Lee County, Alabama.

- Address suggestions only fill in Lee County addresses. Google or the free OpenStreetMap
  geocoder is checked for the county before a suggestion is shown.
- "Check address on the map" retries with the county added, which helps rural addresses
  like "1085 Lee County Road 166". The free map service (used when no Google key is set)
  does not carry most county-road addresses; Google does.
- A call can be entered and sent even when the map cannot verify the address. The typed
  address is used as-is for the voice alert.

## Changing the address or caller

On a call page, staff have a "Change address or caller" card: fix the address (with a map
check), and set the caller name and call back phone. Changes are written into the call
notes.

## Units on and off duty

The dispatch board shows every unit account in two rows: On duty (green) and Off duty.
On-duty units also show when their last location came in. It refreshes with the board.

## Assign the nearest responder

On any call page (open, pending, or dispatched) there are two buttons above the units
box:

- "Recommend units by location" lists on-duty units closest first, straight-line miles,
  and an Add button per unit. If the address is not on the map, the list still comes up
  with "distance unknown".
- "Assign nearest" picks the closest on-duty unit in one click and saves it as the
  assigned unit.

On the dispatch board, every call that is not closed (open, pending, or dispatched) has
a Nearest button right on the row. It assigns the closest on-duty unit to that call
without opening it. Then press Send to units to dispatch.

## Clearing a call

Every row on the dispatch board has a Clear button that closes the call (open, pending,
or dispatched). Closed calls move to the Recent section. The call page also has a Closed
status button. Closed calls stop appearing on unit phones.

## Troubleshooting: a unit is not getting calls

A unit phone only shows calls that match ALL of these. Check in this order:

1. The unit account must have at least one call type picked (police, fire, medical,
   other). The Users page now refuses to save a unit without one, and the unit phone
   shows a red warning when its account has none.
2. The call must be dispatched, not pending. Pending calls never go to unit phones.
   Assign units (Nearest or by hand), then press "Send to units". Assigning alone does
   not send anything.
3. Your unit must be on the call's Units list. Units see only the calls they are
   assigned to, not every call of their type. When dispatch removes your unit from a
   call, the call drops off your phone.
4. The unit phone must be signed in. If the session ran out, the page goes back to the
   login screen; sign in again and re-arm the sound.

## Re-paging units after a reassign

Every unit assigned to a call tones and hears the call when dispatch first presses
Send to unit phones. If dispatch later changes the units on the call (reassigns), the
new unit hears the page only when someone presses **Re-page units (tone + voice
again)** on the call page. Re-page makes every unit on the call tone and hear the call
again, so it also works when someone missed the first page. Closed calls cannot be
re-paged.

## Activity history

The Activity history link at the top shows the latest 200 things that happened:
who created, sent, queued, closed or deleted calls, changed or removed units,
re-paged, set unit statuses, and when units went on or off duty. Dispatchers and
admins can open it, and each line links to the call it is about.
