import type { Metadata } from "next";
import Link from "next/link";
import { ThemeToggle } from "@/components/ThemeToggle";
import "./globals.css";

export const metadata: Metadata = {
  title: "Option research",
  description: "Local viewer for the sophie-option-research results store",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <header className="border-b border-hair bg-surface">
          <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
            <div className="flex items-baseline gap-3">
              <Link href="/" className="text-base font-semibold">Option research</Link>
              <span className="text-xs text-ink2">local · read-only</span>
              <Link href="/data" className="ml-3 text-sm underline decoration-hair underline-offset-2">Data availability</Link>
            </div>
            <ThemeToggle />
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
