import { useState, type FormEvent } from "react";
import { login } from "./api";

export function Login({ onLogin }: { onLogin: (user: string) => void }) {
  const [error, setError] = useState("");
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    const user = String(f.get("user"));
    try {
      if (await login(user, String(f.get("password")))) onLogin(user);
      else setError("Wrong user or password");
    } catch (err) {
      console.error(err);
      setError("Login failed, try again");
    }
  }
  return (
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
  );
}
