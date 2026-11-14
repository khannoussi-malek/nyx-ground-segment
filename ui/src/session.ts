/** Session logic kept out of the components so it is counted and tested. */
import type { MissionDb } from "./api";

export async function attemptLogin(
  login: (u: string, p: string) => Promise<boolean>,
  user: string,
  password: string,
): Promise<string | null> {
  try {
    return (await login(user, password)) ? null : "Wrong user or password";
  } catch (err) {
    console.error(err);
    return "Login failed, try again";
  }
}

export async function bootUser(me: () => Promise<string | null>): Promise<string | null> {
  try {
    return await me();
  } catch (err) {
    console.error(err);
    return null;
  }
}

export async function safeLogout(logout: () => Promise<void>): Promise<void> {
  try {
    await logout();
  } catch (err) {
    console.error(err); // the session is dropped client-side either way
  }
}

/** Called on every stream error: only a dead session (me -> null) leaves the console. */
export async function checkSession(me: () => Promise<string | null>, onExpired: () => void): Promise<void> {
  try {
    if ((await me()) === null) onExpired();
  } catch (err) {
    console.error(err); // API unreachable: stay and keep reconnecting
  }
}

/** Once after login: a dead session goes back to login; any other failure shows ids only. */
export async function loadMission(
  missionDb: () => Promise<MissionDb | null>,
  onExpired: () => void,
): Promise<MissionDb | null> {
  try {
    const m = await missionDb();
    if (m === null) onExpired();
    return m;
  } catch (err) {
    console.error(err); // live values never wait for it
    return null;
  }
}
