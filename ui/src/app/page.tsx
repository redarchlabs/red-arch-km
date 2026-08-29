import { auth } from "@clerk/nextjs/server";
import { redirect } from "next/navigation";

import { Landing } from "@/components/landing/Landing";
import { isBypassEnabled } from "@/lib/auth/bypass";

/**
 * Root route. Signed-in users are sent straight to the app (preserving the old
 * behavior); logged-out visitors get the public marketing landing page instead
 * of an immediate bounce to the Clerk sign-in widget.
 *
 * `/` is listed in middleware's isPublicRoute so this handler runs without a
 * session for logged-out visitors.
 */
export default async function Home() {
  // Offline bypass has no server-side session to read, and calling Clerk's auth() with no
  // internet stalls the request. Show the landing page; the authenticated layout sends a
  // signed-in operator on from there.
  if (isBypassEnabled()) {
    return <Landing />;
  }
  const { userId } = await auth();
  if (userId) redirect("/documents");
  return <Landing />;
}
