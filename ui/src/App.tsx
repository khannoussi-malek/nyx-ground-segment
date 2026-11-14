import { useCallback, useEffect, useState } from "react";
import { logout, me, missionDb, type MissionDb } from "./api";
import { LiveTable } from "./LiveTable";
import { Login } from "./Login";
import { bootUser, checkSession, loadMission, safeLogout } from "./session";
import { connectEvents } from "./sse";
import { createStore, ingest } from "./store";

function Console({ user, onLogout }: { user: string; onLogout: () => void }) {
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
  return (
    <>
      <header>
        <h1>Nyx</h1>
        <span>{user}</span>
        <button onClick={() => void safeLogout(logout).then(onLogout)}>Log out</button>
      </header>
      <main>
        <LiveTable store={store} mission={mission} />
      </main>
    </>
  );
}

export function App() {
  const [user, setUser] = useState<string | null | undefined>(undefined);
  useEffect(() => void bootUser(me).then(setUser), []);
  const out = useCallback(() => setUser(null), []);
  if (user === undefined) return null;
  return user === null ? <Login onLogin={setUser} /> : <Console user={user} onLogout={out} />;
}
