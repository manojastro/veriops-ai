import type { Metadata } from "next";
import type { ReactNode } from "react";

import { AuthProvider } from "@/lib/hooks";

import "./globals.css";

export const metadata: Metadata = {
  title: "VeriOps AI",
  description: "Autonomous production investigation and verified remediation",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
