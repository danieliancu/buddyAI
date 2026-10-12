// The customer's theme before the first paint (same rule as applyTheme in src/theme.ts): light unless chosen.
try {
  var t = localStorage.getItem("ola-theme");
  if (t === "system") t = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  document.documentElement.dataset.theme = t === "dark" ? "dark" : "light";
} catch (e) {}
