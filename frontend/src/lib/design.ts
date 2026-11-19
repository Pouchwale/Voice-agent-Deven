/**
 * Dashboard design: "original" (the blue + white look in globals.css, the default) or "pouchwale"
 * (the PouchWale brand look in app/pouchwale.css).
 *
 * The choice is a per-browser preference kept in localStorage and shown as <html data-design="pouchwale">.
 * This module has no client-only imports, so the root layout (a server component) can use the boot script;
 * the switching itself lives in lib/design-switch.ts.
 */

export type Design = "original" | "pouchwale";

export const DESIGN_STORAGE_KEY = "va-design";
export const DESIGN_ATTRIBUTE = "data-design";

/**
 * Inline <head> script: applies the saved design before the first paint, so a reload or a new tab never
 * flashes the other design. Storage can be blocked (private mode, policies): then the original design shows.
 */
export const DESIGN_BOOT_SCRIPT = `try{if(localStorage.getItem(${JSON.stringify(DESIGN_STORAGE_KEY)})==="pouchwale")document.documentElement.setAttribute(${JSON.stringify(DESIGN_ATTRIBUTE)},"pouchwale")}catch(e){}`;
