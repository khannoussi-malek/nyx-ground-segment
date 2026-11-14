import { useCallback, useEffect, useState } from "react";
import { logout, me } from "./api";
import { LiveTable } from "./LiveTable";
import { Login } from "./Login";
import { bootUser, checkSession, safeLogout } from "./session";
import { connectEvents } from "./sse";
import { createStore, ingest } from "./store";

function Console({ user, onLogout }: { user: string; onLogout: () => void }) {
  const [store] = useState(createStore);
  useEffect(
    () =>
      connectEvents(
        "/api/events",
        (kind, data) => ingest(store, kind, data),
        () => void checkSession(me, onLogout), // unmounting stops the reconnect loop
      ),
    [store, onLogout],
  );
  return (
    <>
      <header>
        <h1>Nyx</h1>
        <span>{user}</span>
        <button onClick={() => void safeLogout(logout).then(onLogout)}>Log out</button>
      </header>
      <LiveTable store={store} />
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
