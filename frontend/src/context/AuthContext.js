import { createContext, useContext, useEffect, useState, useCallback } from "react";
import { api, setSession, clearSession, getStoredUser, getToken } from "../lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null); // null = checking
  const [ready, setReady] = useState(false);

  const refresh = useCallback(async () => {
    if (!getToken()) { setUser(false); setReady(true); return; }
    try {
      const me = await api("/auth/me");
      setUser(me);
      setSession(getToken(), me);
    } catch {
      clearSession();
      setUser(false);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    const stored = getStoredUser();
    if (stored) setUser(stored);
    refresh();
  }, [refresh]);

  const loginAdmin = async (email, password) => {
    const res = await api("/auth/admin/login", { method: "POST", auth: false, body: { email, password } });
    setSession(res.token, res.user); setUser(res.user); return res.user;
  };
  const loginPlayer = async (username, password) => {
    const res = await api("/auth/player/login", { method: "POST", auth: false, body: { username, password } });
    setSession(res.token, res.user); setUser(res.user); return res.user;
  };
  const register = async (username, password) => {
    const res = await api("/auth/player/register", { method: "POST", auth: false, body: { username, password } });
    setSession(res.token, res.user); setUser(res.user); return res.user;
  };
  const forgot = async (email) =>
    api("/auth/admin/forgot-password", { method: "POST", auth: false, body: { email } });

  const logout = () => { clearSession(); setUser(false); };

  return (
    <AuthContext.Provider value={{ user, ready, loginAdmin, loginPlayer, register, forgot, logout, isAdmin: user?.role === "admin", refresh }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
