# Voice Agent console (frontend)

Next.js 15 (App Router) + React 19 + Tailwind CSS v4 console for the outbound AI voice agent:
dashboard, leads, campaigns, call log + call summaries, live monitor (listen-in) and a browser "Test agent" call.

The backend contract lives in `../docs/API_CONTRACT.md`; types are mirrored in `src/lib/types.ts`.

## Environment

Copy `.env.local.example` to `.env.local`:

```
NEXT_PUBLIC_API_URL=http://localhost:8100
```

WebSocket URLs are derived from it (`http` → `ws`, `https` → `wss`). The app still renders when the backend is down
(an offline banner shows and every view offers a retry).

## Develop

```
npm install
npm run dev        # http://localhost:3100
```

The browser test call needs microphone access, which browsers only allow on `localhost` or `https`. Use headphones.

## Build

```
npm run build      # type-check + lint + production build
npm start
```

## Layout

- `src/app/*` - routes (`/`, `/leads`, `/leads/[id]`, `/campaigns`, `/calls`, `/calls/[uuid]`, `/live`, `/settings`)
- `src/lib/` - `api.ts` (typed fetch + SWR keys), `ws.ts` (reconnecting socket), `live-store.ts` (`/ws/live` feed),
  `audio.ts` (PCM helpers), `format.ts` (IST dates, m:ss, phone numbers), `types.ts`
- `src/components/ui/` - brand primitives (Button, Card, Chip, Modal, Toast, fields, skeletons…)
- `src/components/orb/` - small three.js voice-activity orb used in the Test agent panel (lazy, client only)
- `public/pcm-capture-worklet.js` - AudioWorklet that turns the mic into 16 kHz PCM16 frames

Style: plain blue + white UI (tokens in `src/app/globals.css`, hex mirrors in `src/lib/theme.ts`), Inter for UI text and
Hanken Grotesk for headings, Noto Sans Devanagari as the Hindi fallback. The only brand element is the logo
(`public/pouchwale_logo.webp`, a white wordmark) shown on its logo-blue tile in the sidebar.
