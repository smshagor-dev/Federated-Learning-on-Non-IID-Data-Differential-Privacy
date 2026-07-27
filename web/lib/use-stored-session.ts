"use client";

import { useEffect, useState } from "react";

import type { AuthSession } from "@/types/api";

// Every existing client feature (features/audit/audit-console.tsx and
// the run dashboard panels) re-reads and re-parses this same
// localStorage key itself. The Security Center adds five more consoles
// that all need the same session, which is enough repetition to justify
// pulling the read/parse step into one hook rather than copying it a
// fifth and sixth time.
const SESSION_STORAGE_KEY = "fl-platform-session";

export function useStoredSession(): AuthSession | null {
  const [session, setSession] = useState<AuthSession | null>(null);

  useEffect(() => {
    const cached = window.localStorage.getItem(SESSION_STORAGE_KEY);
    if (!cached) {
      return;
    }
    try {
      setSession(JSON.parse(cached) as AuthSession);
    } catch {
      window.localStorage.removeItem(SESSION_STORAGE_KEY);
    }
  }, []);

  return session;
}
