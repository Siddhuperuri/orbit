import type { Metadata } from "next";

import { MembersPage } from "@/features/workspaces/components/members-page";

export const metadata: Metadata = { title: "Members" };

export default function MembersRoute() {
  return <MembersPage />;
}
