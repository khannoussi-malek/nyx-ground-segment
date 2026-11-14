import { useCallback, useEffect, useRef, useState } from "react";
import { logout, me, missionDb, type MissionDb } from "./api";
import { armAudio, beep, beepsFor, tone, toneActive, toneKeys, type ToneState } from "./audio";
import { sessionKeySet } from "./keySet";
import { AlarmList } from "./AlarmList";
import { CommandPanel } from "./CommandPanel";
import { LiveTable } from "./LiveTable";
import { Login } from "./Login";
import { bootUser, checkSession, loadMission, safeLogout } from "./session";
import { connectEvents } from "./sse";
import { createStore, ingest, type Store } from "./store";

/** Beeps once per new RED alert key and sounds one tone at each loss-of-monitoring onset (D-UI-08, D-UI-21).
 *  Runs on every store update, never on the 100 ms tick, and once more when a hidden tab is shown (P-pf18). */
function useAudio(store: Store, ctx: AudioContext | null, running: boolean) {
  const ctxRef = useRef(ctx);
  ctxRef.current = ctx;
  const [stat, setStat] = useState({ beeps: 0, lastTone: "" });
  // Keys are marked only while the audio is running, so an onset while it is off stays pending (P-pf23). When it
  // starts running, ONE check sounds what is pending plus any open unacknowledged RED and active monitoring state.
  useEffect(() => {
    const beeped = sessionKeySet("beeps");
    const toned = sessionKeySet("tones", true);
    let prev: ToneState = { active: {}, lastPlayAt: null };
    const check = (arming = false) => {
      const c = ctxRef.current;
      if (!running || !c) return;
      const now = performance.now();
      const fresh = beepsFor(store.alarms(), beeped);
      const next: ToneState = { active: toneActive(store), lastPlayAt: prev.lastPlayAt };
      const { play, keys } = toneKeys(prev, next, now, toned);
      const redOpen = arming && store.alarms().some((x) => x.severity === "RED" && x.state === "UNACK");
      const toneOpen = arming && Object.keys(next.active).length > 0;
      const sound = play || toneOpen;
      prev = { active: next.active, lastPlayAt: sound ? now : prev.lastPlayAt };
      if (fresh.length > 0 || redOpen) beep(c); // one beep per batch: three REDs in one snapshot are one sound
      if (sound) tone(c);
      const last = keys.at(-1);
      if (fresh.length > 0 || last !== undefined)
        setStat((s) => ({ beeps: s.beeps + fresh.length, lastTone: last === undefined ? s.lastTone : `${next.active[last]}:${last}` }));
    };
    check(true);
    const off = store.subscribe(() => check());
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
  }, [store, running]);
  return stat;
}

/** Whether the context is running, followed through its statechange events. */
function useRunning(ctx: AudioContext | null): boolean {
  const [running, setRunning] = useState(ctx?.state === "running");
  useEffect(() => {
    const f = () => setRunning(ctx?.state === "running");
    f();
    ctx?.addEventListener("statechange", f);
    return () => ctx?.removeEventListener("statechange", f);
  }, [ctx]);
  return running;
}

function SoundBadge({ ctx, running, onArm }: { ctx: AudioContext | null; running: boolean; onArm: (c: AudioContext | null) => void }) {
  if (running) return null;
  // a closed or failing context is replaced by a fresh one made in this click: never silently without audio (P-pf24)
  const rearm = () => {
    void ctx?.close().catch(() => {});
    onArm(armAudio());
  };
  const enable = () => (ctx && ctx.state !== "closed" ? void ctx.resume().catch(rearm) : rearm());
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
  const running = useRunning(ctx);
  const { beeps, lastTone } = useAudio(store, ctx, running);
  return (
    <div data-beeps={beeps} data-last-tone={lastTone}>
      <header>
        <h1>Nyx</h1>
        <SoundBadge ctx={ctx} running={running} onArm={setCtx} />
        <span>{user}</span>
        <button onClick={() => void safeLogout(logout).then(onLogout)}>Log out</button>
      </header>
      <AlarmList store={store} mission={mission} user={user} />
      <CommandPanel store={store} mission={mission} />
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
