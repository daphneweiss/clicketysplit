// Global hotkey registration helper + the Review-step keymap factory.
//
// The dispatcher installs one keydown listener on `document` and routes
// events through a caller-supplied map keyed by an event-shape string
// like "Enter", "Shift+ArrowLeft", or "Ctrl+S".
//
// The Review-step bindings live in `buildReviewHotkeys`. They suppress
// single-key actions while the label input is focused so the user can
// type "R" or "S" into a label without triggering Reject/Skip.

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
  // Keys (using the same event-shape string format as the map keys) that
  // are allowed to fire even when focus is in a typing target. Useful for
  // Tab / Enter / Arrow keys which the review step needs to function on
  // the label input.
  typingFieldAllowlist?: string[];
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
  const allowlist = new Set(options.typingFieldAllowlist ?? []);
  const listener = (event: KeyboardEvent): void => {
    const typing = isTypingTarget(event.target);
    const id = keyId(event);
    if (typing && !allowInTyping) {
      // Per-key allowlist: Tab, Enter, Arrow keys still fire so the user
      // can interact with the label input.
      if (!allowlist.has(id) && !allowlist.has(event.key)) return;
    }
    const handler = map[id] ?? map[event.key];
    if (!handler) return;
    handler(event);
  };
  document.addEventListener("keydown", listener);
  return () => document.removeEventListener("keydown", listener);
}

// ---------------------------------------------------------------------------
// Review-step actions interface
// ---------------------------------------------------------------------------

export interface ReviewHotkeyActions {
  play: () => void;
  accept: () => void;
  reject: () => void;
  skip: () => void;
  prev: () => void;
  next: () => void;
  toggleAddMode: () => void;
  cancelAddOrModal: () => void;
  focusLabel: () => void;
}

/**
 * Build the review-step hotkey map.
 *
 * Single-letter bindings (R/S/A) are suppressed while a typing target
 * has focus so the user can type those letters into a label. Tab, Enter,
 * Arrow keys, and Esc fire even while typing.
 */
export function buildReviewHotkeys(actions: ReviewHotkeyActions): HotkeyMap {
  return {
    // The original tool binds BOTH Space and Tab to play (index.html:1354-1355);
    // hours of muscle memory live on Space.
    " ": (e) => {
      e.preventDefault();
      actions.play();
    },
    Tab: (e) => {
      e.preventDefault();
      actions.play();
    },
    Enter: (e) => {
      e.preventDefault();
      actions.accept();
    },
    r: () => actions.reject(),
    R: () => actions.reject(),
    s: () => actions.skip(),
    S: () => actions.skip(),
    ArrowLeft: (e) => {
      e.preventDefault();
      actions.prev();
    },
    ArrowRight: (e) => {
      e.preventDefault();
      actions.next();
    },
    a: () => actions.toggleAddMode(),
    A: () => actions.toggleAddMode(),
    l: (e) => {
      e.preventDefault();
      actions.focusLabel();
    },
    L: (e) => {
      e.preventDefault();
      actions.focusLabel();
    },
    Escape: () => actions.cancelAddOrModal(),
  };
}

export const REVIEW_TYPING_ALLOWLIST: string[] = [
  "Tab",
  "Enter",
  "ArrowLeft",
  "ArrowRight",
  "ArrowUp",
  "ArrowDown",
  "Escape",
];
