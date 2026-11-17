/** The active phone provider (TELEPHONY_PROVIDER in backend/.env: Plivo or Vobiz), as named in the UI. */

/** Display name ("Plivo" / "Vobiz") from any backend payload that carries it. */
export function providerName(x?: { provider_name?: string | null; telephony_name?: string | null } | null): string {
  return x?.provider_name || x?.telephony_name || "the phone provider";
}

/** The backend/.env keys the active provider needs, e.g. "PLIVO_AUTH_ID, PLIVO_AUTH_TOKEN and PLIVO_FROM_NUMBER". */
export function providerEnvKeys(provider?: string | null): string {
  const p = (provider || "plivo").toUpperCase();
  return `${p}_AUTH_ID, ${p}_AUTH_TOKEN and ${p}_FROM_NUMBER`;
}
