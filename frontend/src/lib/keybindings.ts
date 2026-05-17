// Global hotkey registration helper.
//
// Task 9 only ships the dispatcher; task 10's Review.svelte registers the
// actual review-step bindings (Enter=accept, R=reject, ...). The dispatcher
// installs one keydown listener on the document and dispatches based on a
// caller-supplied map keyed by an event-shape string like "Enter",
// "Shift+ArrowLeft", or "Ctrl+S".

export type HotkeyHandler = (event: KeyboardEvent) => void;
export type HotkeyMap = Record<string, HotkeyHandler>;

function keyId(event: KeyboardEvent): string {
  const parts: string[] = [];
  if (event.ctrlKey) parts.push("Ctrl");
  if (event.metaKey) parts.push("Meta");
  if (event.altKey) parts.push("Alt");
  if (event.shiftKey) parts.push("Shift");
  parts.push(event.key);
  return parts.join("+");
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  if (target.isContentEditable) return true;
  return false;
}

export interface RegisterOptions {
  // If true, the dispatcher fires even when focus is in an input/textarea.
  // Default false — most review-step bindings should not steal Enter/Tab
  // from the label autocomplete field. Components that want input-aware
  // behavior should attach listeners directly to the input element.
  allowInTypingField?: boolean;
}

/**
 * Register a hotkey map on `document`. Returns an unregister function.
 *
 * Usage (later, from Review.svelte):
 *   const off = registerHotkeys({ "Enter": () => accept(), "r": () => reject() });
 *   onDestroy(off);
 */
export function registerHotkeys(
  map: HotkeyMap,
  options: RegisterOptions = {},
): () => void {
  const allowInTyping = options.allowInTypingField ?? false;
  const listener = (event: KeyboardEvent): void => {
    if (!allowInTyping && isTypingTarget(event.target)) return;
    const id = keyId(event);
    const handler = map[id] ?? map[event.key];
    if (!handler) return;
    handler(event);
  };
  document.addEventListener("keydown", listener);
  return () => document.removeEventListener("keydown", listener);
}
