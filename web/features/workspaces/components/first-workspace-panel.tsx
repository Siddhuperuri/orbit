"use client";

import { FolderPlus, MessageSquareText, Upload, type LucideIcon } from "lucide-react";

import { OrbitIcon } from "@/components/feedback/empty-state";
import { KineticText } from "@/components/ui/kinetic-text";
import { useWorkspaces } from "@/features/workspaces/api/use-workspaces";
import { CreateWorkspaceForm } from "@/features/workspaces/components/create-workspace-form";

const STEPS: Array<{ icon: LucideIcon; title: string; body: string }> = [
  { icon: FolderPlus, title: "Name it", body: "A team, a client, or a research topic." },
  { icon: Upload, title: "Add documents", body: "PDFs, Markdown, and plain text." },
  { icon: MessageSquareText, title: "Ask", body: "Answers cite the passages they use." },
];

/**
 * Creating a workspace. On first run nothing else works until one exists, so this
 * is the whole screen and says what comes after; for someone who already has
 * workspaces it is simply "a new one".
 */
export function FirstWorkspacePanel() {
  const { data: workspaces } = useWorkspaces();
  const first = workspaces !== undefined && workspaces.length === 0;

  return (
    <div className="@container mx-auto w-full max-w-2xl px-4 py-14 sm:py-20">
      <div className="text-center">
        <OrbitIcon icon={FolderPlus} className="enter mb-8" />
        <h1 className="text-fg font-serif text-[clamp(2.5rem,1rem+6cqi,4.5rem)] leading-[0.95] font-light tracking-[-0.035em] text-balance">
          <KineticText text={first ? "Create your first workspace" : "Create a workspace"} />
        </h1>
        <p className="enter enter-2 text-fg-muted mx-auto mt-5 max-w-md text-base">
          A workspace holds a set of documents and the conversations about them. Everything you
          upload stays inside it, visible only to its members.
        </p>
      </div>

      <div className="enter enter-3 border-line bg-canvas mx-auto mt-10 max-w-lg border p-7">
        <CreateWorkspaceForm />
      </div>

      {first ? (
        <ol
          className="enter enter-4 border-line bg-canvas mt-10 grid border-y sm:grid-cols-3"
          aria-label="What happens next"
        >
          {STEPS.map(({ icon: Icon, title, body }, index) => (
            <li key={title} className="border-line p-5 text-left sm:not-first:border-l">
              <span className="label-micro text-fg-subtle flex items-center justify-between">
                {String(index + 1).padStart(2, "0")}
                <Icon className="text-accent size-4" strokeWidth={1.5} aria-hidden="true" />
              </span>
              <p className="text-fg mt-6 font-serif text-lg font-light tracking-tight">{title}</p>
              <p className="text-fg-muted mt-1 text-sm">{body}</p>
            </li>
          ))}
        </ol>
      ) : null}
    </div>
  );
}
