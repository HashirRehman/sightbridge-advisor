import type { Metadata } from "next";

import { AuditView } from "@/features/admin/audit-view";

export const metadata: Metadata = { title: "Audit log · Sightbridge Advisor Intelligence" };

export default function AdminPage() {
  return <AuditView />;
}
