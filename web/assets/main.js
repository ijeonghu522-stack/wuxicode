/* WUXICODE — landing page behaviour.
   Three small things: a sticky-nav border, a reveal on scroll, and the
   version string in the footer read from the same place the build reads it,
   so the site cannot claim a version the EXE does not have. */

(function () {
  "use strict";

  // -- sticky nav border ---------------------------------------------------

  var nav = document.querySelector(".nav");
  if (nav) {
    var onScroll = function () {
      nav.classList.toggle("stuck", window.scrollY > 8);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  // -- reveal on scroll ----------------------------------------------------

  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var targets = document.querySelectorAll(
    ".card, .fix, .step, .dl-card, .plan, .tbl tbody tr, .quality"
  );

  if (reduce || !("IntersectionObserver" in window)) {
    targets.forEach(function (el) { el.classList.add("in"); });
  } else {
    var style = document.createElement("style");
    style.textContent =
      ".rv{opacity:0;transform:translateY(14px);" +
      "transition:opacity .55s cubic-bezier(.2,.7,.3,1),transform .55s cubic-bezier(.2,.7,.3,1)}" +
      ".rv.in{opacity:1;transform:none}";
    document.head.appendChild(style);

    targets.forEach(function (el) { el.classList.add("rv"); });

    var io = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          var el = entry.target;
          el.classList.add("in");
          io.unobserve(el);
        });
      },
      { rootMargin: "0px 0px -8% 0px", threshold: 0.06 }
    );

    targets.forEach(function (el, i) {
      el.style.transitionDelay = Math.min(i % 4, 3) * 55 + "ms";
      io.observe(el);
    });
  }

  // -- footer version ------------------------------------------------------

  // The page hardcodes v0.2.0 in the markup as well, so this only has to make
  // sure the two cannot drift unnoticed: anything already present is left
  // alone unless it disagrees with a version.json shipped alongside.
  fetch("version.json", { cache: "no-cache" })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (data) {
      if (!data || !data.version) return;
      document.querySelectorAll(".foot-ver, .eyebrow").forEach(function (el) {
        el.textContent = el.textContent.replace(/v?\d+\.\d+\.\d+/, "v" + data.version);
      });
    })
    .catch(function () {
      /* file:// or no version.json -- the static markup is the fallback */
    });
})();
