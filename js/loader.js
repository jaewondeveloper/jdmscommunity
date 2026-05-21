/**
 * Google 스타일 로딩 + 페이지 전환 시 부드러운 표시
 */
(function () {
  var MIN_VISIBLE_MS = 380;
  var NAV_DELAY_MS = 120;
  var showStartedAt = Date.now();

  var SPINNER_HTML =
    '<div class="android-spinner" role="status" aria-label="로딩 중">' +
    '<svg class="android-spinner__svg" viewBox="0 0 48 48" aria-hidden="true">' +
    '<circle class="android-spinner__arc" cx="24" cy="24" r="20"></circle>' +
    "</svg></div>";

  function getLoader() {
    return document.getElementById("page-loader");
  }

  function ensureSpinnerMarkup() {
    var loader = getLoader();
    if (!loader) return;
    if (!loader.querySelector(".android-spinner__svg")) {
      loader.innerHTML = SPINNER_HTML;
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", ensureSpinnerMarkup);
  } else {
    ensureSpinnerMarkup();
  }

  function showLoader() {
    var loader = getLoader();
    if (!loader) return;
    showStartedAt = Date.now();
    loader.classList.remove("is-hidden");
    loader.classList.add("is-active");
    document.body.classList.remove("page-ready");
    document.body.classList.add("is-loading");
  }

  function hideLoader() {
    var loader = getLoader();
    if (!loader) return;

    var elapsed = Date.now() - showStartedAt;
    var wait = Math.max(0, MIN_VISIBLE_MS - elapsed);

    window.setTimeout(function () {
      loader.classList.remove("is-active");
      loader.classList.add("is-hidden");
      document.body.classList.remove("is-loading");
      document.body.classList.add("page-ready");
    }, wait);
  }

  function isInternalNavLink(anchor) {
    if (!anchor || !anchor.href) return false;
    if (anchor.target === "_blank" || anchor.hasAttribute("download")) return false;

    var href = anchor.getAttribute("href");
    if (!href || href.charAt(0) === "#" || href.indexOf("javascript:") === 0) return false;

    try {
      var url = new URL(anchor.href, window.location.href);
      if (url.origin !== window.location.origin) return false;
      return /\.html$/i.test(url.pathname) || url.pathname.endsWith("/") || !/\./.test(url.pathname.split("/").pop() || "");
    } catch (e) {
      return /\.html$/i.test(href);
    }
  }

  document.addEventListener("click", function (e) {
    var anchor = e.target.closest("a[href]");
    if (!anchor || !isInternalNavLink(anchor)) return;

    e.preventDefault();
    showLoader();
    window.setTimeout(function () {
      window.location.href = anchor.href;
    }, NAV_DELAY_MS);
  });

  window.addEventListener("load", hideLoader);

  window.addEventListener("pageshow", function (ev) {
    if (ev.persisted) {
      showStartedAt = Date.now();
      hideLoader();
    }
  });

  window.JDMSLoader = {
    show: showLoader,
    hide: hideLoader,
  };
})();
