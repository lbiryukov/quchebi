// Primary / secondary names of pack records (features, families, districts, places, eponyms)
// for packs of both languages (docs/pack-format.md, manifest.city.lang):
//   lang 'ka' (Tbilisi): `ka` is Georgian, `ru` Russian, `en` English;
//   lang 'ru' (Moscow):  `ka` holds the primary Russian name, `ru` is omitted (or equal), `en` English.
// The UI never shows an empty secondary line or the same name twice.
import { packLang } from '../store/cities.js';

export { packLang };

const norm = (s) => String(s || '').trim().toLowerCase().replace(/ё/g, 'е').replace(/\s+/g, ' ');

/** Main (local-language) name: ka, else ru, else en. */
export function primaryName(rec) {
  if (!rec) return '';
  return rec.ka || rec.ru || rec.en || '';
}

/** Russian-first label: ru, else ka (Russian in 'ru' packs), else en. */
export function ruLabel(rec) {
  if (!rec) return '';
  return rec.ru || rec.ka || rec.en || '';
}

/**
 * Second line under the primary name: the Russian name for 'ka' packs (else English), the English
 * name for 'ru' packs; '' when missing or equal to the primary name.
 */
export function secondaryName(rec, lang = 'ka') {
  if (!rec) return '';
  const p = norm(primaryName(rec));
  // the same order serves both: in 'ru' packs `ru` is absent or equal to the primary name
  for (const c of [rec.ru, rec.en]) if (c && norm(c) !== p) return c;
  return '';
}

/** {primary, secondary} of a record. */
export function nameLines(rec, lang = 'ka') {
  return { primary: primaryName(rec), secondary: secondaryName(rec, lang) };
}

/**
 * Name variants for the object card: [{label, value, main}] without duplicates. Labels say the
 * language: 'ka', 'ru', 'en' for Tbilisi; 'название', 'en' (and 'ru' only when it differs) for
 * Russian packs.
 */
export function nameVariants(rec, lang = 'ka') {
  if (!rec) return [];
  const out = [];
  const seen = new Set();
  const add = (label, value, main = false) => {
    if (!value) return;
    const k = norm(value);
    if (seen.has(k)) return;
    seen.add(k);
    out.push({ label, value, main });
  };
  if (lang === 'ru') {
    add('название', rec.ka || rec.ru, true);
    add('ru', rec.ru);
    add('en', rec.en);
  } else {
    add('ka', rec.ka, true);
    add('ru', rec.ru, !rec.ka);
    add('en', rec.en, !rec.ka && !rec.ru);
  }
  return out;
}

/**
 * Russian name of an area or place for DSL values and labels: ru, else (Tbilisi) the English
 * spelling before the Georgian one, (Moscow) the primary Russian `ka` before English.
 */
export function areaRu(rec, lang = 'ka') {
  if (!rec) return '';
  return rec.ru || (lang === 'ru' ? rec.ka || rec.en : rec.en || rec.ka) || '';
}
