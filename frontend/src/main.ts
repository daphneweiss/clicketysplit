import { mount } from "svelte";
import App from "./App.svelte";
import "./app.css";

const target = document.getElementById("app");
if (!target) {
  throw new Error("missing #app mount point");
}

// Svelte 5 uses the functional `mount` API rather than `new App({ target })`.
const app = mount(App, { target });

export default app;
