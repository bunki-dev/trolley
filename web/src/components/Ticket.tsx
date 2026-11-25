import { useState } from "react";
import type { Card } from "../types";

interface Props {
  card: Card;
  onAdd: (code: string) => Promise<{ confirm?: { token: string; summary: string }; error?: string } | void>;
  onConfirm: (token: string) => Promise<void>;
}

// A product as a shelf ticket: name and photo up top, the price block below, label facts underneath.
export function Ticket({ card, onAdd, onConfirm }: Props) {
  const [ask, setAsk] = useState<{ token: string; summary: string } | null>(null);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);

  async function add() {
    setBusy(true);
    const res = await onAdd(card.code);
    setBusy(false);
    if (res?.confirm) setAsk(res.confirm);
    else if (res?.error) setNote(res.error);
    else setNote("Added");
  }

  const [dollars, cents] = card.price.replace("$", "").split(".");
  return (
    <article className="ticket" aria-label={card.name}>
      <div className="ticket-top">
        {card.image ? <img src={card.image} alt="" loading="lazy" /> : <span className="ticket-noimg" aria-hidden />}
        <h3>{card.name}</h3>
      </div>
      <div className="ticket-price">
        <p className="price" aria-label={card.price}>
          <span className="cur">$</span>
          {dollars}
          <span className="cents">.{cents}</span>
        </p>
        <p className="unit">
          {card.unit_price}
          <br />
          {card.price_source === "demo" ? "demo price" : "reported price"}
        </p>
      </div>
      <ul className="facts">
        {!card.in_stock && <li className="fact out">Out of stock</li>}
        {card.allergens.map((a) => (
          <li key={a} className="fact warn">
            Contains {a}
          </li>
        ))}
        {card.may_contain.map((a) => (
          <li key={a} className="fact caution">
            May contain {a}
          </li>
        ))}
        {!card.ingredients_known && <li className="fact caution">No ingredient data</li>}
        {card.labels.map((l) => (
          <li key={l} className="fact label">
            {l}
          </li>
        ))}
      </ul>
      {ask ? (
        <div className="ticket-ask" role="alert">
          <p>{ask.summary}</p>
          <div className="row">
            <button
              className="btn small danger"
              onClick={async () => {
                await onConfirm(ask.token);
                setAsk(null);
                setNote("Added");
              }}
            >
              Add anyway
            </button>
            <button className="btn small ghost" onClick={() => setAsk(null)}>
              Don't add
            </button>
          </div>
        </div>
      ) : (
        <div className="ticket-foot">
          <button className="btn small" onClick={add} disabled={busy || !card.in_stock}>
            Add to basket
          </button>
          <span className="ticket-note" aria-live="polite">
            {note}
          </span>
        </div>
      )}
    </article>
  );
}
