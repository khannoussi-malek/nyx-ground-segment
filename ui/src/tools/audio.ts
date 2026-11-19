import type { KeySet } from "../state/keySet";
import type { AlarmView, Store } from "../state/store";

/** Create and resume the AudioContext; call it inside a click (autoplay policy, D-UI-08). null = no audio. */
export function armAudio(): AudioContext | null {
  try {
    const Ctor = globalThis.AudioContext ?? (globalThis as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return null;
    const ctx = new Ctor();
    void ctx.resume().catch(() => {}); // a refused resume leaves state "suspended": the SOUND OFF badge shows
    return ctx;
  } catch {
    return null;
  }
}

function play(ctx: AudioContext, type: OscillatorType, hz: number, gain: number, attack: number, total: number): void {
  const t = ctx.currentTime;
  const osc = ctx.createOscillator();
  const amp = ctx.createGain();
  osc.type = type;
  osc.frequency.value = hz;
  amp.gain.setValueAtTime(0, t);
  amp.gain.linearRampToValueAtTime(gain, t + attack);
  amp.gain.linearRampToValueAtTime(0, t + total);
  osc.connect(amp).connect(ctx.destination);
  osc.start(t);
  osc.stop(t + total);
}

/** RED alert: 880 Hz square, 10 ms attack, 600 ms (urgent, abrupt). */
export const beep = (ctx: AudioContext): void => play(ctx, "square", 880, 0.2, 0.01, 0.6);
/** Loss of monitoring: 440 Hz sine, 200 ms linear attack, 800 ms (less urgent: lower, slower onset, D-UI-21). */
export const tone = (ctx: AudioContext): void => play(ctx, "sine", 440, 0.2, 0.2, 0.8);

/** Keys of alert transitions not beeped yet; marks them beeped. Snapshots and deltas alike (D-UI-18). */
export function beepsFor(alarms: readonly AlarmView[], seen: KeySet): string[] {
  return alarms.filter((a) => a.last.alert && seen.add(a.last.key)).map((a) => a.last.key);
}

const TONE_COALESCE_MS = 1000;
/** active: onset key -> state kind. lastPlayAt: when the last tone sounded. */
export type ToneState = { active: Readonly<Record<string, string>>; lastPlayAt: number | null };

/** Onsets between two states. Keys of the form `<vehicle>|GPD` or `*|BROKER` end with their state and are forgotten,
 *  so the next onset sounds again; event keys (LOS, FROZEN) are unique per onset and stay (ruling R-n). */
export function toneKeys(
  prev: ToneState,
  next: ToneState,
  now: number,
  seen?: KeySet,
): { play: boolean; keys: string[] } {
  for (const k of Object.keys(prev.active)) if (!(k in next.active) && k.includes("|")) seen?.delete(k);
  const keys = Object.keys(next.active).filter((k) => !(k in prev.active) && (seen?.add(k) ?? true));
  const play = keys.length > 0 && (prev.lastPlayAt === null || now - prev.lastPlayAt >= TONE_COALESCE_MS);
  return { play, keys };
}

/** The monitoring states that deserve a tone, from what the server and the link said. No clock: LOS and FROZEN come
 *  from the server's link events, NO LIVE CONNECTION from the store's silence timer, so a store update is enough. */
export function toneActive(store: Store): Record<string, string> {
  const link = store.link();
  if (!link.connected) return store.hasSnapshot() ? { "*|NLC": "NLC" } : {}; // before the first snapshot there is nothing to lose
  if (link.broker === "down") return { "*|BROKER": "BROKER" };
  const active: Record<string, string> = {};
  for (const v of store.vehicles()) {
    if (link.processing[v] === false) {
      active[`${v}|GPD`] = "GPD";
      continue;
    }
    const lastLink = store.lastLink(v);
    if (lastLink?.kind === "LOS") active[lastLink.key] = "LOS";
    else if (store.vehicle(v)?.frozen) {
      const f = store.lastFrozen(v);
      if (f) active[f.key] = "FROZEN";
    }
  }
  return active;
}
