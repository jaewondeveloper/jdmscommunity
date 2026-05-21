import {
  issueAccessToken,
  issueVerifyToken,
  verifyToken,
  getUserFromRequest,
  getAuthSession,
  setAuthSession,
  deleteAuthSession,
  sendAuthEmail,
} from "./lib/auth.js";
import { json, nowIso, rowToPost } from "./lib/util.js";

const ALLOWED_ORIGINS = [
  "https://jaewondeveloper.github.io",
  "http://localhost:8787",
  "http://127.0.0.1:8787",
];

function withCors(request, response) {
  const origin = request.headers.get("Origin");
  if (origin && ALLOWED_ORIGINS.some((o) => origin === o || origin.startsWith(o))) {
    const r = new Response(response.body, response);
    r.headers.set("Access-Control-Allow-Origin", origin);
    r.headers.set("Access-Control-Allow-Headers", "Content-Type, Authorization");
    r.headers.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    r.headers.set("Vary", "Origin");
    return r;
  }
  return response;
}

function handleOptions(request) {
  return withCors(request, new Response(null, { status: 204 }));
}

export default {
  async fetch(request, env, ctx) {
    if (request.method === "OPTIONS") return handleOptions(request);

    const url = new URL(request.url);
    const path = url.pathname;

    try {
      let res;
      if (path === "/api/auth/send-link" && request.method === "POST") {
        res = await handleSendLink(request, env, url);
      } else if (path === "/api/auth/poll" && request.method === "GET") {
        res = await handlePoll(request, env);
      } else if (path === "/api/auth/verify" && request.method === "GET") {
        res = await handleVerify(request, env);
      } else if (path === "/api/auth/me" && request.method === "GET") {
        res = await handleMe(request, env);
      } else if (path === "/api/posts" && request.method === "GET") {
        res = await handleListPosts(request, env, url);
      } else if (path === "/api/posts" && request.method === "POST") {
        res = await handleCreatePost(request, env);
      } else if (path.match(/^\/api\/posts\/\d+$/) && request.method === "GET") {
        res = await handleGetPost(request, env, path);
      } else if (path.match(/^\/api\/posts\/\d+\/comments$/) && request.method === "POST") {
        res = await handleCreateComment(request, env, path);
      } else {
        res = json({ error: "Not found" }, 404);
      }
      return withCors(request, res);
    } catch (e) {
      return withCors(request, json({ error: e.message || "Server error" }, 500));
    }
  },
};

async function handleSendLink(request, env, url) {
  const data = await request.json();
  const email = (data.email || "").trim().toLowerCase();
  const deviceId = data.device_id;
  if (!email) return json({ success: false, error: "이메일을 입력해 주세요." }, 400);
  if (!deviceId) return json({ success: false, error: "device_id가 필요합니다." }, 400);
  if (!data.bypass && !email.endsWith("@joongdong.ms.kr")) {
    return json({ success: false, error: "학교 이메일(@joongdong.ms.kr)만 사용할 수 있습니다." }, 400);
  }

  const token = await issueVerifyToken(env, email, deviceId);
  const expires = new Date(Date.now() + 10 * 60 * 1000).toISOString();

  await setAuthSession(env, deviceId, {
    email,
    status: "pending",
    user_data: null,
    expires_at: expires,
  });

  const apiOrigin = url.origin;
  const verifyLink = `${apiOrigin}/api/auth/verify?token=${token}`;
  await sendAuthEmail(env, email, verifyLink);

  return json({ success: true });
}

async function handlePoll(request, env) {
  const deviceId = new URL(request.url).searchParams.get("device_id");
  if (!deviceId) return json({ status: "error" }, 400);

  const session = await getAuthSession(env, deviceId);
  if (!session) return json({ status: "not_found" });
  if (session.status === "completed") {
    const userData = session.user_data;
    await deleteAuthSession(env, deviceId);
    return json({ status: "completed", userData });
  }
  return json({ status: "pending" });
}

