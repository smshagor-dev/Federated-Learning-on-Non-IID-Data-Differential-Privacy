import type { ReactNode } from "react";
import "./globals.css";

export const metadata = {
  title: "Federated Learning Super System",
  description: "Enterprise dashboard scaffold for federated learning operations",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
