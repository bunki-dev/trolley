import type { AppState, Span, Turn } from "../types";

const money = (s: string) => Number(s.replace(/[^0-9.]/g, ""));

export function BasketPanel({
  state,
  onQty,
  onCheckout,
}: {
  state: AppState;
  onQty: (code: string, qty: number) => void;
  onCheckout: () => void;
}) {
  const b = state.basket;
  if (!b.lines.length) {
    return (
      <div className="empty">
        <p>Your basket is empty.</p>
        <p className="muted">Ask for something in the chat, or tap Add to basket on a product.</p>
      </div>
    );
  }
  const toFree = money(b.free_delivery_from) - money(b.subtotal);
  return (
    <div className="basket">
      <ul className="lines">
        {b.lines.map((l) => (
          <li key={l.code}>
            {l.image ? <img src={l.image} alt="" /> : <span className="thumb" aria-hidden />}
            <div>
              <p className="name">{l.name}</p>
              <p className="muted">
                {l.price} each{l.allergens.length ? ` · contains ${l.allergens.join(", ")}` : ""}
              </p>
            </div>
            <div className="stepper" role="group" aria-label={`Quantity of ${l.name}`}>
              <button onClick={() => onQty(l.code, l.qty - 1)} aria-label="One fewer">
                −
              </button>
              <span>{l.qty}</span>
              <button onClick={() => onQty(l.code, l.qty + 1)} aria-label="One more">
                +
              </button>
            </div>
            <p className="line-total">{l.line_total}</p>
          </li>
        ))}
      </ul>
      <dl className="totals">
        <dt>Subtotal</dt>
        <dd>{b.subtotal}</dd>
        <dt>Delivery</dt>
        <dd>{b.delivery_fee}</dd>
        <dt className="grand">Total</dt>
        <dd className="grand">{b.total}</dd>
      </dl>
      {toFree > 0 && <p className="muted">Spend ${toFree.toFixed(2)} more for free delivery.</p>}
      <button className="btn wide" onClick={onCheckout}>
        Check out with Trolley
      </button>
      <p className="muted small">Trolley will offer delivery times and ask you to confirm before placing anything.</p>
    </div>
  );
}

