/* Demo page: the drill buttons and the operator box choose which recording the dashboard frame plays. */
(function () {
  "use strict";
  var frame = document.getElementById("app"), full = document.getElementById("full"), op = document.getElementById("op");
  var chips = document.querySelectorAll("#chips [data-scen]");
  if (!frame || !op) return;
  var scen = "frozen_screen";

  function go() {
    var url = "demo/app.html?scenario=" + encodeURIComponent(scen) + (op.checked ? "&op=1" : "");
    frame.src = url;
    full.href = url;
    Array.prototype.forEach.call(chips, function (b) { b.setAttribute("aria-pressed", String(b.getAttribute("data-scen") === scen)); });
  }
  Array.prototype.forEach.call(chips, function (b) {
    b.addEventListener("click", function () { scen = b.getAttribute("data-scen"); go(); });
  });
  op.addEventListener("change", go);

  // Size the frame to the dashboard inside it, so the dashboard never scrolls on its own: the page just scrolls,
  // and the mouse wheel is never caught by the frame. (Same origin, so the page may measure it.)
  var watching = null;
  function fit() {
    try {
      var body = frame.contentDocument && frame.contentDocument.body;
      if (body) frame.style.height = Math.max(640, Math.ceil(body.scrollHeight) + 1) + "px";
    } catch (e) { /* if it cannot be measured, the stylesheet's height stays */ }
  }
  frame.addEventListener("load", function () {
    fit();
    try {
      if (watching) watching.disconnect();
      if ("ResizeObserver" in window) { watching = new ResizeObserver(fit); watching.observe(frame.contentDocument.body); }
    } catch (e) { /* no observer: the height set at load stays */ }
  });
})();
