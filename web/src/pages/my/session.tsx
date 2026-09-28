import { createContext, useContext } from "react";
import type { Account } from "../../api";

export interface CustomerSession {
  account: Account;
  setAccount: (a: Account) => void;
  /** Re-read the account (e.g. after verifying the email in another tab). */
  reload: () => Promise<void>;
  signOut: () => Promise<void>;
}

export const CustomerCtx = createContext<CustomerSession | null>(null);

/** The signed-in customer (only inside the /my area). */
export function useCustomer(): CustomerSession {
  const ctx = useContext(CustomerCtx);
  if (!ctx) throw new Error("useCustomer outside the customer area");
  return ctx;
}

/** A same-origin path to return to after sign-in (never an external URL). */
export function safeNext(next: string | null | undefined, fallback: string): string {
  return next && next.startsWith("/") && !next.startsWith("//") ? next : fallback;
}
