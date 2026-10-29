"use client";

import { createContext, useContext, useEffect, useState } from "react";

import { api, type Me } from "@/lib/api";

type SessionState = { me: Me | null; loading: boolean };

const SessionContext = createContext<SessionState>({ me: null, loading: true });

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<SessionState>({ me: null, loading: true });
  useEffect(() => {
    api
      .me()
      .then((me) => setState({ me, loading: false }))
      .catch(() => setState({ me: null, loading: false }));
  }, []);
  return <SessionContext.Provider value={state}>{children}</SessionContext.Provider>;
}

/** Identity for display only. Every permission is enforced by the backend. */
export const useSession = () => useContext(SessionContext);
