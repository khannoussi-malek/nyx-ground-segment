import { useEffect, useState } from "react";
import { logout, me } from "./api";
import { LiveTable } from "./LiveTable";
import { Login } from "./Login";
import { connectEvents } from "./sse";
import { createStore, ingest } from "./store";

function Console({ user, onLogout }: { user: string; onLogout: () => void }) {
  const [store] = useState(createStore);
  useEffect(() => connectEvents("/api/events", (kind, data) => ingest(store, kind, data)), [store]);
  async function out() {
    try {
      await logout();
    } catch (err) {
      console.error(err); // the session is dropped client-side either way
    }
    onLogout();
  }
  return (
    <>
      <header>
        <h1>Nyx</h1>
        <span>{user}</span>
        <button onClick={out}>Log out</button>
      </header>
      <LiveTable store={store} />
    </>
  );
}

export function App() {
  const [user, setUser] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    me().then(setUser, (err) => {
      console.error(err);
      setUser(null);
    });
  }, []);
  if (user === undefined) return null;
  return user === null ? <Login onLogin={setUser} /> : <Console user={user} onLogout={() => setUser(null)} />;
}
