import { useEffect, useRef, useState, type FormEvent } from "react";
import { ackAlarm } from "./api";
import { alarmText, hashText } from "./alarmText";
import type { MissionDb } from "./api";
import type { AlarmView } from "./store";

/** ACK with a required note in a native <dialog>: it shows the alarm, takes the note and keeps its errors inline.
 *  (window.confirm can do none of that and blocks the live page.) One request id per opening: a retry is the same request. */
export function AckDialog({ alarm, mission, opener, onDone, onCancel }: {
  alarm: AlarmView;
  mission: MissionDb | null;
  opener: HTMLElement | null; // gets the focus back when the dialog closes
  onDone: (status: "pending" | "applied" | "superseded" | "rejected", requestId: string) => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [requestId] = useState(() => crypto.randomUUID());
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    return () => {
      d?.close();
      opener?.focus();
    };
  }, [opener]);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const r = await ackAlarm(alarm.alarm_id, requestId, note.trim());
    setBusy(false);
    if (r.ok) onDone(r.status, requestId);
    else setError(r.message);
  }
  return (
    <dialog ref={ref} className="ack" aria-labelledby="ack-title" onCancel={(e) => (busy ? e.preventDefault() : onCancel())}>
      <form onSubmit={(e) => void submit(e)}>
        <h2 id="ack-title">Acknowledge alarm</h2>
        <p className="ack-alarm">
          <strong>{alarm.vehicle}</strong> {alarm.param}
          <br />
          {alarmText(alarm, mission)}
          <br />
          <small>{hashText(alarm)}</small>
        </p>
        <label>
          Note (required)
          <textarea value={note} onChange={(e) => setNote(e.target.value)} required rows={3} autoFocus />
        </label>
        {error && <p role="alert">{error}</p>}
        <div className="actions">
          <button type="button" className="secondary" disabled={busy} onClick={onCancel}>Cancel</button>
          <button type="submit" disabled={busy || note.trim() === ""}>ACK</button>
        </div>
      </form>
    </dialog>
  );
}
