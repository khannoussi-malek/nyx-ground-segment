import { useEffect, useRef, useState } from "react";
import { AMBIGUOUS_TEXT, confirmEnabled, rejectedText } from "./commandText";
import { sendCommand, type CommandRequest } from "./api";

const DEAD_TOKEN = new Set(["CONFIRMATION_INVALID"]);

/** Hazardous command confirm in a native <dialog> (never window.confirm): shows what will be sent and why it is
 *  hazardous, and enables Confirm only when the operator has typed the vehicle id and a reason (D-UI-20). */
export function ConfirmDialog({ req, hazard, token, expiresInS, initialReason, opener, onSent, onAmbiguous, onCancel }: {
  req: CommandRequest;
  hazard: string;
  token: string;
  expiresInS: number;
  initialReason: string;
  opener: HTMLElement | null; // gets the focus back when the dialog closes
  onSent: () => void;
  onAmbiguous: () => void; // the confirm may have gone through: the panel refreshes its list
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [typed, setTyped] = useState("");
  const [reason, setReason] = useState(initialReason);
  const [error, setError] = useState("");
  const [dead, setDead] = useState(false); // the token cannot be used again: cancel and start over
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false); // two clicks in one task must not send twice
  const ambiguous = useRef(false); // once the outcome is unknown nothing may say "send again"
  const closing = useRef(false); // our own close() on unmount is not a native close
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    // one-shot: the server enforces the expiry, this only tells the operator before they type into a dead dialog
    const t = setTimeout(() => {
      setDead(true);
      // while the confirm is in flight or its outcome unknown, "send again" would be the wrong advice
      if (!ambiguous.current && !inFlight.current) setError("Confirmation expired \u2013 cancel and send the command again");
    }, expiresInS * 1000);
    return () => {
      clearTimeout(t);
      closing.current = true;
      d?.close();
      opener?.focus();
    };
  }, [expiresInS, opener]);
  async function confirm() {
    // a programmatic submit must not get past what the button enforces
    // (the refs, not only `dead`: after an ambiguous result this handler runs once more before React re-renders)
    if (inFlight.current || ambiguous.current || dead || !confirmEnabled(typed, req.vehicle, reason)) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    const r = await sendCommand({ ...req, confirm_token: token, reason: reason.trim() });
    inFlight.current = false;
    setBusy(false);
    if (r.kind === "accepted") return onSent();
    if (r.kind === "rejected" && r.ambiguous) {
      ambiguous.current = true;
      setDead(true);
      setError(AMBIGUOUS_TEXT);
      return onAmbiguous();
    }
    if (r.kind === "confirm") {
      setDead(true);
      return setError("The server asked for a new confirmation \u2013 cancel and send the command again");
    }
    setError(rejectedText(r));
    if (DEAD_TOKEN.has(r.error)) setDead(true);
  }
  const args = Object.entries(req.args).map(([k, v]) => `${k} = ${v}`).join(", ");
  return (
    <dialog ref={ref} className="confirm" aria-labelledby="confirm-title" onCancel={(e) => (busy ? e.preventDefault() : onCancel())}
      onClose={() => !closing.current && onCancel()}>
      <form onSubmit={(e) => { e.preventDefault(); void confirm(); }}>
        <h2 id="confirm-title">Hazardous command</h2>
        <p>
          <strong>{req.vehicle}</strong> {req.name}
          {args && <> ({args})</>}
        </p>
        <p className="hazard" role="note">{hazard}</p>
        <label>
          Reason (at least 10 characters)
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={2} />
        </label>
        <label>
          Type {req.vehicle} to confirm
          <input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} />
        </label>
        {error && <p role="alert">{error}</p>}
        <div className="actions">
          <button type="button" className="secondary" disabled={busy} onClick={onCancel}>Cancel</button>
          <button type="submit" disabled={busy || dead || !confirmEnabled(typed, req.vehicle, reason)}>Confirm</button>
        </div>
      </form>
    </dialog>
  );
}
