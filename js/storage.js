/**
 * 로컬 스토리지 기반 게시글·댓글 (서버 없이 file:// 에서도 동작)
 */
(function (global) {
  const STORAGE_KEY = "jdms_community_posts";

  const SAMPLE_POSTS = [
    {
      id: "1",
      title: "중동중학교 커뮤니티에 오신 것을 환영합니다",
      content:
        "안녕하세요! 이곳은 중동중학교 학생·교사를 위한 커뮤니티입니다.\n\n전체 게시판에서 자유롭게 글을 올리거나, 반별 게시판에서 우리 반 이야기를 나눠 보세요.",
      author: "관리자",
      category: "notice",
      classId: null,
      createdAt: new Date().toISOString(),
      comments: [],
    },
    {
      id: "2",
      title: "1학년 3반 체육대회 준비 모임",
      content:
        "이번 주 금요일 방과 후에 체육대회 연습할 분 구해요! 준비물이나 역할 분담 같이 정하면 좋겠어요.",
      author: "학생A",
      category: "free",
      classId: "1-3",
      createdAt: new Date(Date.now() - 86400000).toISOString(),
      likes: 12,
      views: 84,
      comments: [
        {
          id: "c1",
          author: "학생B",
          content: "저도 참여할게요!",
          createdAt: new Date(Date.now() - 3600000).toISOString(),
        },
        {
          id: "c2",
          author: "학생C",
          content: "금요일 몇 시에 모이나요?",
          createdAt: new Date(Date.now() - 1800000).toISOString(),
        },
      ],
    },
    {
      id: "3",
      title: "수학 숙제 3번 풀이 방법 질문",
      content: "교과서 45쪽 3번인데 식이 안 맞아요. 힌트만 주실 분 계신가요?",
      author: "2학년5반",
      category: "qna",
      classId: null,
      createdAt: new Date(Date.now() - 172800000).toISOString(),
      likes: 5,
      views: 120,
      comments: [],
    },
    {
      id: "4",
      title: "동아리 신규 부원 모집합니다",
      content:
        "밴드부에서 신입 부원을 모집합니다. 악기 경험 없어도 괜찮아요. 3월 첫 주 오디션 예정!",
      author: "밴드부",
      category: "notice",
      classId: null,
      createdAt: new Date(Date.now() - 432000000).toISOString(),
      likes: 28,
      views: 310,
      comments: [
        {
          id: "c3",
          author: "1학년1반",
          content: "지원 방법 알려주세요!",
          createdAt: new Date(Date.now() - 86400000).toISOString(),
        },
      ],
    },
  ];

  const CATEGORIES = {
    all: "전체",
    notice: "공지",
    free: "자유",
    qna: "질문",
  };

  const CLASSES = [
    "1-1", "1-2", "1-3", "1-4", "1-5", "1-6", "1-7", "1-8", "1-9",
    "2-1", "2-2", "2-3", "2-4", "2-5", "2-6", "2-7", "2-8", "2-9",
    "3-1", "3-2", "3-3", "3-4", "3-5", "3-6", "3-7", "3-8", "3-9",
  ];

  function getPosts() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(SAMPLE_POSTS));
        return SAMPLE_POSTS.slice();
      }
      return JSON.parse(raw);
    } catch (e) {
      return SAMPLE_POSTS.slice();
    }
  }

  function savePosts(posts) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(posts));
  }

  function getPostById(id) {
    return getPosts().find(function (p) {
      return p.id === id;
    }) || null;
  }

  function addPost(post) {
    const posts = getPosts();
    posts.unshift(post);
    savePosts(posts);
    return post;
  }

  function addComment(postId, comment) {
    const posts = getPosts();
    const post = posts.find(function (p) {
      return p.id === postId;
    });
    if (!post) return null;
    if (!post.comments) post.comments = [];
    post.comments.push(comment);
    savePosts(posts);
    return comment;
  }

  function generateId() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  function formatClassLabel(classId) {
    if (!classId) return "";
    const parts = classId.split("-");
    return parts[0] + "학년 " + parts[1] + "반";
  }

  function formatDate(iso) {
    const d = new Date(iso);
    return d.toLocaleDateString("ko-KR", {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function timeAgo(iso) {
    const diff = Date.now() - new Date(iso).getTime();
    const min = Math.floor(diff / 60000);
    if (min < 1) return "방금 전";
    if (min < 60) return min + "분 전";
    const hr = Math.floor(min / 60);
    if (hr < 24) return hr + "시간 전";
    const day = Math.floor(hr / 24);
    if (day < 30) return day + "일 전";
    const month = Math.floor(day / 30);
    if (month < 12) return month + "개월 전";
    return Math.floor(month / 12) + "년 전";
  }

  global.JDMS = global.JDMS || {};
  Object.assign(global.JDMS, {
    getPosts: getPosts,
    savePosts: savePosts,
    getPostById: getPostById,
    addPost: addPost,
    addComment: addComment,
    generateId: generateId,
    CATEGORIES: CATEGORIES,
    CLASSES: CLASSES,
    formatClassLabel: formatClassLabel,
    formatDate: formatDate,
    timeAgo: timeAgo,
  });
})(window);
