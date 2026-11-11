import { ref } from "vue";

// Theme preference: "auto" follows prefers-color-scheme, "light"/"dark"
// win manually. Persisted; applied as a .dark class on <html>.
const KEY = "nexus-theme";
const pref = ref(localStorage.getItem(KEY) || "auto");

const media = window.matchMedia("(prefers-color-scheme: dark)");
media.addEventListener("change", () => apply());

function apply() {
  const dark =
    pref.value === "dark" || (pref.value === "auto" && media.matches);
  document.documentElement.classList.toggle("dark", dark);
}

function set(value) {
  pref.value = value;
  localStorage.setItem(KEY, value);
  apply();
}

apply();

export function useTheme() {
  return { pref, set, apply };
}
