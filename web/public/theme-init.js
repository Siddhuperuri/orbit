/*
 * Applies the saved theme before first paint, so a dark-mode user never sees a
 * flash of the light palette. Served as a same-origin file rather than inlined
 * so the site's Content-Security-Policy can stay free of `unsafe-inline` for
 * scripts (docs/architecture/security.md). Keep it tiny and dependency-free.
 */
(function () {
  var theme = "light";
  try {
    var stored = window.localStorage.getItem("orbit:theme");
    var dark =
      stored === "dark" ||
      (stored !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    theme = dark ? "dark" : "light";
  } catch (_error) {
    // Storage or matchMedia unavailable: the light palette is a safe default.
  }
  document.documentElement.setAttribute("data-theme", theme);
})();
