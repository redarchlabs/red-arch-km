import Image from "next/image";
import Link from "next/link";
import type { ReactNode } from "react";

/**
 * Branded frame shared by the /login and /sign-up pages. The Clerk <SignIn/> /
 * <SignUp/> widgets render in `children` unchanged; this only adds the brand,
 * a short value proposition, and a link back to the public site — replacing the
 * previous bare, centered form.
 */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-screen bg-background text-foreground lg:grid-cols-2">
      {/* Brand / value panel — above the form on mobile, beside it on desktop. */}
      <aside className="flex flex-col justify-between gap-8 border-b border-border p-8 lg:border-b-0 lg:border-r lg:p-12">
        <Link href="/" className="flex items-center gap-2">
          <Image
            src="/images/redarch-logo-small.png"
            alt="Red Arch"
            width={32}
            height={32}
            priority
          />
          <span className="text-base font-semibold tracking-tight">
            Red Arch Knowledge Manager
          </span>
        </Link>

        <div className="max-w-md">
          <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
            Your organization&apos;s source of truth.
          </h1>
          <ul className="mt-6 space-y-3 text-sm text-muted-foreground">
            <li>• Centralize documents and make them searchable and answerable.</li>
            <li>• Ask questions and get cited answers grounded in your own content.</li>
            <li>• Fine-grained access by region, department, role, and group.</li>
          </ul>
        </div>

        <Link href="/help" className="text-sm text-muted-foreground underline underline-offset-4">
          Learn more about the platform →
        </Link>
      </aside>

      {/* Clerk widget */}
      <div className="flex items-center justify-center p-6">{children}</div>
    </div>
  );
}
