/* Shared behaviour: the mobile menu, the nav's border on scroll, the quiet reveal, the facts strip.
 * Everything here is decoration. With JavaScript off the pages read the same, minus the movement. */
(function () {
  "use strict";
  var d = document;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // mobile menu
  var btn = d.querySelector(".menu-btn"), menu = d.querySelector(".menu");
  if (btn && menu) {
    var setOpen = function (open) {
      menu.classList.toggle("open", open);
      btn.setAttribute("aria-expanded", String(open));
      btn.textContent = open ? "Close" : "Menu";
    };
    btn.addEventListener("click", function () { setOpen(!menu.classList.contains("open")); });
    d.addEventListener("keydown", function (e) { if (e.key === "Escape") setOpen(false); });
    d.addEventListener("click", function (e) { if (!menu.contains(e.target) && e.target !== btn) setOpen(false); });
    menu.addEventListener("click", function (e) { if (e.target.closest("a")) setOpen(false); });
  }

  // a hairline under the nav once the page has moved
  var nav = d.querySelector(".nav");
  if (nav) {
    var onScroll = function () { nav.classList.toggle("scrolled", window.scrollY > 8); };
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  // the facts strip: a second copy lets the loop run without a seam; screen readers skip it
  var track = d.querySelector(".track");
  if (track && !reduce) {
    Array.prototype.slice.call(track.children).forEach(function (n) {
      var c = n.cloneNode(true);
      c.setAttribute("aria-hidden", "true");
      track.appendChild(c);
    });
  }

  // reveal on scroll
  var items = d.querySelectorAll(".rv");
  if (!("IntersectionObserver" in window) || reduce) {
    Array.prototype.forEach.call(items, function (n) { n.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (en) {
        if (en.isIntersecting) { en.target.classList.add("in"); io.unobserve(en.target); }
      });
    }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    Array.prototype.forEach.call(items, function (n) { io.observe(n); });
  }
})();