export function OrdersPanel({ state, onAsk }: { state: AppState; onAsk: (text: string) => void }) {
  return (
    <ul className="orders">
      {state.orders.map((o) => (
        <li key={o.order_id} className={`order is-${o.status}`}>
          <div className="order-head">
            <h3>{o.order_id}</h3>
            <span className="status">{o.status === "placed" ? "Upcoming" : o.status}</span>
          </div>
          <p>{o.delivery}</p>
          {o.delivered_at && (
            <p className="muted">
              Delivered {o.delivered_at}
              {o.minutes_late > 0 ? `, ${o.minutes_late} min late` : ""}
            </p>
          )}
          {o.changes_close && <p className="muted">Changes close {o.changes_close}</p>}
          <details>
            <summary>
              {o.lines.reduce((n, l) => n + l.qty, 0)} items · {o.total}
            </summary>
            <ul className="order-lines">
              {o.lines.map((l) => (
                <li key={l.code}>
                  {l.qty} × {l.name} <span className="muted">{l.price}</span>
                </li>
              ))}
            </ul>
          </details>
          <div className="row">
            {o.status === "placed" ? (
              <>
                <button className="btn small ghost" onClick={() => onAsk(`Can I change the delivery time for ${o.order_id}?`)}>
                  Change time
                </button>
                <button className="btn small ghost" onClick={() => onAsk(`Cancel order ${o.order_id}`)}>
                  Cancel
                </button>
              </>
            ) : o.status === "delivered" ? (
              <button className="btn small ghost" onClick={() => onAsk(`There's a problem with order ${o.order_id}`)}>
                Report a problem
              </button>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

const OUTCOME: Record<string, string> = {
  refunded: "Refunded",
  credited: "Credit added",
  with_our_team: "With our team",
  declined: "Not eligible",
};

export function CasesPanel({ state }: { state: AppState }) {
  if (!state.cases.length) {
    return (
      <div className="empty">
        <p>No help cases yet.</p>
        <p className="muted">Refunds, credits and hand-offs to a person show up here, with the policy that decided them.</p>
      </div>
    );
  }
  return (
    <ul className="cases">
      {[...state.cases].reverse().map((c) => (
        <li key={c.id}>
          <div className="order-head">
            <h3>{c.id}</h3>
            <span className={`status is-${c.outcome}`}>{OUTCOME[c.outcome] ?? c.outcome}</span>
          </div>
          <p>{c.summary}</p>
          <p className="muted">
            {c.order_id ?? "No order"} · {c.kind.replace("_", " ")}
            {c.amount !== "$0.00" ? ` · ${c.amount}` : ""} · policy {c.policy.join(", ")}
          </p>
        </li>
      ))}
    </ul>
  );
}

// ---------- receipts: one per turn, everything the agent did and every check it passed ----------

function args(a: Record<string, unknown>): string {
  return Object.entries(a)
    .map(([k, v]) => `${k}=${typeof v === "string" ? `"${v.length > 28 ? v.slice(0, 27) + "…" : v}"` : JSON.stringify(v)}`)
    .join(", ");
}

function SpanLine({ s }: { s: Span }) {
  const at = s.attributes;
  if (s.kind === "guardrail" && s.name === "input_guardrail") {
    const flags = (at.flags as string[]) ?? [];
    return (
      <li className={flags.length ? "flag" : ""}>
        <span>Input check</span>
        <span>{flags.length ? flags.join(" ") : "clear"}</span>
      </li>
    );
  }
  if (s.kind === "guardrail") {
    const ok = at.ok as boolean;
    const problems = (at.problems as string[]) ?? [];
    return (
      <>
        <li className={ok ? "ok" : "flag"}>
          <span>Grounding check</span>
          <span>{ok ? "passed" : "rejected"}</span>
        </li>
        {problems.map((p, i) => (
          <li key={i} className="sub flag">
            {p}
          </li>
        ))}
      </>
    );
  }
  if (s.kind === "llm") {
    return (
      <li className="dim">
        <span>Model step {Number(at.step) + 1}</span>
        <span>
          {String(at.input_tokens ?? 0)} in / {String(at.output_tokens ?? 0)} out
        </span>
      </li>
    );
  }
  return (
    <li className={s.error ? "flag" : ""}>
      <span className="tool">
        {s.name}({args((at.arguments as Record<string, unknown>) ?? {})})
      </span>
      <span>{s.error ? "refused" : `${Math.max(1, Math.round(s.duration_ms))} ms`}</span>
      {s.error && <span className="sub">{s.error}</span>}
    </li>
  );
}

export function ReceiptsPanel({ turns, focus }: { turns: Turn[]; focus: number | null }) {
  const done = turns.filter((t) => t.response);
  if (!done.length) {
    return (
      <div className="empty">
        <p>Nothing to show yet.</p>
        <p className="muted">
          Every reply gets a receipt here: the tools it called, the checks it passed and what it cost.
        </p>
      </div>
    );
  }
  return (
    <ol className="receipts">
      {[...done].reverse().map((t) => {
        const r = t.response!;
        return (
          <li key={t.id} id={`receipt-${t.id}`} className={`receipt${focus === t.id ? " is-focus" : ""}`}>
            <p className="r-head">
              <span>Turn {t.id}</span>
              <span>{r.model}</span>
            </p>
            <p className="r-user">“{t.user.length > 90 ? t.user.slice(0, 89) + "…" : t.user}”</p>
            <ul>
              {r.trace.spans.map((s, i) => (
                <SpanLine key={i} s={s} />
              ))}
            </ul>
            <p className="r-total">
              <span>{r.latency_ms < 1000 ? `${Math.round(r.latency_ms)} ms` : `${(r.latency_ms / 1000).toFixed(1)} s`}</span>
              <span>
                {r.usage.input_tokens + r.usage.output_tokens} tokens
                {r.retried ? " · 1 retry" : ""}
              </span>
            </p>
            {r.flags.length > 0 && <p className="r-flags">{r.flags.join(" ")}</p>}
          </li>
        );
      })}
    </ol>
  );
}
