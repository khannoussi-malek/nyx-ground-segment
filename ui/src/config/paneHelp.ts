import type { PaneId } from "./layout";

/** The note behind each pane's info button: what the pane shows and what the operator can do there, in the
 *  console's own words (D-UI-06 vocabulary). Kept short: the operator guide has the full story. */
export const PANE_HELP: Readonly<Record<PaneId, string>> = {
  alarms:
    "Every limit or rate alarm the ground raised, RED first. A row stays until someone acknowledges it, even after the value is back inside its limits (RETURNED). ACK opens a dialog that needs a note. Filter by vehicle, severity or state. Click a parameter to plot it.",
  live:
    "Live telemetry, one card per vehicle and one row per parameter: name, value, unit and limit gauge. Grey means nominal; colour and a badge appear only for YELLOW or RED. STALE: no new value for 2 s. FROZEN: values stopped changing. NO DATA: nothing yet. On the gauge, striped zones are the yellow and red ranges and the pointer is the value. Click a name to plot it.",
  chart:
    "One parameter over time. The line is the value, the grey band its min and max, the dashed lines its limits. Hover to read a value; drag across the plot to zoom in; −/+ or Ctrl/⌘ + scroll to zoom; ‹ Older, Newer ›, Shift + scroll or a sideways swipe to move in time. HISTORY stops following the clock until you press Live.",
  commands:
    "Send a command: pick the vehicle and the command, fill its arguments, give a reason, Send. A hazardous command asks you to type the vehicle ID before it goes. Below, each command shows when it was QUEUED, SENT, RECEIVED and VERIFIED, and if it FAILED, whether it is safe to send again. Show and Search filter the list.",
  sessions:
    "A session records one test pass on one vehicle. Name it and press Start session; press Stop when the pass is over. Its report (frames, alarms, commands) is ready a few seconds later; Report opens it and Download CSV saves it for sign-off. Show and Search filter the list.",
  events:
    "What happened on the ground link, newest first: loss and return of signal (LOS, AOS) with the frames lost, sequence resets, and mission-database changes applied or rejected. Show and Search filter the list.",
};
