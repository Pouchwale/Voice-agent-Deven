import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { Providers } from "@/components/providers/Providers";
import { AppShell } from "@/components/shell/AppShell";
import "./globals.css";

// Fonts ship with the project (variable woff2 from Google Fonts in ./fonts): next/font/google downloads them
// on every build, and a build started before the internet is up (e.g. start-all.bat at login) failed.
const inter = localFont({
  src: "./fonts/inter-latin-var.woff2",
  variable: "--font-inter",
  display: "swap",
  weight: "300 900",
});

const hanken = localFont({
  src: "./fonts/hanken-grotesk-latin-var.woff2",
  variable: "--font-hanken",
  display: "swap",
  weight: "400 900",
});

// Transcripts contain Hindi (Devanagari); Inter/Hanken have no Devanagari glyphs, so this is the fallback.
const devanagari = localFont({
  src: "./fonts/noto-sans-devanagari-var.woff2",
  variable: "--font-devanagari",
  display: "swap",
  weight: "100 900",
  preload: false,
});

export const metadata: Metadata = {
  title: { default: "Voice Agent", template: "%s · Voice Agent" },
  description: "Outbound AI voice agent console: leads, campaigns, live calls and call summaries.",
};

export const viewport: Viewport = {
  themeColor: "#FFFFFF",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} ${hanken.variable} ${devanagari.variable}`}>
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
