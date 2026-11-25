export type ProviderKind = "offline" | "anthropic" | "azure";

export interface ModelSettings {
  provider: ProviderKind;
  api_key: string;
  model: string;
  endpoint: string;
  deployment: string;
  api_version: string;
}

const KEY = "trolley.settings";

export const DEFAULT_SETTINGS: ModelSettings = {
  provider: "offline",
  api_key: "",
  model: "claude-sonnet-5-5",
  endpoint: "",
  deployment: "",
  api_version: "2024-10-21",
};

// Settings, including any API key, stay in this browser on this device.
export function loadSettings(): ModelSettings {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...DEFAULT_SETTINGS, ...JSON.parse(raw) } : { ...DEFAULT_SETTINGS };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveSettings(s: ModelSettings): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* storage unavailable: settings last for this visit */
  }
}

export function providerLabel(s: ModelSettings): string {
  if (s.provider === "anthropic" && s.api_key) return s.model;
  if (s.provider === "azure" && s.api_key) return `Azure · ${s.deployment}`;
  return "Demo mode";
}
