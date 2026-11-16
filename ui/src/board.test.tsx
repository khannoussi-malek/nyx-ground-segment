// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, test } from "vitest";
import { Board, LAYOUT_KEY, useLayout } from "./Board";
import { DEFAULT_LAYOUT, PANES, type PaneId } from "./layout";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root;
let host: HTMLElement;
beforeEach(() => {
  localStorage.clear();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

const PANE_DEFS = Object.fromEntries(PANES.map((p) => [p, { title: p.toUpperCase(), node: <p className="body">{p}</p> }])) as Record<PaneId, { title: string; node: React.ReactNode }>;
function Harness({ editing = true }: { editing?: boolean }) {
  const { layout, update, reset } = useLayout();
  return (
    <>
      <button className="reset" onClick={reset}>Reset layout</button>
      <Board panes={PANE_DEFS} layout={layout} update={update} editing={editing} />
    </>
  );
}
const mount = (editing = true) => act(() => root.render(<Harness editing={editing} />));
const order = () => [...host.querySelectorAll<HTMLElement>(".pane")].map((p) => p.dataset.pane);
const btn = (label: string) => host.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;
const stored = () => JSON.parse(localStorage.getItem(LAYOUT_KEY)!);

test("every pane renders once, in the default order, its content under its bar", () => {
  mount();
  expect(order()).toEqual([...DEFAULT_LAYOUT.order]);
  const alarms = host.querySelector('[data-pane="alarms"]')!;
  expect(alarms.querySelector(".pane-title")!.textContent).toBe("ALARMS");
  expect(alarms.querySelector(".body")!.textContent).toBe("alarms");
});

test("the move buttons reorder with the keyboard path; the ends are disabled", () => {
  mount();
  expect(btn("Move ALARMS earlier").disabled).toBe(true);
  expect(btn("Move EVENTS later").disabled).toBe(true);
  act(() => btn("Move LIVE earlier").click());
  expect(order().slice(0, 2)).toEqual(["live", "alarms"]);
  act(() => btn("Move LIVE later").click());
  expect(order().slice(0, 2)).toEqual(["alarms", "live"]);
});

test("the width toggle switches full and half width and says which it is", () => {
  mount();
  const pane = () => host.querySelector<HTMLElement>('[data-pane="commands"]')!;
  expect(pane().dataset.wide).toBeUndefined();
  expect(btn("COMMANDS full width").getAttribute("aria-pressed")).toBe("false");
  act(() => btn("COMMANDS full width").click());
  expect(pane().dataset.wide).toBe("true");
  expect(btn("COMMANDS full width").getAttribute("aria-pressed")).toBe("true");
});

test("the arrangement is kept in this browser and comes back on the next load; Reset layout brings the default", () => {
  mount();
  act(() => btn("Move EVENTS earlier").click());
  expect(stored().order.at(-2)).toBe("events");
  act(() => root.unmount());
  root = createRoot(host);
  mount();
  expect(order().at(-2)).toBe("events");
  act(() => host.querySelector<HTMLButtonElement>("button.reset")!.click());
  expect(order()).toEqual([...DEFAULT_LAYOUT.order]);
  expect(stored().order).toEqual([...DEFAULT_LAYOUT.order]);
});

test("a damaged stored arrangement never hides a pane", () => {
  localStorage.setItem(LAYOUT_KEY, '{"order":["events","bogus"]');
  mount();
  expect(order()).toEqual([...DEFAULT_LAYOUT.order]);
  localStorage.setItem(LAYOUT_KEY, JSON.stringify({ order: ["events"] }));
  act(() => root.unmount());
  root = createRoot(host);
  mount();
  expect(order()[0]).toBe("events");
  expect([...order()].sort()).toEqual([...PANES].sort());
});

test("a height left by the resize handle becomes the pane's min-height on release, so its row neighbour can stretch", () => {
  mount();
  const pane = host.querySelector<HTMLElement>('[data-pane="sessions"]')!;
  pane.getBoundingClientRect = () => ({ height: 412.4 }) as DOMRect;
  pane.style.height = "412px"; // what the native handle writes
  act(() => document.dispatchEvent(new Event("pointerup")));
  expect(pane.style.height).toBe("");
  expect(pane.style.minHeight).toBe("412px");
  expect(stored().heights).toEqual({ sessions: 412 });
});

test("outside edit mode the panes keep their titles but offer no grip, move or width controls", () => {
  mount(false);
  expect(host.querySelector(".board")!.hasAttribute("data-editing")).toBe(false);
  expect(host.querySelectorAll(".pane-title")).toHaveLength(PANES.length);
  expect(host.querySelector(".grip, .pane-bar button")).toBeNull();
  mount(true);
  expect(host.querySelector(".board")!.getAttribute("data-editing")).toBe("true");
  expect(host.querySelectorAll(".grip")).toHaveLength(PANES.length);
});

test("drag and drop: a pane dropped on another takes its place, and the drop target is marked while hovered", () => {
  mount();
  const fire = (el: Element, type: string) => {
    const e = new Event(type, { bubbles: true, cancelable: true });
    Object.defineProperty(e, "dataTransfer", { value: { setData() {}, setDragImage() {}, effectAllowed: "" } });
    act(() => void el.dispatchEvent(e));
    return e;
  };
  const pane = (id: string) => host.querySelector(`[data-pane="${id}"]`)!;
  fire(pane("events").querySelector(".grip")!, "dragstart");
  expect(fire(pane("live"), "dragover").defaultPrevented).toBe(true); // the drop is accepted
  expect(pane("live").getAttribute("data-drop")).toBe("true");
  fire(pane("live"), "drop");
  expect(order().slice(0, 3)).toEqual(["alarms", "events", "live"]);
  expect(host.querySelector("[data-drop]")).toBeNull();
  expect(fire(pane("chart"), "dragover").defaultPrevented).toBe(false); // nothing dragged: not a drop target
});
