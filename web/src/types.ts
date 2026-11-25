export interface Card {
  code: string;
  name: string;
  aisle: string;
  price: string;
  price_source: "demo" | "open_prices";
  unit_price: string;
  in_stock: boolean;
  allergens: string[];
  may_contain: string[];
  labels: string[];
  ingredients_known: boolean;
  image: string;
}

export interface BasketLine extends Card {
  qty: number;
  line_total: string;
}

export interface Basket {
  lines: BasketLine[];
  items: number;
  subtotal: string;
  delivery_fee: string;
  total: string;
  free_delivery_from: string;
}

export interface Order {
  order_id: string;
  status: "placed" | "delivered" | "cancelled";
  delivery: string;
  delivered_at: string | null;
  minutes_late: number;
  changes_close: string | null;
  can_change: boolean;
  lines: { code: string; name: string; qty: number; price: string }[];
  subtotal: string;
  delivery_fee: string;
  total: string;
}

export interface HelpCase {
  id: string;
  order_id: string | null;
  kind: string;
  summary: string;
  outcome: string;
  amount: string;
  policy: string[];
}

export interface AppState {
  customer: { id: string; name: string; suburb: string; allergies: string[]; notes: string };
  basket: Basket;
  orders: Order[];
  cases: HelpCase[];
  now: string;
}

export interface Span {
  name: string;
  kind: "llm" | "tool" | "guardrail";
  start_ms: number;
  duration_ms: number;
  attributes: Record<string, unknown>;
  error: string | null;
}

export interface ToolEvent {
  name: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  duration_ms: number;
  error: string | null;
}

export interface ChatResponse {
  reply: string;
  tool_events: ToolEvent[];
  usage: { input_tokens: number; output_tokens: number };
  latency_ms: number;
  flags: string[];
  retried: boolean;
  trace: { trace_id: string; spans: Span[] };
  model: string;
  state: AppState;
  products: Record<string, Card>;
  pending: { token: string; summary: string }[];
  error?: string;
}

export interface Turn {
  id: number;
  user: string;
  response?: ChatResponse;
  error?: string;
}

export interface Meta {
  fetched_at?: string;
  source?: string;
  licence?: string;
  products?: number;
  real_prices?: number;
}
