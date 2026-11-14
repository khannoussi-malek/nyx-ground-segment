export const REOPEN_MS = 1000;

/** EventSource with a client watchdog (D-UI-03): on error close and reopen after 1 s, one pending at a time. */
export function connectEvents(
  url: string,
  onEvent: (kind: "snapshot" | "tm", data: unknown) => void,
  onError: () => void,
  deps = {
    EventSource: globalThis.EventSource,
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
  },
): () => void {
  let es: EventSource | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let stopped = false;
  const open = () => {
    timer = undefined;
    if (stopped) return;
    const src = new deps.EventSource(url);
    es = src;
    for (const kind of ["snapshot", "tm"] as const) {
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
      timer ??= deps.setTimeout(open, REOPEN_MS);
      onError();
    });
  };
  open();
  return () => {
    stopped = true;
    es?.close();
    if (timer !== undefined) deps.clearTimeout(timer);
  };
}
