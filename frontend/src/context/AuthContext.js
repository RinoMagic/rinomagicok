import { createContext, useContext, useEffect, useState, useCallback } from "react";
import api, { setToken, clearToken, getToken } from "../lib/api";

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null); // null = checking
  const [ready, setReady] = useState(false);

  const loadMe = useCallback(async () => {
    if (!getToken()) {
      setUser(false);
      setReady(true);
      return;
    }
    try {
      const { data } = await api.get("/auth/me");
      setUser(data);
    } catch (_) {
      clearToken();
      setUser(false);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    loadMe();
  }, [loadMe]);

  const login = async (identifier, password) => {
    const { data } = await api.post("/auth/login", { identifier, password });
    setToken(data.token);
    setUser(data.user);
    return data.user;
  };

  const logout = () => {
    clearToken();
    setUser(false);
  };

  return (
    <AuthContext.Provider value={{ user, ready, login, logout, isAdmin: user?.role === "admin" }}>
      {children}
    </AuthContext.Provider>
  );
}

export const useAuth = () => useContext(AuthContext);
