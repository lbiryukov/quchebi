// `дом:` values in the DSL. Parsing and row matching live in search/houses.js (shared with the
// result card); this module adds the DSL-facing pieces: parse with a helpful error, and the
// Russian description used by explain().
import { parseHouseQuery } from '../search/houses.js';

export const HOUSE_HELP = 'примеры: 3, =3, 3а, 3/16, 1-9, 3?, есть, нет';

/** Parse one `дом:` value -> {q} | {error}. q is the search/houses.js query object. */
export function parseHouseValue(raw) {
  const q = parseHouseQuery(raw);
  if (q.error) return { error: `${q.error} — ${HOUSE_HELP}` };
  return { q };
}

const LIT_RU = { 'ა': 'а', 'ბ': 'б', 'გ': 'г', 'დ': 'д', 'ე': 'е', 'ვ': 'в', 'ზ': 'з', 'ი': 'и', 'კ': 'к', 'ლ': 'л', 'მ': 'м', 'ნ': 'н', 'ო': 'о', 'პ': 'п', 'რ': 'р', 'ს': 'с', 'ტ': 'т', 'უ': 'у' };
const litRu = (l) => (l ? LIT_RU[l] || l : '');

/** Short Russian description of a house query, for explain(). */
export function describeHouse(q) {
  const num = `${q.n}${litRu(q.lit)}${q.sub ? '/' + q.sub : ''}`;
  const prob = q.probable ? ' (или где он вероятен)' : '';
  switch (q.kind) {
    case 'has': return 'с номерами домов';
    case 'none': return 'без номеров домов';
    case 'any': return `с домом ${q.n}${prob}`;
    case 'exact': return `с домом ровно ${q.n}${prob}`;
    case 'lit':
    case 'sub': return `с домом ${q.exact ? 'ровно ' : ''}${num}${prob}`;
    case 'corp': return `с домом ${q.n} корп. ${q.corp}${prob}`;
    case 'range': return `с домами ${q.from}–${q.to}`;
    default: return 'с домами';
  }
}
