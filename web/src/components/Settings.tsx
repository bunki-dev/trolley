import { useState, type FormEvent } from "react";
import { saveSettings, type ModelSettings, type ProviderKind } from "../runtime/settings";

interface Props {
  initial: ModelSettings;
  onClose: (saved: ModelSettings | null) => void;
}

const OPTIONS: { id: ProviderKind; label: string; hint: string }[] = [
  { id: "offline", label: "Demo mode", hint: "No AI model: a keyword router drives the same tools and guardrails. Free, and handles simple requests." },
  { id: "anthropic", label: "Claude", hint: "Uses your Anthropic API key." },
  { id: "azure", label: "Azure AI Foundry", hint: "Uses your Azure OpenAI deployment and key." },
];

export function Settings({ initial, onClose }: Props) {
  const [s, setS] = useState<ModelSettings>(initial);
  const set = (patch: Partial<ModelSettings>) => setS((prev) => ({ ...prev, ...patch }));

  function submit(e: FormEvent) {
    e.preventDefault();
    saveSettings(s);
    onClose(s);
  }

  return (
    <div className="sheet-backdrop" role="presentation" onClick={() => onClose(null)}>
      <form
        className="sheet"
        role="dialog"
        aria-modal="true"
        aria-labelledby="settings-title"
        onSubmit={submit}
        onClick={(e) => e.stopPropagation()}
      >
        <h2 id="settings-title">Model</h2>
        <fieldset className="choices">
          <legend className="visually-hidden">Model</legend>
          {OPTIONS.map((o) => (
            <label key={o.id} className={s.provider === o.id ? "choice is-on" : "choice"}>
              <input
                type="radio"
                name="provider"
                value={o.id}
                checked={s.provider === o.id}
                onChange={() => set({ provider: o.id })}
              />
              <span>
                <strong>{o.label}</strong>
                <small>{o.hint}</small>
              </span>
            </label>
          ))}
        </fieldset>

        {s.provider !== "offline" && (
          <label className="field">
            API key
            <input
              type="password"
              autoComplete="off"
              value={s.api_key}
              onChange={(e) => set({ api_key: e.target.value })}
              placeholder={s.provider === "anthropic" ? "sk-ant-…" : "Azure key"}
              required
            />
          </label>
        )}
        {s.provider === "anthropic" && (
          <label className="field">
            Model
            <input value={s.model} onChange={(e) => set({ model: e.target.value })} />
          </label>
        )}
        {s.provider === "azure" && (
          <>
            <label className="field">
              Endpoint
              <input
                type="url"
                value={s.endpoint}
                onChange={(e) => set({ endpoint: e.target.value })}
                placeholder="https://your-resource.openai.azure.com/"
                required
              />
            </label>
            <label className="field">
              Deployment name
              <input value={s.deployment} onChange={(e) => set({ deployment: e.target.value })} required />
            </label>
          </>
        )}
        {s.provider !== "offline" && (
          <p className="fine">
            Your key is saved only in this browser on this device and is sent only to the provider you picked. Use a
            key with a spending limit.
          </p>
        )}
        <div className="sheet-actions">
          <button type="button" className="btn ghost" onClick={() => onClose(null)}>
            Cancel
          </button>
          <button type="submit" className="btn">
            Save
          </button>
        </div>
      </form>
    </div>
  );
}
