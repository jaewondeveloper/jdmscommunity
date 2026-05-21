(function () {
  var params = new URLSearchParams(location.search);
  var presetCategory = params.get("category") || "all";
  var sidebarMap = { notice: "notice", free: "free", qna: "qna" };

  JDMS.initShell("board", sidebarMap[presetCategory] || "board");
  document.getElementById("footer").innerHTML = JDMS.renderFooter();

  var activeCategory = presetCategory;
  var tabsEl = document.getElementById("category-tabs");
  var listEl = document.getElementById("post-list");

  tabsEl.innerHTML = Object.keys(JDMS.CATEGORIES)
    .map(function (key) {
      return (
        '<button type="button" class="filter-tab' +
        (key === activeCategory ? " is-active" : "") +
        '" data-category="' +
        key +
        '">' +
        JDMS.CATEGORIES[key] +
        "</button>"
      );
    })
    .join("");

  function renderList() {
    var posts = JDMS.getPosts().filter(function (p) {
      return !p.classId;
    });
    if (activeCategory !== "all") {
      posts = posts.filter(function (p) {
        return p.category === activeCategory;
      });
    }

    if (posts.length === 0) {
      listEl.innerHTML =
        '<li class="empty-state"><p>게시글이 없습니다.</p><a class="btn btn--primary" href="write.html">글 작성하기</a></li>';
      return;
    }
    listEl.innerHTML = posts.map(JDMS.renderPostListItem).join("");
  }

  tabsEl.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-category]");
    if (!btn) return;
    activeCategory = btn.dataset.category;
    tabsEl.querySelectorAll(".filter-tab").forEach(function (t) {
      t.classList.remove("is-active");
    });
    btn.classList.add("is-active");
    renderList();
  });

  renderList();
})();
