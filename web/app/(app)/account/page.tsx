import type { Metadata } from "next";

import { AccountPageContent } from "@/features/settings/components/account-page-content";

export const metadata: Metadata = { title: "Account" };

export default function AccountPage() {
  return <AccountPageContent />;
}
