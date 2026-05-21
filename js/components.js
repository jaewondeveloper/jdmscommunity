(function (global) {
  var JDMS = global.JDMS;
  var LOGO_SRC = "assets/jdmslogo.png";

  var TOP_MENU = [
    { id: "home", label: "홈", href: "index.html" },
    { id: "community", label: "커뮤니티", href: "index.html" },
    { id: "board", label: "전체 게시판", href: "board.html" },
    { id: "class", label: "반별 게시판", href: "class-board.html" },
  ];

  var SIDEBAR_MENU = [
    { id: "home", label: "HOME", href: "index.html" },
    { id: "popular", label: "인기글", href: "index.html#popular" },
    { id: "board", label: "전체 게시판", href: "board.html" },
    { id: "notice", label: "공지", href: "board.html?category=notice" },
    { id: "free", label: "자유게시판", href: "board.html?category=free" },
    { id: "qna", label: "질문", href: "board.html?category=qna" },
    { id: "class", label: "반별 게시판", href: "class-board.html" },
    { id: "write", label: "글쓰기", href: "write.html" },
  ];

  var TAB_MENU = [
    { id: "home", label: "홈", icon: "home", href: "index.html" },
    { id: "board", label: "게시판", icon: "layout-grid", href: "board.html" },
    { id: "class", label: "반별", icon: "users", href: "class-board.html" },
    { id: "write", label: "글쓰기", icon: "square-pen", href: "write.html" },
  ];

  var LUCIDE_ICONS = {
    home:
      '<path d="M15 21v-8a1 1 0 0 0-1-1h-4a1 1 0 0 0-1 1v8"/><path d="M3 10a2 2 0 0 1 .709-1.528l7-6a2 2 0 0 1 2.582 0l7 6A2 2 0 0 1 21 10v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "layout-grid":
      '<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
    users:
      '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><path d="M16 3.128a4 4 0 0 1 0 7.744"/><path d="M22 21v-2a4 4 0 0 0-3-3.87"/><circle cx="9" cy="7" r="4"/>',
    "square-pen":
      '<path d="M12 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.375 2.625a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4Z"/>',
  };

  function lucideIcon(name) {
    var paths = LUCIDE_ICONS[name] || LUCIDE_ICONS.home;
    return (
      '<svg class="lucide-icon" xmlns="http://www.w3.org/2000/svg" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
      paths +
      "</svg>"
    );
  }

  function escapeHtml(str) {
    var div = document.createElement("div");
    div.textContent = str == null ? "" : str;
    return div.innerHTML;
  }

  function renderNavbar(activeId) {
    activeId = activeId || "community";
    var menu = TOP_MENU.map(function (m) {
      return (
        '<a class="top-nav__link' +
        (activeId === m.id ? " is-active" : "") +
        '" href="' +
        m.href +
        '">' +
        m.label +
        "</a>"
      );
    }).join("");

    return (
      '<header class="top-nav">' +
      '<div class="top-nav__inner">' +
      '<button type="button" class="icon-btn mobile-menu-btn" id="mobile-menu-btn" aria-label="메뉴">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h16M4 18h16"/></svg>' +
      "</button>" +
      '<a class="top-nav__brand" href="index.html">' +
      '<img class="top-nav__logo" src="' +
      LOGO_SRC +
      '" alt="중동중학교 커뮤니티" />' +
      "</a>" +
      '<nav class="top-nav__menu" aria-label="상단 메뉴">' +
      menu +
      "</nav>" +
      '<div class="top-nav__actions">' +
      '<button type="button" class="icon-btn" id="search-toggle" aria-label="검색">' +
      '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3-3"/></svg>' +
      "</button>" +
      '<button type="button" class="btn btn--primary btn--sm" id="login-btn">로그인</button>' +
      "</div></div></header>" +
      '<div class="search-overlay" id="search-overlay">' +
      '<div class="search-box">' +
      '<input class="form-control" type="search" placeholder="게시글 검색..." id="search-input" />' +
      '<button type="button" class="btn btn--primary btn--sm" id="search-close">닫기</button>' +
      "</div></div>"
    );
  }

  function renderDrawer(activeId) {
    activeId = activeId || "home";
    var links = SIDEBAR_MENU.map(function (m) {
      return (
        '<li><a class="drawer__link' +
        (activeId === m.id ? " is-active" : "") +
        '" href="' +
        m.href +
        '">' +
        m.label +
        "</a></li>"
      );
    }).join("");

    return (
      '<div class="drawer-overlay" id="drawer-overlay" aria-hidden="true"></div>' +
      '<aside class="drawer" id="drawer" aria-hidden="true">' +
      '<p class="drawer__title">메뉴</p>' +
      '<ul class="drawer__nav">' +
      links +
      "</ul></aside>"
    );
  }

  function renderSidebar(activeId) {
    activeId = activeId || "home";
    var links = SIDEBAR_MENU.map(function (m) {
      return (
        '<li><a class="sidebar__link' +
        (activeId === m.id ? " is-active" : "") +
        '" href="' +
        m.href +
        '">' +
        m.label +
        "</a></li>"
      );
    }).join("");

    return (
      '<aside class="sidebar">' +
      '<ul class="sidebar__nav">' +
      links +
      "</ul>" +
      '<div class="sidebar__divider"></div>' +
      '<div class="sidebar__promo">' +
      '<div class="sidebar__promo-icon" aria-hidden="true">🏫</div>' +
      '<p class="sidebar__promo-text">중동중학교 학생·교사를 위한 소통 공간입니다.</p>' +
      '<a class="sidebar__promo-link" href="class-board.html">반별 게시판 보기 ›</a>' +
      "</div></aside>"
    );
  }

  function renderTabBar(activeId) {
    activeId = activeId || "home";
    if (activeId === "community") activeId = "home";
    if (activeId === "popular" || activeId === "notice" || activeId === "free" || activeId === "qna") {
      activeId = "board";
    }

    var items = TAB_MENU.map(function (m) {
      var cls = "tab-bar__item";
      if (activeId === m.id) cls += " is-active";
      if (m.id === "write") cls += " tab-bar__item--write";
      return (
        '<a class="' +
        cls +
        '" href="' +
        m.href +
        '">' +
        '<span class="tab-bar__icon">' +
        lucideIcon(m.icon) +
        "</span>" +
        "<span>" +
        m.label +
        "</span></a>"
      );
    }).join("");

    return (
      '<nav class="tab-bar" id="tab-bar" aria-label="하단 메뉴">' +
      '<div class="tab-bar__inner">' +
      items +
      "</div></nav>"
    );
  }

  function bindDrawer() {
    var btn = document.getElementById("mobile-menu-btn");
    var drawer = document.getElementById("drawer");
    var overlay = document.getElementById("drawer-overlay");
    if (!btn || !drawer || !overlay) return;

    function open() {
      drawer.classList.add("is-open");
      overlay.classList.add("is-open");
      overlay.setAttribute("aria-hidden", "false");
      drawer.setAttribute("aria-hidden", "false");
      document.body.style.overflow = "hidden";
    }

    function close() {
      drawer.classList.remove("is-open");
      overlay.classList.remove("is-open");
      overlay.setAttribute("aria-hidden", "true");
      drawer.setAttribute("aria-hidden", "true");
      document.body.style.overflow = "";
    }

    btn.addEventListener("click", open);
    overlay.addEventListener("click", close);
    drawer.querySelectorAll("a").forEach(function (a) {
      a.addEventListener("click", close);
    });
  }

  function bindSearch() {
    var toggle = document.getElementById("search-toggle");
    var overlay = document.getElementById("search-overlay");
    var close = document.getElementById("search-close");
    if (!toggle || !overlay) return;

    toggle.addEventListener("click", function () {
      overlay.classList.add("is-open");
      var input = document.getElementById("search-input");
      if (input) input.focus();
    });
    if (close) {
      close.addEventListener("click", function () {
        overlay.classList.remove("is-open");
      });
    }
    overlay.addEventListener("click", function (e) {
      if (e.target === overlay) overlay.classList.remove("is-open");
    });
  }

  function initShell(navActive, sidebarActive) {
    var navEl = document.getElementById("navbar");
    var sideEl = document.getElementById("sidebar");
    var drawerEl = document.getElementById("drawer-root");
    var tabEl = document.getElementById("tab-bar-root");

    if (navEl) navEl.innerHTML = renderNavbar(navActive);
    if (sideEl) sideEl.innerHTML = renderSidebar(sidebarActive);
    if (drawerEl) drawerEl.innerHTML = renderDrawer(sidebarActive);
    if (tabEl) tabEl.innerHTML = renderTabBar(sidebarActive);

    bindDrawer();
    bindSearch();
    if (global.JDMSAuth) global.JDMSAuth.bindLoginButton();
  }

  function renderFooter() {
    return '<footer class="site-footer"><p>© 중동중학교(JDMS) 커뮤니티</p></footer>';
  }

  function categoryBadge(category) {
    var label = JDMS.CATEGORIES[category] || category;
    var map = {
      notice: "badge badge--notice",
      qna: "badge badge--qna",
      free: "badge",
    };
    return '<span class="' + (map[category] || "badge") + '">' + label + "</span>";
  }

  function postStats(post) {
    var likes = post.likes != null ? post.likes : Math.floor((post.id ? post.id.length : 1) * 3 + 1);
    var views = post.views != null ? post.views : Math.floor((post.id ? post.id.length : 1) * 17 + 10);
    var comments = post.comments ? post.comments.length : 0;
    return { likes: likes, views: views, comments: comments };
  }

  function avatarLetter(name) {
    return (name || "?").charAt(0).toUpperCase();
  }

  function renderPostCard(post) {
    var stats = postStats(post);
    var excerpt = (post.content || "").replace(/\n/g, " ").slice(0, 120);
    var classTag = post.classId
      ? '<span class="badge badge--class">' + JDMS.formatClassLabel(post.classId) + "</span> "
      : "";

    return (
      '<a class="post-card" href="post.html?id=' +
      post.id +
      '">' +
      '<h3 class="post-card__title">' +
      classTag +
      escapeHtml(post.title) +
      "</h3>" +
      '<p class="post-card__excerpt">' +
      escapeHtml(excerpt) +
      (post.content && post.content.length > 120 ? "…" : "") +
      "</p>" +
      '<div class="post-card__footer">' +
      '<span class="post-card__avatar">' +
      avatarLetter(post.author) +
      "</span>" +
      '<span class="post-card__author">' +
      escapeHtml(post.author) +
      "</span>" +
      '<span class="post-card__stats">' +
      "<span>추천 " +
      stats.likes +
      "</span>" +
      "<span>조회 " +
      stats.views +
      "</span>" +
      (stats.comments > 0 ? "<span>댓글 " + stats.comments + "</span>" : "") +
      "<span>" +
      JDMS.timeAgo(post.createdAt) +
      "</span></span></div></a>"
    );
  }

  function renderPostListItem(post) {
    var classBadge = post.classId
      ? '<span class="badge badge--class">' + JDMS.formatClassLabel(post.classId) + "</span>"
      : "";
    var stats = postStats(post);

    return (
      '<li class="board-item">' +
      '<a href="post.html?id=' +
      post.id +
      '">' +
      categoryBadge(post.category) +
      classBadge +
      '<span class="board-item__title">' +
      escapeHtml(post.title) +
      "</span></a>" +
      '<span class="board-item__meta">' +
      escapeHtml(post.author) +
      " · 조회 " +
      stats.views +
      (stats.comments > 0 ? " · 댓글 " + stats.comments : "") +
      " · " +
      JDMS.timeAgo(post.createdAt) +
      "</span></li>"
    );
  }

  Object.assign(JDMS, {
    renderNavbar: renderNavbar,
    renderSidebar: renderSidebar,
    initShell: initShell,
    renderFooter: renderFooter,
    categoryBadge: categoryBadge,
    renderPostCard: renderPostCard,
    renderPostListItem: renderPostListItem,
    escapeHtml: escapeHtml,
  });
})(window);
