// The customer's theme before the first paint (same rule as applyTheme in src/theme.ts).
try {
  var t = localStorage.getItem("ola-theme");
  if (t !== "light" && t !== "dark") t = matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
  document.documentElement.dataset.theme = t;
} catch (e) {}
