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
})();
