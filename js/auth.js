(function (global) {
  var DEVICE_KEY = "jdms_device_id";
  var USER_KEY = "jdms_user";

  function getDeviceId() {
    var id = localStorage.getItem(DEVICE_KEY);
    if (!id) {
      id = "dev_" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
      localStorage.setItem(DEVICE_KEY, id);
    }
    return id;
  }

  function getUser() {
    try {
      var raw = localStorage.getItem(USER_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (e) {
      return null;
    }
  }

  function setUser(user) {
    if (user) localStorage.setItem(USER_KEY, JSON.stringify(user));
    else localStorage.removeItem(USER_KEY);
    global.dispatchEvent(new Event("jdms-auth-change"));
  }

  function isLoggedIn() {
    return !!(getUser() && getUser().token);
  }

  function logout() {
    setUser(null);
    location.href = "login.html";
  }

  function apiBase() {
    return (global.JDMS_CONFIG && global.JDMS_CONFIG.API_BASE) || "";
  }

  function sendLoginLink(email) {
    return fetch(apiBase() + "/api/auth/send-link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: email,
        device_id: getDeviceId(),
        bypass: false,
      }),
    }).then(function (r) {
      return r.json();
    });
  }

  function pollAuth() {
    return fetch(apiBase() + "/api/auth/poll?device_id=" + encodeURIComponent(getDeviceId())).then(function (r) {
      return r.json();
    });
  }

  function parseAuthFromUrl() {
    var params = new URLSearchParams(location.search);
    if (!params.get("auth_success") || !params.get("data")) return null;
    try {
      var json = atob(params.get("data").replace(/-/g, "+").replace(/_/g, "/"));
      return JSON.parse(json);
    } catch (e) {
      return null;
    }
  }

  function bindLoginButton() {
    var btn = document.getElementById("login-btn");
    if (!btn) return;
    if (isLoggedIn()) {
      var u = getUser();
      btn.textContent = (u.name || "로그아웃").slice(0, 8);
      btn.title = u.email + " — 클릭 시 로그아웃";
      btn.onclick = function () {
        if (confirm("로그아웃 하시겠습니까?")) logout();
      };
    } else {
      btn.textContent = "로그인";
      btn.onclick = function () {
        location.href = "login.html";
      };
    }
  }

  global.JDMSAuth = {
    getDeviceId: getDeviceId,
    getUser: getUser,
    setUser: setUser,
    isLoggedIn: isLoggedIn,
    logout: logout,
    sendLoginLink: sendLoginLink,
    pollAuth: pollAuth,
    parseAuthFromUrl: parseAuthFromUrl,
    bindLoginButton: bindLoginButton,
  };

  global.addEventListener("jdms-auth-change", bindLoginButton);
})(window);
