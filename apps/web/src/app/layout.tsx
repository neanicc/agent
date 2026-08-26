import type { Metadata, Viewport } from "next";
import { cookies } from "next/headers";

import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "@fontsource/ibm-plex-sans/400.css";
import "@fontsource/ibm-plex-sans/500.css";
import "@fontsource/ibm-plex-sans/600.css";
import "@fontsource/space-grotesk/600.css";
import "@fontsource/space-grotesk/700.css";
import "@/styles/tokens.css";
import "@/styles/globals.css";
import { CSRF_COOKIE } from "@/auth";
import { Providers } from "./providers";


export const metadata: Metadata = {
  title: { default: "LoopGuard", template: "%s · LoopGuard" },
  description: "Inspect agent runs, verification proof, and safe interventions.",
};

export const viewport: Viewport = {
  colorScheme: "light dark",
  viewportFit: "cover",
  width: "device-width",
  initialScale: 1,
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const csrfToken = (await cookies()).get(CSRF_COOKIE)?.value ?? "";
  return (
    <html lang="en">
      <head>
        <meta content={csrfToken} name="csrf-token" />
      </head>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
