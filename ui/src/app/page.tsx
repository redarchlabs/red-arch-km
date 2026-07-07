import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";

import { Landing } from "@/components/landing/Landing";

/**
 * Root route. Signed-in users are sent straight to the app (preserving the old
 * behavior); logged-out visitors get the public marketing landing page instead
 * of an immediate bounce to the Clerk sign-in widget.
 *
 * `/` is listed in middleware's isPublicRoute so this handler runs without a
 * session for logged-out visitors.
 */
export default async function Home() {
  const { userId } = await auth();
  if (userId) redirect("/documents");
  return <Landing />;
}
