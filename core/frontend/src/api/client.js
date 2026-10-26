import { useSession } from "../stores/session";

export async function api(path, options = {}) {
  const session = useSession();
  const headers = { ...(options.headers || {}) };
  if (session.token) headers.Authorization = `Bearer ${session.token}`;
  const resp = await fetch(path, { ...options, headers });
  if (resp.status === 401) {
    session.logout();
    throw new Error("unauthorized");
  }
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(detail || resp.statusText);
  }
  return resp.json();
}

// media files sit behind the login gate; <img> cannot send headers so
// the bearer token rides along as a query parameter
export function mediaUrl(path) {
  if (!path) return "";
  const session = useSession();
  const sep = path.includes("?") ? "&" : "?";
  return session.token
    ? `/media/${path}${sep}t=${encodeURIComponent(session.token)}`
    : `/media/${path}`;
}
