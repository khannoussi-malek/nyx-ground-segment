import { useCallback, useEffect, useRef, useState } from "react";
import { logout, me, missionDb, type MissionDb } from "./api";
import { armAudio, beep, beepsFor, tone, toneActive, toneKeys, type ToneState } from "./audio";
import { sessionKeySet } from "./keySet";
import { LiveTable } from "./LiveTable";
import { Login } from "./Login";
import { bootUser, checkSession, loadMission, safeLogout } from "./session";
import { connectEvents } from "./sse";
import { createStore, ingest, type Store } from "./store";

/** Beeps once per new RED alert key and sounds one tone at each loss-of-monitoring onset (D-UI-08, D-UI-21).
 *  Runs on every store update, never on the 100 ms tick, and once more when a hidden tab is shown (P-pf18). */
function useAudio(store: Store, ctx: AudioContext | null) {
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;
  const [stat, setStat] = useState({ beeps: 0, lastTone: "" });
  useEffect(() => {
    const beeped = sessionKeySet("beeps");
    const toned = sessionKeySet("tones");
    let prev: ToneState = { active: {}, lastPlayAt: null };
    const check = () => {
      const now = performance.now();
      const fresh = beepsFor(store.alarms(), beeped);
      const next: ToneState = { active: toneActive(store), lastPlayAt: prev.lastPlayAt };
      const { play, keys } = toneKeys(prev, next, now, toned);
      prev = { active: next.active, lastPlayAt: play ? now : prev.lastPlayAt };
      const c = ctxRef.current;
      if (c && fresh.length > 0) beep(c); // one beep per batch: three REDs in one snapshot are one sound
      if (c && play) tone(c);
      const last = keys.at(-1);
      if (fresh.length > 0 || last !== undefined)
        setStat((s) => ({ beeps: s.beeps + fresh.length, lastTone: last === undefined ? s.lastTone : `${next.active[last]}:${last}` }));
    };
    check();
    const off = store.subscribe(check);
    const onVisible = () => {
      if (document.visibilityState === "visible") {
        store.recheck();
        check();
      }
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      off();
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [store]);
  return stat;
}

function SoundBadge({ ctx, onArm }: { ctx: AudioContext | null; onArm: (c: AudioContext | null) => void }) {
  const [, redraw] = useState(0);
  useEffect(() => {
    const f = () => redraw((n) => n + 1);
    ctx?.addEventListener("statechange", f);
    return () => ctx?.removeEventListener("statechange", f);
  }, [ctx]);
  if (ctx?.state === "running") return null;
  const enable = () => (ctx ? void ctx.resume().catch(() => {}) : onArm(armAudio()));
  return (
    <button className="sound-off" onClick={enable}>
      SOUND OFF – click to enable
    </button>
  );
}

function Console({ user, audio, onLogout }: { user: string; audio: AudioContext | null; onLogout: () => void }) {
  const [ctx, setCtx] = useState(audio);
  const [store] = useState(createStore);
  const [mission, setMission] = useState<MissionDb | null>(null);
  useEffect(() => {
    let mounted = true; // a logout before the answer must not set state on an unmounted console
    void loadMission(missionDb, onLogout).then((m) => mounted && setMission(m));
    return () => {
      mounted = false;
    };
  }, [onLogout]);
  useEffect(() => {
    const stop = connectEvents(
      "/api/events",
      (kind, data) => ingest(store, kind, data),
      () => {
        store.noteError();
        void checkSession(me, onLogout);
      },
    ); // unmounting stops the reconnect loop and the silence timer
    return () => {
      stop();
      store.stop();
    };
  }, [store, onLogout]);
  const { beeps, lastTone } = useAudio(store, ctx);
  return (
    <div data-beeps={beeps} data-last-tone={lastTone}>
      <header>
        <h1>Nyx</h1>
        <SoundBadge ctx={ctx} onArm={setCtx} />
        <span>{user}</span>
        <button onClick={() => void safeLogout(logout).then(onLogout)}>Log out</button>
      </header>
      <main>
        <LiveTable store={store} mission={mission} />
      </main>
    </div>
  );
}

export function App() {
  const [user, setUser] = useState<string | null | undefined>(undefined);
  const [audio, setAudio] = useState<AudioContext | null>(null); // only a login click can arm it; a reload shows SOUND OFF
  useEffect(() => void bootUser(me).then(setUser), []);
  const out = useCallback(() => setUser(null), []);
  if (user === undefined) return null;
  return user === null ? (
    <Login
      onLogin={(u, ctx) => {
        setAudio(ctx);
        setUser(u);
      }}
    />
  ) : (
    <Console user={user} audio={audio} onLogout={out} />
  );
}