async function handleVerify(request, env) {
  const token = new URL(request.url).searchParams.get("token");
  if (!token) return new Response("인증 토큰이 없습니다.", { status: 400 });

  try {
    const payload = await verifyToken(env, token);
    const email = payload.email;
    const deviceId = payload.device_id;
    const name = (email || "").split("@")[0].toUpperCase() || "USER";

    const session = await getAuthSession(env, deviceId);
    if (!session) return new Response("유효하지 않거나 만료된 세션입니다.", { status: 403 });

    const access = await issueAccessToken(env, email, name);
    const userData = {
      email,
      name,
      token: access,
      loggedInAt: nowIso(),
    };

    await env.DB.prepare(
      "INSERT OR IGNORE INTO users (email, name, created_at) VALUES (?, ?, ?)"
    )
      .bind(email, name, nowIso())
      .run();

    await setAuthSession(env, deviceId, {
      email,
      status: "completed",
      user_data: userData,
      expires_at: session.expires_at,
    });

    const frontend =
      env.FRONTEND_URL || "https://jaewondeveloper.github.io/jdmscommunity";
    const encoded = btoa(JSON.stringify(userData)).replace(/\+/g, "-").replace(/\//g, "_");
    return Response.redirect(`${frontend}/login.html?auth_success=true&data=${encoded}`, 302);
  } catch {
    return new Response("유효하지 않거나 만료된 링크입니다.", { status: 401 });
  }
}

async function handleMe(request, env) {
  const user = await getUserFromRequest(env, request);
  if (!user) return json({ loggedIn: false });
  return json({ loggedIn: true, user });
}

async function handleListPosts(request, env, url) {
  const category = url.searchParams.get("category");
  const classId = url.searchParams.get("classId");
  const limit = Math.min(parseInt(url.searchParams.get("limit") || "50", 10), 100);

  let sql = "SELECT * FROM posts WHERE 1=1";
  const params = [];
  if (classId) {
    sql += " AND class_id = ?";
    params.push(classId);
  } else {
    sql += " AND (class_id IS NULL OR class_id = '')";
  }
  if (category && category !== "all") {
    sql += " AND category = ?";
    params.push(category);
  }
  sql += " ORDER BY created_at DESC LIMIT ?";
  params.push(limit);

  const { results: rows } = await env.DB.prepare(sql).bind(...params).all();
  const posts = [];
  for (const row of rows) {
    const { results: comments } = await env.DB.prepare(
      "SELECT * FROM comments WHERE post_id = ? ORDER BY created_at ASC"
    )
      .bind(row.id)
      .all();
    posts.push(
      rowToPost(
        row,
        comments.map((c) => ({
          id: String(c.id),
          author: c.author_name,
          content: c.content,
          createdAt: c.created_at,
        }))
      )
    );
  }
  return json({ posts });
}

async function handleGetPost(request, env, path) {
  const postId = parseInt(path.split("/")[3], 10);
  const row = await env.DB.prepare("SELECT * FROM posts WHERE id = ?").bind(postId).first();
  if (!row) return json({ error: "게시글을 찾을 수 없습니다." }, 404);

  await env.DB.prepare("UPDATE posts SET views = views + 1 WHERE id = ?").bind(postId).run();
  const updated = await env.DB.prepare("SELECT * FROM posts WHERE id = ?").bind(postId).first();
  const { results: comments } = await env.DB.prepare(
    "SELECT * FROM comments WHERE post_id = ? ORDER BY created_at ASC"
  )
    .bind(postId)
    .all();

  return json({
    post: rowToPost(
      updated,
      comments.map((c) => ({
        id: String(c.id),
        author: c.author_name,
        content: c.content,
        createdAt: c.created_at,
      }))
    ),
  });
}

async function handleCreatePost(request, env) {
  const user = await getUserFromRequest(env, request);
  if (!user) return json({ error: "로그인이 필요합니다." }, 401);

  const data = await request.json();
  const title = (data.title || "").trim();
  const content = (data.content || "").trim();
  const category = data.category || "free";
  let classId = data.classId || null;
  if (classId === "") classId = null;

  if (!title || !content) return json({ error: "제목과 내용을 입력해 주세요." }, 400);

  const now = nowIso();
  await env.DB.prepare(
    "INSERT OR IGNORE INTO users (email, name, created_at) VALUES (?, ?, ?)"
  )
    .bind(user.email, user.name, now)
    .run();

  const result = await env.DB.prepare(
    `INSERT INTO posts (title, content, author_email, author_name, category, class_id, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`
  )
    .bind(title, content, user.email, user.name, category, classId, now)
    .run();

  const row = await env.DB.prepare("SELECT * FROM posts WHERE id = ?")
    .bind(result.meta.last_row_id)
    .first();

  return json({ post: rowToPost(row, []) }, 201);
}

async function handleCreateComment(request, env, path) {
  const user = await getUserFromRequest(env, request);
  if (!user) return json({ error: "로그인이 필요합니다." }, 401);

  const postId = parseInt(path.split("/")[3], 10);
  const data = await request.json();
  const content = (data.content || "").trim();
  if (!content) return json({ error: "댓글 내용을 입력해 주세요." }, 400);

  const post = await env.DB.prepare("SELECT id FROM posts WHERE id = ?").bind(postId).first();
  if (!post) return json({ error: "게시글을 찾을 수 없습니다." }, 404);

  const now = nowIso();
  const result = await env.DB.prepare(
    `INSERT INTO comments (post_id, author_email, author_name, content, created_at)
     VALUES (?, ?, ?, ?, ?)`
  )
    .bind(postId, user.email, user.name, content, now)
    .run();

  const c = await env.DB.prepare("SELECT * FROM comments WHERE id = ?")
    .bind(result.meta.last_row_id)
    .first();

  return json(
    {
      comment: {
        id: String(c.id),
        author: c.author_name,
        content: c.content,
        createdAt: c.created_at,
      },
    },
    201
  );
}
