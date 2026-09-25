import { RootRedirect } from "@/features/workspaces/components/root-redirect";

/**
 * `/` has no content of its own: it sends the user to the workspace they were last
 * in, or to first-run setup if they have none.
 */
export default function HomePage() {
  return <RootRedirect />;
}
