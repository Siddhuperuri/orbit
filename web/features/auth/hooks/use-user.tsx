"use client";

import { createContext, useContext } from "react";

import type { User } from "@/features/auth/types";

const UserContext = createContext<User | null>(null);

export const UserProvider = UserContext.Provider;

/**
 * The signed-in user, guaranteed present. Only valid below `AuthGate`, which does
 * not render its children until `/auth/me` has produced a user -- so components
 * never carry a "what if there is no user" branch that cannot actually happen.
 */
export function useUser(): User {
  const user = useContext(UserContext);
  if (!user) throw new Error("useUser must be used inside <AuthGate>.");
  return user;
}
