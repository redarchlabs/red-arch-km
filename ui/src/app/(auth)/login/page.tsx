"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { useAuth } from "@/context/AuthContext";

export default function LoginPage() {
  const router = useRouter();
  const { isAuthenticated, isInitializing } = useAuth();

  useEffect(() => {
    if (!isInitializing && isAuthenticated) {
      router.replace("/documents");
    }
  }, [isAuthenticated, isInitializing, router]);

  return (
    <div className="flex min-h-screen items-center justify-center">
      <div className="text-center">
        <h1 className="text-2xl font-semibold">Red Arch Knowledge Management</h1>
        <p className="mt-2 text-muted-foreground">Redirecting to sign-in…</p>
      </div>
    </div>
  );
}
