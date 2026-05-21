(function () {
  JDMS.initShell("community", "board");
  document.getElementById("footer").innerHTML = JDMS.renderFooter();

  var params = new URLSearchParams(location.search);
  var postId = params.get("id");
  var contentEl = document.getElementById("post-content");
  var commentListEl = document.getElementById("comment-list");
  var commentCountEl = document.getElementById("comment-count");
  var backLink = document.getElementById("back-link");

  if (!postId) {
    contentEl.innerHTML = '<div class="empty-state"><p>게시글을 찾을 수 없습니다.</p></div>';
    return;
  }

  renderPost(postId);

  function renderPost(id) {
    var post = JDMS.getPostById(id);
    if (!post) {
      contentEl.innerHTML = '<div class="empty-state"><p>게시글을 찾을 수 없습니다.</p></div>';
      return;
    }

    backLink.href = post.classId ? "class-board.html?class=" + post.classId : "board.html";

    var classInfo = post.classId
      ? '<span class="badge badge--class">' + JDMS.formatClassLabel(post.classId) + "</span>"
      : "";

    contentEl.innerHTML =
      '<article class="article-card">' +
      '<div style="margin-bottom:0.5rem;">' +
      JDMS.categoryBadge(post.category) +
      " " +
      classInfo +
      "</div>" +
      '<h1 class="article-card__title">' +
      JDMS.escapeHtml(post.title) +
      "</h1>" +
      '<div class="article-card__meta">' +
      "<span>" +
      JDMS.escapeHtml(post.author) +
      "</span>" +
      "<span>" +
      JDMS.formatDate(post.createdAt) +
      "</span>" +
      "<span>" +
      JDMS.timeAgo(post.createdAt) +
      "</span>" +
      "<span>" +
      (JDMS.CATEGORIES[post.category] || post.category) +
      "</span></div>" +
      '<div class="article-card__body">' +
      JDMS.escapeHtml(post.content) +
      "</div></article>";

    renderComments(post);
  }

  function renderComments(post) {
    var comments = post.comments || [];
    commentCountEl.textContent = "(" + comments.length + ")";

    if (comments.length === 0) {
      commentListEl.innerHTML =
        '<li class="empty-state" style="border:none;padding:1rem 0;"><p>첫 댓글을 남겨 보세요.</p></li>';
    } else {
      commentListEl.innerHTML = comments
        .map(function (c) {
          return (
            '<li class="comment-item">' +
            '<div class="comment-item__author">' +
            JDMS.escapeHtml(c.author) +
            "</div>" +
            '<div class="comment-item__date">' +
            JDMS.timeAgo(c.createdAt) +
            "</div>" +
            "<p>" +
            JDMS.escapeHtml(c.content) +
            "</p></li>"
          );
        })
        .join("");
    }
  }

  document.getElementById("comment-form").addEventListener("submit", function (e) {
    e.preventDefault();
    if (!postId) return;

    var author = document.getElementById("comment-author").value.trim();
    var content = document.getElementById("comment-body").value.trim();
    if (!author || !content) return;

    JDMS.addComment(postId, {
      id: JDMS.generateId(),
      author: author,
      content: content,
      createdAt: new Date().toISOString(),
    });

    document.getElementById("comment-body").value = "";
    renderComments(JDMS.getPostById(postId));
  });
})();
