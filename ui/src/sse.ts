export const REOPEN_MS = 1000;
/** A reopen that failed before it opened (the api is still down) retries this soon (UI-07). */
export const RETRY_MS = 250;
export const KINDS = ["hello", "snapshot", "tm", "alarm", "cmd", "link", "ground", "hb"] as const;
export type Kind = (typeof KINDS)[number];

/**
 * EventSource with a client watchdog (D-UI-03): on error close and reopen after 1 s, one pending at a time.
 * A source that never opened retries after RETRY_MS: waiting a full second per refused attempt put up
 * to 1 s between the api listening again and the console reconnecting (UI-07).
 */
export function connectEvents(
  url: string,
  onEvent: (kind: Kind, data: unknown) => void,
  onError: () => void,
  deps = {
    EventSource: globalThis.EventSource,
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
  },
): () => void {
  // bare calls: a browser's setTimeout throws "Illegal invocation" when called as deps.setTimeout(...)
  const { setTimeout, clearTimeout } = deps;
  let es: EventSource | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;
  const open = () => {
    timer = undefined;
    if (stopped) return;
    const src = new deps.EventSource(url);
    es = src;
    let opened = false;
    src.addEventListener("open", () => {
      opened = true;
    });
    for (const kind of KINDS) {
      src.addEventListener(kind, (e) => {
        let data: unknown;
        try {
          data = JSON.parse((e as MessageEvent<string>).data);
        } catch (err) {
          console.warn(`dropping malformed ${kind} event`, err);
          return;
        }
        onEvent(kind, data); // outside the try: handler bugs must surface
      });
    }
    src.addEventListener("error", () => {
      src.close();
      timer ??= setTimeout(open, opened ? REOPEN_MS : RETRY_MS);
      onError();
    });
  };
  open();
  return () => {
    stopped = true;
    es?.close();
    if (timer !== undefined) clearTimeout(timer);
  };
}
