const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
export const API = `${BACKEND_URL}/api`;

const TOKEN_KEY = "schedinabar_token";
const USER_KEY = "schedinabar_user";

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setSession = (token, user) => {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
};
export const clearSession = () => {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
};
export const getStoredUser = () => {
  try { return JSON.parse(localStorage.getItem(USER_KEY)); } catch { return null; }
};

function extractError(status, data) {
  let msg = `Errore ${status}`;
  if (data && typeof data === "object" && data.detail) {
    if (typeof data.detail === "string") msg = data.detail;
    else if (Array.isArray(data.detail)) {
      const f = data.detail[0];
      if (f?.msg) msg = String(f.msg).replace(/^Value error,\s*/i, "");
    }
  }
  return msg;
}

export async function api(path, opts = {}) {
  const { method = "GET", body, auth = true } = opts;
  const headers = { "Content-Type": "application/json" };
  if (auth) {
    const t = getToken();
    if (t) headers.Authorization = `Bearer ${t}`;
  }
  const res = await fetch(`${API}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  const ct = res.headers.get("content-type") || "";
  const data = ct.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) throw new Error(extractError(res.status, data));
  return data;
}
