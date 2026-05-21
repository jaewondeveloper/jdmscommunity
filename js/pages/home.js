(function () {
  JDMS.initShell("community", "home");
  document.getElementById("footer").innerHTML = JDMS.renderFooter();

  var all = JDMS.getPosts();
  var popular = all
    .slice()
    .sort(function (a, b) {
      return (b.comments ? b.comments.length : 0) - (a.comments ? a.comments.length : 0);
    })
    .slice(0, 4);

  var popularEl = document.getElementById("popular-posts");
  if (popular.length > 0) {
    popularEl.innerHTML = popular.map(JDMS.renderPostCard).join("");
  } else {
    popularEl.innerHTML =
      '<div class="empty-state" style="grid-column:1/-1"><p>아직 인기글이 없습니다.</p><a class="btn btn--primary" href="write.html">글쓰기</a></div>';
  }

  var recentEl = document.getElementById("recent-posts");
  var recent = all.slice(0, 8);
  if (recent.length > 0) {
    recentEl.innerHTML = recent.map(JDMS.renderPostListItem).join("");
  } else {
    recentEl.innerHTML = '<li class="empty-state"><p>게시글이 없습니다.</p></li>';
  }

  var classPosts = all.filter(function (p) {
    return p.classId;
  }).slice(0, 4);
  var classEl = document.getElementById("class-preview");
  if (classPosts.length > 0) {
    classEl.innerHTML = classPosts.map(JDMS.renderPostCard).join("");
  } else {
    classEl.innerHTML = JDMS.CLASSES.slice(0, 4)
      .map(function (id) {
        return (
          '<a class="post-card" href="class-board.html?class=' +
          id +
          '" style="justify-content:center;align-items:center;text-align:center;">' +
          '<h3 class="post-card__title">' +
          JDMS.formatClassLabel(id) +
          "</h3>" +
          '<p class="post-card__excerpt">이 반 게시판으로 이동</p></a>'
        );
      })
      .join("");
  }
})();
