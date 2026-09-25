import { AppShell } from "@/components/layout/app-shell";
import { AuthGate } from "@/features/auth/components/auth-gate";

/**
 * Everything behind sign-in. The gate resolves the user before the shell renders;
 * see `AuthGate` for why that is a client-side check.
 */
export default function AuthenticatedLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthGate>
      <AppShell>{children}</AppShell>
    </AuthGate>
  );
}
