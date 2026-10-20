// How a model reads wherever a person picks one or lists them: the catalog
// wire's rank, Coding Index and price (ranking.annotate on the server).

// USD per million tokens without trailing zeros: 15, 0.6, 1.25.
const usd = (v) => (v == null ? "?" : `$${+v.toFixed(3)}`);

// A model's price per million tokens in and out, "free" when it costs nothing
// through its harness, or "" when nothing names a price.
export function modelPrice(m) {
  if (m.free || (m.price_in === 0 && m.price_out === 0)) return "free";
  if (m.price_in == null && m.price_out == null) return "";
  return `${usd(m.price_in)} / ${usd(m.price_out)}`;
}

// The chip on a model pick: rank within its harness, Coding Index, price.
// An unranked model shows "unranked" and its price.
export function modelMeta(m) {
  const head = m.rank ? [`#${m.rank}`, m.coding.toFixed(1)] : ["unranked"];
  const price = modelPrice(m);
  return [...head, ...(price ? [price] : [])].join(" · ");
}
