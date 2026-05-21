(function () {
  JDMS.initShell("community", "board");
  document.getElementById("footer").innerHTML = JDMS.renderFooter();

  var params = new URLSearchParams(location.search);
  var postId = params.get("id");
  var contentEl = document.getElementById("post-content");
  var commentListEl = document.getElementById("comment-list");
  var commentCountEl = document.getElementById("comment-count");
  var backLink = document.getElementById("back-link");
  var commentForm = document.getElementById("comment-form");
  var commentLoginHint = document.getElementById("comment-login-hint");

  if (!postId) {
    contentEl.innerHTML = '<div class="empty-state"><p>게시글을 찾을 수 없습니다.</p></div>';
    return;
  }

  function renderComments(comments) {
    commentCountEl.textContent = "(" + comments.length + ")";
    if (comments.length === 0) {
      commentListEl.innerHTML =
        '<li class="comments-empty"><p>첫 댓글을 남겨 보세요.</p></li>';
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
            '<p class="comment-item__body">' +
            JDMS.escapeHtml(c.content) +
            "</p></li>"
          );
        })
        .join("");
    }
  }

  function syncCommentForm() {
    if (JDMSAuth.isLoggedIn()) {
      commentForm.hidden = false;
      if (commentLoginHint) commentLoginHint.hidden = true;
    } else {
      commentForm.hidden = true;
      if (commentLoginHint) commentLoginHint.hidden = false;
    }
  }

  JDMS.getPostById(postId)
    .then(function (post) {
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
        "</span><span>" +
        JDMS.formatDate(post.createdAt) +
        "</span><span>" +
        JDMS.timeAgo(post.createdAt) +
        "</span></div>" +
        '<div class="article-card__body">' +
        JDMS.escapeHtml(post.content) +
        "</div></article>";

      renderComments(post.comments || []);
      syncCommentForm();
    })
    .catch(function () {
      contentEl.innerHTML = '<div class="empty-state"><p>게시글을 불러올 수 없습니다.</p></div>';
    });

  commentForm.addEventListener("submit", function (e) {
    e.preventDefault();
    if (!JDMSAuth.isLoggedIn()) {
      location.href = "login.html";
      return;
    }
    var content = document.getElementById("comment-body").value.trim();
    if (!content) return;

    JDMS.addComment(postId, content)
      .then(function () {
        document.getElementById("comment-body").value = "";
        return JDMS.getPostById(postId);
      })
      .then(function (post) {
        renderComments(post.comments || []);
      })
      .catch(function (err) {
        alert(err.message || "댓글 등록 실패");
      });
  });
})();
