(function (global) {
  var API_BASE = (global.JDMS_CONFIG && global.JDMS_CONFIG.API_BASE) || "";

  var CATEGORIES = {
    all: "전체",
    notice: "공지",
    free: "자유",
    qna: "질문",
  };

  var CLASSES = [
    "1-1", "1-2", "1-3", "1-4", "1-5", "1-6", "1-7", "1-8", "1-9",
    "2-1", "2-2", "2-3", "2-4", "2-5", "2-6", "2-7", "2-8", "2-9",
    "3-1", "3-2", "3-3", "3-4", "3-5", "3-6", "3-7", "3-8", "3-9",
  ];

  function formatClassLabel(classId) {
    if (!classId) return "";
    var parts = classId.split("-");
    return parts[0] + "학년 " + parts[1] + "반";
  }

  function formatDate(iso) {
    var d = new Date(iso);
    return d.toLocaleDateString("ko-KR", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function timeAgo(iso) {
    var diff = Date.now() - new Date(iso).getTime();
    var min = Math.floor(diff / 60000);
    if (min < 1) return "방금 전";
    if (min < 60) return min + "분 전";
    var hr = Math.floor(min / 60);
    if (hr < 24) return hr + "시간 전";
    var day = Math.floor(hr / 24);
    if (day < 30) return day + "일 전";
    var month = Math.floor(day / 30);
    if (month < 12) return month + "개월 전";
    return Math.floor(month / 12) + "년 전";
  }

  function getAuthHeaders() {
    var user = global.JDMSAuth && global.JDMSAuth.getUser();
    if (!user || !user.token) return {};
    return { Authorization: "Bearer " + user.token };
  }

  function apiFetch(path, options) {
    options = options || {};
    var headers = Object.assign({ "Content-Type": "application/json" }, getAuthHeaders(), options.headers || {});
    return fetch(API_BASE + path, {
      method: options.method || "GET",
      headers: headers,
      body: options.body ? JSON.stringify(options.body) : undefined,
    }).then(function (res) {
      return res.json().then(function (data) {
        if (!res.ok) {
          var err = new Error(data.error || "요청에 실패했습니다.");
          err.status = res.status;
          throw err;
        }
        return data;
      });
    });
  }

  function getPosts(opts) {
    opts = opts || {};
    var q = [];
    if (opts.category && opts.category !== "all") q.push("category=" + encodeURIComponent(opts.category));
    if (opts.classId) q.push("classId=" + encodeURIComponent(opts.classId));
    if (opts.limit) q.push("limit=" + opts.limit);
    var qs = q.length ? "?" + q.join("&") : "";
    return apiFetch("/api/posts" + qs).then(function (d) {
      return d.posts || [];
    });
  }

  function getPostById(id) {
    return apiFetch("/api/posts/" + id).then(function (d) {
      return d.post;
    });
  }

  function addPost(post) {
    return apiFetch("/api/posts", {
      method: "POST",
      body: {
        title: post.title,
        content: post.content,
        category: post.category,
        classId: post.classId,
      },
    }).then(function (d) {
      return d.post;
    });
  }

  function addComment(postId, content) {
    return apiFetch("/api/posts/" + postId + "/comments", {
      method: "POST",
      body: { content: content },
    }).then(function (d) {
      return d.comment;
    });
  }

  global.JDMS = global.JDMS || {};
  Object.assign(global.JDMS, {
    getPosts: getPosts,
    getPostById: getPostById,
    addPost: addPost,
    addComment: addComment,
    CATEGORIES: CATEGORIES,
    CLASSES: CLASSES,
    formatClassLabel: formatClassLabel,
    formatDate: formatDate,
    timeAgo: timeAgo,
    apiFetch: apiFetch,
  });
})(window);
