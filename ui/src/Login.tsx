import { useState, type FormEvent } from "react";
import { login } from "./api";
import { armAudio } from "./audio";
import { attemptLogin } from "./session";

export function Login({ onLogin }: { onLogin: (user: string, audio: AudioContext | null) => void }) {
  const [error, setError] = useState("");
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const audio = armAudio(); // inside the click, before any await: autoplay policy (D-UI-08)
    const f = new FormData(e.currentTarget);
    const user = String(f.get("user"));
    const failure = await attemptLogin(login, user, String(f.get("password")));
    if (failure === null) onLogin(user, audio);
    else {
      void audio?.close().catch(() => {}); // browsers cap live contexts; a failed login must not leak one
      setError(failure);
    }
  }
  return (
    <main className="login">
      <form onSubmit={submit}>
        <h1>Nyx</h1>
        <label>
          User <input name="user" autoComplete="username" required />
        </label>
        <label>
          Password <input name="password" type="password" autoComplete="current-password" required />
        </label>
        <button type="submit">Log in</button>
        {error && <p role="alert">{error}</p>}
      </form>
    </main>
  );
}
