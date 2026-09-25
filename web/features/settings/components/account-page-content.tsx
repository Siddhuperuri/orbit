"use client";

import { LogOut } from "lucide-react";

import { PageContainer } from "@/components/layout/page-container";
import { PageHeader } from "@/components/layout/page-header";
import { SettingsSection } from "@/components/layout/settings-section";
import { Button } from "@/components/ui/button";
import { useLogout } from "@/features/auth/api/use-auth-mutations";
import { AppearanceSection } from "@/features/settings/components/appearance-section";
import { ProfileSection } from "@/features/settings/components/profile-section";
import { AboutSection } from "@/features/system/components/about-section";

export function AccountPageContent() {
  const logout = useLogout();

  return (
    <PageContainer width="narrow">
      <PageHeader title="Account" description="Your profile and how ORBIT looks on this device." />

      <SettingsSection title="Profile">
        <ProfileSection />
      </SettingsSection>

      <SettingsSection title="Appearance">
        <AppearanceSection />
      </SettingsSection>

      <SettingsSection title="Session" description="Signing out ends your session on this device.">
        <Button onClick={() => logout.mutate()} loading={logout.isPending}>
          <LogOut aria-hidden="true" />
          {logout.isPending ? "Signing out" : "Sign out"}
        </Button>
      </SettingsSection>

      <SettingsSection title="About">
        <AboutSection />
      </SettingsSection>
    </PageContainer>
  );
}
