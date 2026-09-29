"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

import { api, getToken, setToken } from "@/lib/api";
import type { User } from "@/lib/types";

/** Fetch + optional polling. Keeps the previous data while refreshing. */
export function usePoll<T>(path: string | null, intervalMs = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const pathRef = useRef(path);
  pathRef.current = path;

  const refresh = useCallback(async () => {
    if (!pathRef.current) return;
    try {
      const result = await api<T>(pathRef.current);
      setData(result);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!path) return;
    setLoading(true);
    void refresh();
    if (!intervalMs) return;
    const id = window.setInterval(() => void refresh(), intervalMs);
    return () => window.clearInterval(id);
  }, [path, intervalMs, refresh]);

  return { data, error, loading, refresh };
}

interface AuthState {
  user: User | null;
  ready: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  can: (permission: string) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (!getToken()) {
      setReady(true);
      return;
    }
    api<User>("/api/auth/me")
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setReady(true));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const result = await api<{ access_token: string; user: User }>("/api/auth/login", {
      method: "POST",
      json: { username, password },
    });
    setToken(result.access_token);
    setUser(result.user);
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    window.location.href = "/login";
  }, []);

  const can = useCallback(
    (permission: string) => !!user && (user.permissions.includes(permission) || user.role === "administrator"),
    [user],
  );

  return <AuthContext.Provider value={{ user, ready, login, logout, can }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
