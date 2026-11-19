import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { Providers } from "@/components/providers/Providers";
import { AppShell } from "@/components/shell/AppShell";
import { DESIGN_BOOT_SCRIPT } from "@/lib/design";
import "./globals.css";
import "./pouchwale.css";

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

// PouchWale design (switch in the top bar): Mada display + Mulish text. Not preloaded: the original design
// never downloads them; they load when the PouchWale design is on.
const mada = localFont({
  src: "./fonts/mada-latin-var.woff2",
  variable: "--font-mada",
  display: "swap",
  weight: "200 900",
  preload: false,
});

const mulish = localFont({
  src: "./fonts/mulish-latin-var.woff2",
  variable: "--font-mulish",
  display: "swap",
  weight: "200 1000",
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
    // suppressHydrationWarning: the boot script may add data-design="pouchwale" to <html> before React loads.
    <html
      lang="en"
      className={`${inter.variable} ${hanken.variable} ${devanagari.variable} ${mada.variable} ${mulish.variable}`}
      suppressHydrationWarning
    >
      <head>
        {/* Applies the saved dashboard design (Original / PouchWale) before the first paint: no flash on reload. */}
        <script dangerouslySetInnerHTML={{ __html: DESIGN_BOOT_SCRIPT }} />
      </head>
      <body>
        <Providers>
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
