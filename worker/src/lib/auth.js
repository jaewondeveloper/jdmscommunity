import { SignJWT, jwtVerify } from "jose";

function secretKey(env) {
  return new TextEncoder().encode(env.JWT_SECRET || "jdms_community_jwt_secret");
}

export async function issueAccessToken(env, email, name) {
  return await new SignJWT({ email, name })
    .setProtectedHeader({ alg: "HS256" })
    .setExpirationTime("30d")
    .sign(secretKey(env));
}

export async function issueVerifyToken(env, email, deviceId) {
  return await new SignJWT({ email, device_id: deviceId })
    .setProtectedHeader({ alg: "HS256" })
    .setExpirationTime("10m")
    .sign(secretKey(env));
}

export async function verifyToken(env, token) {
  const { payload } = await jwtVerify(token, secretKey(env));
  return payload;
}

export async function getUserFromRequest(env, request) {
  const auth = request.headers.get("Authorization") || "";
  const token = auth.startsWith("Bearer ") ? auth.slice(7) : "";
  if (!token) return null;
  try {
    const data = await verifyToken(env, token);
    return {
      email: data.email,
      name: data.name || String(data.email).split("@")[0],
    };
  } catch {
    return null;
  }
}

export async function getAuthSession(env, deviceId) {
  const row = await env.DB.prepare("SELECT * FROM auth_sessions WHERE device_id = ?")
    .bind(deviceId)
    .first();
  if (!row) return null;
  return {
    ...row,
    user_data: row.user_data ? JSON.parse(row.user_data) : null,
  };
}

export async function setAuthSession(env, deviceId, data) {
  await env.DB.prepare(
    `INSERT INTO auth_sessions (device_id, email, status, user_data, expires_at)
     VALUES (?, ?, ?, ?, ?)
     ON CONFLICT(device_id) DO UPDATE SET
       email=excluded.email, status=excluded.status,
       user_data=excluded.user_data, expires_at=excluded.expires_at`
  )
    .bind(
      deviceId,
      data.email,
      data.status,
      data.user_data ? JSON.stringify(data.user_data) : null,
      data.expires_at || null
    )
    .run();
}

export async function deleteAuthSession(env, deviceId) {
  await env.DB.prepare("DELETE FROM auth_sessions WHERE device_id = ?").bind(deviceId).run();
}

export async function sendAuthEmail(env, email, verifyLink) {
  const html = `
    <div style="font-family:sans-serif;max-width:500px;margin:0 auto;padding:20px;">
      <h2 style="color:#000;">JDMS 커뮤니티 로그인</h2>
      <p>아래 버튼을 눌러 로그인을 완료해 주세요.</p>
      <a href="${verifyLink}" style="display:inline-block;padding:12px 24px;background:#000;color:#fff;text-decoration:none;border-radius:12px;font-weight:bold;">로그인 완료하기</a>
      <p style="font-size:12px;color:#888;">10분간 유효합니다.</p>
    </div>`;

  if (env.RESEND_API_KEY) {
    const from = env.FROM_EMAIL || "onboarding@resend.dev";
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.RESEND_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        from: `JDMS Community <${from}>`,
        to: [email],
        subject: "[JDMS 커뮤니티] 로그인 인증 링크",
        html,
      }),
    });
    if (res.ok) return true;
  }

  if (env.SENDGRID_API_KEY) {
    await fetch("https://api.sendgrid.com/v3/mail/send", {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.SENDGRID_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        personalizations: [
          { to: [{ email }], subject: "[JDMS 커뮤니티] 로그인 인증 링크" },
        ],
        from: { email: env.FROM_EMAIL || "corerepublix@gmail.com", name: "JDMS Community" },
        content: [{ type: "text/html", value: html }],
      }),
    });
    return true;
  }

  return false;
}
