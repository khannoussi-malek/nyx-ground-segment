import { useEffect, useRef, useState, type FormEvent } from "react";
import { ackAlarm } from "../server/api";
import { alarmText, hashText } from "../tools/alarmText";
import type { MissionDb } from "../server/api";
import type { AlarmView } from "../state/store";

/** The operator must re-read an alarm whose event, severity or state moved on (or that cleared) since the dialog opened. */
const changed = (seen: AlarmView, now: AlarmView | undefined): boolean =>
  !now || now.last.key !== seen.last.key || now.severity !== seen.severity || now.state !== seen.state;

/** ACK with a required note in a native <dialog>: it shows the alarm, takes the note and keeps its errors inline.
 *  (window.confirm can do none of that and blocks the live page.) One request id and one seen_key per opening: a retry
 *  is the same request. The ACK binds to what the operator saw (P-pf42/42b/43): `alarm` is the alarm as it was when the
 *  dialog opened, `current` the live one; a change closes the dialog (onChanged) unless a request is in flight (then
 *  the owner's seen_key check answers). `live` false (stale screen) disables the ACK. */
export function AckDialog({ alarm, current, live, mission, opener, onDone, onCancel, onChanged }: {
  alarm: AlarmView;
  current: AlarmView | undefined; // undefined once the alarm cleared
  live: boolean;
  mission: MissionDb | null;
  opener: HTMLElement | null; // gets the focus back when the dialog closes
  onDone: (status: "pending" | "applied" | "superseded" | "rejected", requestId: string) => void;
  onCancel: () => void;
  onChanged: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [requestId] = useState(() => crypto.randomUUID());
  const [seen] = useState(alarm); // captured at open: never re-read at submit (P-pf43b rule 1)
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false); // two submits in one task send once
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    return () => {
      d?.close();
      opener?.focus();
    };
  }, [opener]);
  const moved = changed(seen, current);
  useEffect(() => {
    if (moved && !busy) onChanged();
  }, [moved, busy, onChanged]);
  async function submit(e: FormEvent) {
    e.preventDefault();
    // a programmatic submit must not get past what the disabled button enforces
    if (inFlight.current || note.trim() === "" || !live) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    const r = await ackAlarm(seen.alarm_id, requestId, note.trim(), seen.last.key);
    inFlight.current = false;
    setBusy(false);
    if (r.ok) onDone(r.status, requestId);
    else setError(r.message);
  }
  return (
    <dialog ref={ref} className="ack" aria-labelledby="ack-title" onCancel={(e) => (busy ? e.preventDefault() : onCancel())}>
      <form onSubmit={(e) => void submit(e)}>
        <h2 id="ack-title">Acknowledge alarm</h2>
        <p className="ack-alarm">
          <strong>{seen.vehicle}</strong> {seen.param}
          <br />
          {alarmText(seen, mission)}
          <br />
          <small>{hashText(seen)}</small>
        </p>
        <label>
          Note (required)
          <textarea value={note} onChange={(e) => setNote(e.target.value)} required rows={3} autoFocus />
        </label>
        {error && <p role="alert">{error}</p>}
        <div className="actions">
          <button type="button" className="secondary" disabled={busy} onClick={onCancel}>Cancel</button>
          <button type="submit" disabled={busy || !live || note.trim() === ""}>ACK</button>
        </div>
      </form>
    </dialog>
  );
}
