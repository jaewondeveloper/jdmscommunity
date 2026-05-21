export function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

export function nowIso() {
  return new Date().toISOString();
}

export function rowToPost(row, comments = []) {
  return {
    id: String(row.id),
    title: row.title,
    content: row.content,
    author: row.author_name,
    authorEmail: row.author_email,
    category: row.category,
    classId: row.class_id || null,
    likes: row.likes,
    views: row.views,
    createdAt: row.created_at,
    comments,
  };
}
