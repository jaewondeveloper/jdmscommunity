(function () {
  JDMS.initShell("class", "class");
  document.getElementById("footer").innerHTML = JDMS.renderFooter();

  var params = new URLSearchParams(location.search);
  var activeClass = params.get("class") || JDMS.CLASSES[0];

  var selectEl = document.getElementById("class-select");
  var gridEl = document.getElementById("post-grid");
  var labelEl = document.getElementById("class-label");
  var writeBtn = document.getElementById("write-class-btn");

  selectEl.innerHTML = JDMS.CLASSES.map(function (id) {
    return (
      '<button type="button" class="class-select__btn' +
      (id === activeClass ? " is-active" : "") +
      '" data-class="' +
      id +
      '">' +
      JDMS.formatClassLabel(id) +
      "</button>"
    );
  }).join("");

  function updateUrl() {
    var url = new URL(location.href);
    url.searchParams.set("class", activeClass);
    history.replaceState(null, "", url);
  }

  function renderGrid() {
    labelEl.textContent = JDMS.formatClassLabel(activeClass) + " 게시판";
    writeBtn.href = "write.html?class=" + activeClass;

    var posts = JDMS.getPosts().filter(function (p) {
      return p.classId === activeClass;
    });

    if (posts.length === 0) {
      gridEl.innerHTML =
        '<div class="empty-state" style="grid-column:1/-1"><p>이 반 게시글이 아직 없습니다.</p><a class="btn btn--primary" href="write.html?class=' +
        activeClass +
        '">첫 글 작성하기</a></div>';
      return;
    }
    gridEl.innerHTML = posts.map(JDMS.renderPostCard).join("");
  }

  selectEl.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-class]");
    if (!btn) return;
    activeClass = btn.dataset.class;
    selectEl.querySelectorAll(".class-select__btn").forEach(function (b) {
      b.classList.remove("is-active");
    });
    btn.classList.add("is-active");
    updateUrl();
    renderGrid();
  });

  updateUrl();
  renderGrid();
})();
