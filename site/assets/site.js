/* Shared behaviour: inertial scrolling, the mobile menu, the nav's border on scroll, the staggered reveal, the facts strip,
 * and quiet prefetching of the other pages. Everything here is decoration: with JavaScript off the pages read the same,
 * minus the movement. */
(function () {
  "use strict";
  var d = document;
  var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Inertial scrolling for mouse wheels and trackpads (Lenis, MIT licence, kept in assets/vendor/).
  // Touch screens keep their own native scrolling. If the library is missing or fails, the page scrolls natively.
  if (!reduce && typeof window.Lenis === "function") {
    try { new window.Lenis({ autoRaf: true, lerp: 0.1, anchors: { offset: -84 } }); } catch (e) { /* native scrolling */ }
  }

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

  // Reveal on scroll. Blocks that arrive in the same moment (a row of cards, a set of steps) appear one after another:
  // rising and fading normally, fading only (site.css) for anyone whose system asks for reduced motion.
  var items = d.querySelectorAll(".rv");
  if (!("IntersectionObserver" in window)) {
    Array.prototype.forEach.call(items, function (n) { n.classList.add("in"); });
  } else {
    var io = new IntersectionObserver(function (entries) {
      var batch = entries.filter(function (en) { return en.isIntersecting; }).sort(function (a, b) {
        var ra = a.boundingClientRect, rb = b.boundingClientRect;
        return (ra.top - rb.top) || (ra.left - rb.left);
      });
      batch.forEach(function (en, i) {
        var el = en.target, delay = Math.min(i, 5) * 90;
        el.style.setProperty("--d", delay + "ms");
        el.classList.add("in");
        io.unobserve(el);
        // once it has arrived, drop the delay so hover effects answer at once
        setTimeout(function () { el.classList.add("done"); }, 900 + delay + 60);
      });
    }, { rootMargin: "0px 0px -3% 0px", threshold: 0 });
    Array.prototype.forEach.call(items, function (n) { io.observe(n); });
  }

  // Prefetch the other pages once the browser is idle, and again when a link is pointed at, so a click opens at once.
  var conn = navigator.connection || {};
  if (!conn.saveData && !/(^|-)2g$/.test(conn.effectiveType || "")) {
    var seen = {};
    var prefetch = function (href) {
      if (!href || seen[href]) return;
      seen[href] = true;
      var l = d.createElement("link");
      l.rel = "prefetch"; l.href = href; l.as = "document";
      d.head.appendChild(l);
    };
    var local = function (a) {                         // a same-folder page link such as "team.html"
      var h = a.getAttribute("href") || "";
      return /^[a-z-]+\.html$/.test(h) && a.pathname.split("/").pop() !== location.pathname.split("/").pop() ? h : "";
    };
    var links = Array.prototype.slice.call(d.querySelectorAll("a[href$='.html']"));
    var warm = function () { links.forEach(function (a) { prefetch(local(a)); }); };
    if ("requestIdleCallback" in window) window.requestIdleCallback(warm, { timeout: 2500 }); else setTimeout(warm, 1500);
    links.forEach(function (a) {
      ["pointerenter", "focus", "touchstart"].forEach(function (ev) {
        a.addEventListener(ev, function () { prefetch(local(a)); }, { passive: true, once: true });
      });
    });
  }
})();
