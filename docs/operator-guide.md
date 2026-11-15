# Operator guide

One page for the person at the console. All times are UTC and end in `Z`, for example `14:03:17Z`.

## Read the console
- One row per parameter and vehicle. A value is **grey when nominal**; colour, text and a glyph appear only for YELLOW or RED. A RED badge on a row that is also STALE shows the last limit as "was RED LOW".
- Next to the value you see its freshness: **OK**, **STALE** (older than 2 s), **FROZEN** (all analog values identical for 2 s or more) or **NO DATA** (no value yet).
- An alarm row says in words why it fired (for example "YELLOW - RATE FALLING -0.40 kPa/s (limit 0.15)") and shows two 8-character hash prefixes. They identify the limits file (`limits`) and the whole mission database (`set`) that were live when it fired; quote them when you report a limit question.
- The event strip under the table lists lost frames, LOS/AOS, sequence resets and mission-db changes, newest first.

## Banners and tones
Banners follow one order, highest first: **NO LIVE CONNECTION** (this browser lost the server) > **GROUND PROCESSING DOWN** (the ground software does not process that vehicle; "- BROKER" on every vehicle when the data broker is down) > **LOSS OF SIGNAL** (no frame for 2 s while the ground is healthy) > **FROZEN** > **STALE**. While a higher banner shows, trust nothing below it: no RED can be raised.
- **Loud beep**: once per new RED alarm. A reload does not repeat it.
- **Lower, slower tone**: once when monitoring is lost (LOS, FROZEN, GROUND PROCESSING DOWN, NO LIVE CONNECTION). It does not repeat and needs no acknowledgement; the banner stays until the state ends. A single STALE value is silent.
- **SOUND OFF** badge: audio is not running. Click anywhere on the page to arm it.

## Acknowledge an alarm
Press ACK on the alarm, type a note (required), confirm. The row shows **ACK PENDING (by you)** until the ground software applies it, normally within 1 s, then ACKED. **ACK NOT APPLIED** means it was not applied: if the text says someone already acknowledged it, nothing more to do; otherwise try once more and tell the test conductor if it repeats. An alarm that returned to normal stays listed as "cleared - not acked" until acknowledged.

## Send a command
Choose the target vehicle yourself (there is no default), the command and its arguments. A **hazardous** command asks for confirmation: the dialog repeats vehicle, command, arguments and the hazard text, wants a reason of at least 10 characters, and enables Confirm only after you type the vehicle ID (for example `NYX-TA2`). The confirmation lasts 60 s. If the console says **duplicate** (the same command was sent by someone within the last 2 s, name and time shown), do not send it again; look at the command list. After a network error the console says "May have been sent - check the command list before sending again": do that first.

## When a command shows FAILED
FAILED is final; what happened is in the outcome:
- **NOT SENT**: it never left the ground. Safe to send again.
- **NOT EXECUTED**: the vehicle refused it. Check why before sending again.
- **EXECUTED LATE** ("vehicle executed it at 14:03:17Z"): it happened after the wait. **Do not resend.**
- **RECEIVED LATE**: the vehicle accepted it but nobody saw it executing yet. Wait and watch the parameters.
- **UNKNOWN**: nothing proves either way. Check the vehicle `mode`, `last_cmd_id` and the affected parameters. Never resend a hazardous command blindly. Tell the test conductor.

## Run a test session
Start: sessions panel, pick the vehicle, name the session, Start. A vehicle has one active session; a second start on it is refused. Stop ends it for everyone. The report is **pending** until the vehicle's data has reached the end of the session (usually seconds); after 30 s it is frozen anyway and marked "incomplete: n of m frames". A frozen report never changes. Download the CSV for sign-off, which happens outside the tool. In a spreadsheet, a cell that begins with `'` is text we protected from being read as a formula; the apostrophe is not part of the value.
