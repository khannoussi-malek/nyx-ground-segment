export type KeySet = {
  has(k: string): boolean;
  add(k: string): boolean; // true when new
  delete(k: string): void;
};

/** A set of keys mirrored to a Storage under `nyx.<name>` so a reload remembers it (D-UI-08).
 *  Storage that is missing, full or throwing leaves a memory-only set: the console still works, it may repeat after a reload. */
export function createKeySet(name: string, storage: Pick<Storage, "getItem" | "setItem"> | null): KeySet {
  const id = `nyx.${name}`;
  let keys = new Set<string>();
  try {
    const raw: unknown = JSON.parse(storage?.getItem(id) ?? "[]");
    if (Array.isArray(raw)) keys = new Set(raw.filter((k): k is string => typeof k === "string"));
  } catch {
    /* unreadable: start empty */
  }
  const save = () => {
    try {
      storage?.setItem(id, JSON.stringify([...keys]));
    } catch {
      /* memory only */
    }
  };
  return {
    has: (k) => keys.has(k),
    add(k) {
      if (keys.has(k)) return false;
      keys.add(k);
      save();
      return true;
    },
    delete(k) {
      if (keys.delete(k)) save();
    },
  };
}

/** sessionStorage can throw on access itself (blocked site data), so the lookup is guarded too. */
export function sessionKeySet(name: string): KeySet {
  let s: Storage | null = null;
  try {
    s = globalThis.sessionStorage ?? null;
  } catch {
    s = null;
  }
  return createKeySet(name, s);
}
