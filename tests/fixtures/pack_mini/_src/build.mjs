// Generator of tests/fixtures/pack_mini (a tiny but plausible pack, docs/pack-format.md v1).
// Run from the project root:  node tests/fixtures/pack_mini/_src/build.mjs
// Hand-written: names, kinds, families, eponyms, old names, house numbers, polygons.
// Computed here: len, c, bb, d/p (zone rule), crossings + direction masks, near pairs,
// house points, encoded geometry, manifest sha256.
import { writeFileSync, mkdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  encodePolyline, lineLength, makeProjection, directionMask, PreparedPolygon,
  clipLengthInside, polygonAreaKm2, circlePolygon, pointLineDistance, isClosedRing,
} from '../../../../app/src/zone/geo.js';

const OUT = join(dirname(fileURLToPath(import.meta.url)), '..');
const e5 = (x) => Math.round(x * 1e5);

// ------------------------------------------------------------------ kinds (pack-format.md)
const KINDS = [
  ['street', 'ქუჩა', 'улица', [], 'street*'], ['avenue', 'გამზირი', 'проспект', [], 'street*'],
  ['highway', 'გზატკეცილი', 'шоссе', [], 'street*'], ['road', 'გზა', 'дорога', [], 'street*'],
  ['embankment', 'სანაპირო', 'набережная', [], 'street*'], ['ascent', 'აღმართი', 'подъём', [], 'street*'],
  ['descent', 'დაღმართი', 'спуск', [], 'street*'], ['alley', 'ხეივანი', 'аллея', [], 'street*'],
  ['row', 'რიგი', 'ряд', [], 'street*'], ['lane', 'შესახვევი', 'переулок', [], 'lane*'],
  ['dead_end', 'ჩიხი', 'тупик', [], 'lane*'], ['passage', 'გასასვლელი', 'проезд', ['съезд'], 'lane*'],
  ['square', 'მოედანი', 'площадь', [], ''], ['bridge', 'ხიდი', 'мост', [], ''],
  ['tunnel', 'გვირაბი', 'тоннель', [], ''], ['ravine', 'ხევი', 'балка', ['ущелье'], ''],
  ['garden', 'სკვერი', 'сквер', ['сад'], ''], ['park', 'პარკი', 'парк', [], ''],
  ['river', 'მდინარე', 'река', [], 'water*'], ['stream', 'ღელე', 'ручей', [], 'water*'],
  ['canal', 'არხი', 'канал', [], 'water*'], ['lake', 'ტბა', 'озеро', [], 'water*'],
  ['metro', '', 'метро', ['станция'], ''], ['district', 'რაიონი', 'район', [], 'area*'],
  ['neighbourhood', '', 'микрорайон', ['квартал', 'местность'], 'area*'], ['other', '', 'прочее', [], ''],
].map(([code, ka, ru, syn, group]) => ({ code, ka, ru, syn, group }));
const K = Object.fromEntries(KINDS.map((k, i) => [k.code, i]));

// ------------------------------------------------------------------ areas
const S = [41.6934, 44.8014]; // Freedom Square centre
const DISTRICTS = [
  { ka: 'მთაწმინდის რაიონი', ru: 'Мтацминда', en: 'Mtatsminda',
    poly: [[[[41.684, 44.780], [41.705, 44.780], [41.705, 44.8045], [41.684, 44.8045], [41.684, 44.780]]]] },
  { ka: 'კრწანისის რაიონი', ru: 'Крцаниси', en: 'Krtsanisi',
    poly: [[[[41.680, 44.8045], [41.700, 44.8045], [41.700, 44.8077], [41.695, 44.8092], [41.690, 44.8108],
      [41.686, 44.8126], [41.680, 44.8124], [41.680, 44.8045]]]] },
  { ka: 'ისნის რაიონი', ru: 'Исани', en: 'Isani',
    poly: [[[[41.680, 44.8124], [41.686, 44.8126], [41.690, 44.8108], [41.695, 44.8092], [41.700, 44.8077],
      [41.705, 44.8077], [41.705, 44.830], [41.680, 44.830], [41.680, 44.8124]]]] },
];
const PLACES = [
  { ka: 'სოლოლაკი', ru: 'Сололаки', en: 'Sololaki', kind: 'neighbourhood', parent: 0,
    poly: [[[[41.6870, 44.7900], [41.6928, 44.7900], [41.6928, 44.8000], [41.6870, 44.8000], [41.6870, 44.7900]]]] },
  { ka: 'კალა', ru: 'Кала', en: 'Kala', kind: 'neighbourhood', parent: 1,
    poly: [[[[41.6870, 44.8030], [41.6925, 44.8030], [41.6925, 44.8100], [41.6870, 44.8100], [41.6870, 44.8030]],
      [[41.6874, 44.8040], [41.6880, 44.8040], [41.6880, 44.8050], [41.6874, 44.8050], [41.6874, 44.8040]]]] },
  { ka: 'ავლაბარი', ru: 'Авлабари', en: 'Avlabari', kind: 'neighbourhood', parent: 2, poly: [], c: [41.6930, 44.8150] },
];

// ------------------------------------------------------------------ eponyms
const TAXONOMY = [
  { id: 'lit', ru: 'Литература', aliases: ['литература', 'литератор', 'литераторы'], children: [
    { id: 'lit.poet', ru: 'поэт', aliases: ['поэт', 'поэтесса', 'поэты'] },
    { id: 'lit.writer', ru: 'писатель', aliases: ['писатель', 'писательница', 'писатели', 'прозаик'] },
    { id: 'lit.playwright', ru: 'драматург', aliases: ['драматург'] }] },
  { id: 'power', ru: 'Власть', aliases: ['власть', 'правитель'], children: [
    { id: 'power.monarch', ru: 'царь', aliases: ['царь', 'царица', 'король', 'монарх'] },
    { id: 'power.revolutionary', ru: 'революционер', aliases: ['революционер'] },
    { id: 'power.public', ru: 'общественный деятель', aliases: ['общественный деятель', 'общественник'] }] },
  { id: 'mil', ru: 'Военные', aliases: ['военные', 'военный'], children: [
    { id: 'mil.commander', ru: 'полководец', aliases: ['полководец', 'военачальник', 'генерал'] },
    { id: 'mil.hero', ru: 'герой войны', aliases: ['герой войны', 'герой'] }] },
  { id: 'rel', ru: 'Религия', aliases: ['религия'], children: [
    { id: 'rel.saint', ru: 'святой', aliases: ['святой', 'святая'] }] },
  { id: 'sci', ru: 'Наука', aliases: ['наука', 'учёный', 'ученый'], children: [
    { id: 'sci.engineer', ru: 'инженер', aliases: ['инженер'] }] },
  { id: 'art', ru: 'Искусство', aliases: ['искусство'], children: [
    { id: 'art.composer', ru: 'композитор', aliases: ['композитор'] }] },
  { id: 'biz', ru: 'Меценаты', aliases: ['меценаты'], children: [
    { id: 'biz.patron', ru: 'меценат', aliases: ['меценат'] },
    { id: 'biz.entrepreneur', ru: 'предприниматель', aliases: ['предприниматель', 'купец'] }] },
  { id: 'group', ru: 'Группы', aliases: ['группы'], children: [
    { id: 'group.family', ru: 'братья и семьи', aliases: ['братья', 'семья'] }] },
  { id: 'place', ru: 'Топонимы', aliases: ['топонимы'], children: [
    { id: 'place.tbilisi', ru: 'место в Тбилиси', aliases: ['место в тбилиси'] },
    { id: 'place.foreign_city', ru: 'зарубежный город', aliases: ['зарубежный город'] }] },
  { id: 'nature', ru: 'Природа', aliases: ['природа и ландшафт'], children: [
    { id: 'nature.landscape', ru: 'ландшафт', aliases: ['ландшафт'] }] },
  { id: 'concept', ru: 'Понятия', aliases: ['понятия'], children: [
    { id: 'concept.abstract', ru: 'абстрактное', aliases: ['абстрактное', 'абстракция'] }] },
];
const EP_KINDS = {
  person: ['персона', 'человек', 'люди'], toponym: ['топоним', 'место'], event_date: ['событие', 'дата'],
  nature: ['природа'], concept: ['понятие'], group: ['группа'],
};
const EPONYMS = [
  /* 0 */ { ka: 'ლადო ასათიანი', ru: 'Ладо Асатиани', en: 'Lado Asatiani', k: 'person', cat: ['lit.poet'], occ: ['поэт'],
    g: 'm', b: 1917, d: 1943, o: 'ge', desc: 'грузинский поэт', src: 'osm', conf: 0.97, ai: 0 },
  /* 1 */ { ka: 'შოთა რუსთაველი', ru: 'Шота Руставели', en: 'Shota Rustaveli', k: 'person', cat: ['lit.poet'],
    occ: ['поэт', 'государственный деятель'], g: 'm', b: 1160, d: 1220, o: 'ge',
    desc: 'грузинский поэт XII века, автор поэмы «Витязь в тигровой шкуре»', src: 'wd_match', conf: 0.99, ai: 0 },
  /* 2 */ { ka: 'თავისუფლება', ru: 'Свобода', en: 'Freedom', k: 'concept', cat: ['concept.abstract'],
    desc: 'понятие «свобода»', src: 'rules', conf: 0.9, ai: 1 },
  /* 3 */ { ka: 'გალაკტიონ ტაბიძე', ru: 'Галактион Табидзе', en: 'Galaktion Tabidze', q: 'Q144086', k: 'person',
    cat: ['lit.poet'], occ: ['поэт'], g: 'm', b: 1891, d: 1959, o: 'ge', desc: 'грузинский поэт', src: 'osm', conf: 0.98, ai: 0 },
  /* 4 */ { ka: 'კოტე აფხაზი', ru: 'Коте Апхази', en: 'Kote Apkhazi', k: 'person', cat: ['mil.commander', 'mil.hero'],
    occ: ['генерал', 'военачальник'], g: 'm', b: 1867, d: 1923, o: 'ge',
    desc: 'грузинский генерал, национальный герой', src: 'wd_match', conf: 0.93, ai: 0 },
  /* 5 */ { ka: 'ერეკლე II', ru: 'Ираклий II', en: 'Erekle II', k: 'person', cat: ['power.monarch', 'mil.commander'],
    occ: ['царь', 'монарх', 'полководец'], g: 'm', b: 1720, d: 1798, o: 'ge',
    desc: 'царь Картли-Кахетинского царства', src: 'wd_match', conf: 0.96, ai: 0 },
  /* 6 */ { ka: 'მეტეხი', ru: 'Метехи', en: 'Metekhi', k: 'toponym', cat: ['place.tbilisi'],
    desc: 'исторический район Тбилиси на скале над Курой', src: 'rules', conf: 0.9, ai: 1 },
  /* 7 */ { ka: 'ეკატერინე გაბაშვილი', ru: 'Екатерина Габашвили', en: 'Ekaterine Gabashvili', k: 'person',
    cat: ['lit.writer', 'power.public'], occ: ['писательница', 'общественная деятельница'], g: 'f', b: 1851, d: 1938,
    o: 'ge', desc: 'грузинская писательница и общественная деятельница', src: 'wd_match', conf: 0.92, ai: 0 },
  /* 8 */ { ka: 'ვახტანგ გორგასალი', ru: 'Вахтанг Горгасали', en: 'Vakhtang Gorgasali', k: 'person',
    cat: ['power.monarch', 'rel.saint', 'mil.hero'], occ: ['царь', 'святой'], g: 'm', b: 440, d: 502, o: 'ge',
    desc: 'царь Иберии, по преданию основатель Тбилиси', src: 'wd_match', conf: 0.97, ai: 0 },
  /* 9 */ { ka: 'სოლოლაკი', ru: 'Сололаки', en: 'Sololaki', k: 'toponym', cat: ['place.tbilisi'],
    desc: 'исторический район Тбилиси', src: 'rules', conf: 0.95, ai: 0 },
  /* 10 */ { ka: 'ზუბალაშვილები', ru: 'братья Зубалашвили', en: 'Zubalashvili brothers', k: 'persons_multi',
    cat: ['biz.patron', 'group.family'], occ: ['меценат', 'предприниматель'], g: 'm', b: 1840, d: 1920, o: 'ge',
    desc: 'тифлисские купцы-меценаты', src: 'llm', conf: 0.8, ai: 1 },
  /* 11 */ { ka: 'ალექსანდრე პუშკინი', ru: 'Александр Пушкин', en: 'Alexander Pushkin', q: 'Q7200', k: 'person',
    cat: ['lit.poet', 'lit.playwright'], occ: ['поэт', 'писатель', 'драматург'], g: 'm', b: 1799, d: 1837, o: 'fo',
    desc: 'русский поэт, бывал в Тифлисе в 1829 году', src: 'osm', conf: 0.99, ai: 0 },
  /* 12 */ { ka: 'რიყე', ru: 'Рике', en: 'Rike', k: 'nature', cat: ['nature.landscape'],
    desc: 'галечный речной берег', src: 'rules', conf: 0.85, ai: 1 },
  /* 13 */ { ka: 'ბესიკი', ru: 'Бесики', en: 'Besiki', k: 'person', cat: ['lit.poet'], occ: ['поэт', 'дипломат'],
    g: 'm', b: 1750, d: 1791, o: 'ge', desc: 'грузинский поэт и дипломат (Бесарион Габашвили)', src: 'wd_match', conf: 0.9, ai: 0 },
  /* 14 */ { ka: 'კონსტანტინე ლესელიძე', ru: 'Константин Леселидзе', en: 'Konstantine Leselidze', k: 'person',
    cat: ['mil.commander'], occ: ['генерал-полковник', 'военачальник'], g: 'm', b: 1903, d: 1944, o: 'ge',
    desc: 'советский генерал-полковник', src: 'enc2008', conf: 0.9, ai: 0 },
  /* 15 */ { ka: 'ვლადიმერ ლენინი', ru: 'Владимир Ленин', en: 'Vladimir Lenin', k: 'person',
    cat: ['power.revolutionary'], occ: ['революционер', 'политик'], g: 'm', b: 1870, d: 1924, o: 'fo',
    desc: 'русский революционер, глава Советского государства', src: 'enc2008', conf: 0.95, ai: 0 },
];
const FAMS = [
  { ka: 'ლადო ასათიანის', ru: 'Ладо Асатиани', e: 0 }, { ka: 'შოთა რუსთაველის', ru: 'Шота Руставели', e: 1 },
  { ka: 'თავისუფლების', ru: 'Свободы', e: 2 }, { ka: 'გალაკტიონ ტაბიძის', ru: 'Галактиона Табидзе', e: 3 },
  { ka: 'კოტე აფხაზის', ru: 'Коте Апхази', e: 4 }, { ka: 'ერეკლე II-ის', ru: 'Ираклия II', e: 5 },
  { ka: 'მეტეხის', ru: 'Метехский', e: 6 }, { ka: 'ეკატერინე გაბაშვილის', ru: 'Екатерины Габашвили', e: 7 },
  { ka: 'ვახტანგ გორგასლის', ru: 'Вахтанга Горгасали', e: 8 }, { ka: 'სოლოლაკის', ru: 'Сололакский', e: 9 },
  { ka: 'ზუბალაშვილების', ru: 'Зубалашвили', e: 10 }, { ka: 'პუშკინის', ru: 'Пушкина', e: 11 },
  { ka: 'რიყის', ru: 'Рике', e: 12 }, { ka: 'ბესიკის', ru: 'Бесики', e: 13 },
  { ka: 'ჩიტაძის', ru: 'Читадзе' },
];

// ------------------------------------------------------------------ features
// geom: lines [[lat, lon], ...] (one part) | {ring} closed polygon | {point}
// houses: [raw, n, lit, sub, n_to?, corp?]
const ring = (pts) => ({ ring: [...pts, pts[0]] });
const F = [
  /* 0 */ { ka: 'ლადო ასათიანის ქუჩა', ru: 'улица Ладо Асатиани', en: 'Lado Asatiani Street', k: 'street', fam: 0,
    geom: [[41.6902, 44.7930], [41.6905, 44.7980], [41.6908, 44.8010]],
    houses: [['1', 1], ['2', 2], ['3', 3], ['3ა', 3, 'ა'], ['3/16', 3, '', '16'], ['4', 4], ['5', 5], ['6', 6], ['7', 7], ['9', 9], ['12', 12]] },
  /* 1 */ { ka: 'ლადო ასათიანის I შესახვევი', ru: '1-й переулок Ладо Асатиани', en: 'Lado Asatiani 1st Lane', k: 'lane', o: 1, fam: 0, par: 0,
    alt: ['Lado Asatiani I Lane'], geom: [[41.69026, 44.7940], [41.6915, 44.7940]], houses: [['1', 1], ['2', 2]] },
  /* 2 */ { ka: 'ლადო ასათიანის II შესახვევი', ru: '2-й переулок Ладо Асатиани', en: 'Lado Asatiani 2nd Lane', k: 'lane', o: 2, fam: 0, par: 0,
    alt: ['Lado Asatiani II Lane'], geom: [[41.69041, 44.7965], [41.68893, 44.7965]], houses: [['2', 2], ['4', 4]] },
  /* 3 */ { ka: 'ლადო ასათიანის ჩიხი', ru: 'тупик Ладо Асатиани', en: 'Lado Asatiani Dead End', k: 'dead_end', fam: 0, par: 0,
    geom: [[41.69065, 44.7995], [41.6900, 44.7995]] },
  /* 4 */ { ka: 'ლადო ასათიანის გასასვლელი', ru: 'проезд Ладо Асатиани', en: 'Lado Asatiani Passage', k: 'passage', fam: 0, par: 0,
    geom: [[41.69075, 44.8005], [41.6913, 44.8008]] },
  /* 5 */ { ka: 'შოთა რუსთაველის გამზირი', ru: 'проспект Шота Руставели', en: 'Shota Rustaveli Avenue', k: 'avenue', fam: 1,
    alt: ['Rustaveli Ave'], geom: [[41.70095, 44.79381], [41.69947, 44.79689], [41.69788, 44.79912], S],
    houses: [['6', 6], ['8', 8], ['10', 10], ['12', 12], ['13', 13], ['14', 14], ['37', 37]] },
  /* 6 */ { ka: 'თავისუფლების მოედანი', ru: 'площадь Свободы', en: 'Freedom Square', k: 'square', fam: 2,
    old: [{ ka: 'ლენინის მოედანი', ru: 'площадь Ленина', lat: 'Lenin Square', from: 1921, to: 1990, e: 15, src: 'enc2008' },
      { ka: 'ერევნის მოედანი', ru: 'Эриванская площадь', lat: 'Erivan Square', from: 1827, to: 1918, src: 'enc2008' }],
    geom: ring([[41.6928, 44.8005], [41.6940, 44.8005], [41.6940, 44.8023], [41.6928, 44.8023]]) },
  /* 7 */ { ka: 'გალაკტიონ ტაბიძის ქუჩა', ru: 'улица Галактиона Табидзе', en: 'Galaktion Tabidze Street', k: 'street', fam: 3,
    q: 'Q144086', alt: ['Galaktion Tabidze St'], geom: [S, [41.6910, 44.7985], [41.6895, 44.7970]],
    houses: [['1', 1], ['2', 2], ['4', 4], ['5', 5], ['7', 7]] },
  /* 8 */ { ka: 'კოტე აფხაზის ქუჩა', ru: 'улица Коте Апхази', en: 'Kote Apkhazi Street', k: 'street', fam: 4,
    old: [{ ka: 'ლესელიძის ქუჩა', ru: 'улица Леселидзе', lat: 'Leselidze St', from: 1944, to: 2006, e: 14, src: 'osm' }],
    geom: [S, [41.6905, 44.8060], [41.6885, 44.8102]],
    houses: [['3', 3], ['16 კორპ. 3', 16, '', '', null, '3'], ['20/22', 20, '', '22'], ['27;29', 27], ['27;29', 29], ['31', 31]] },
  /* 9 */ { ka: 'ერეკლე II-ის ქუჩა', ru: 'улица Ираклия II', en: 'Erekle II Street', k: 'street', fam: 5,
    geom: [[41.6920, 44.8070], [41.6895, 44.8045]], houses: [['3ა/1', 3, 'ა', '1'], ['32 ბ', 32, 'ბ'], ['16', 16]] },
  /* 10 */ { ka: 'მეტეხის ხიდი', ru: 'Метехский мост', en: 'Metekhi Bridge', k: 'bridge', fam: 6,
    geom: [[41.6895, 44.8097], [41.6898, 44.8135]] },
  /* 11 */ { ka: 'მტკვარი', ru: 'река Кура', en: 'Mtkvari River', k: 'river', alt: ['მდინარე მტკვარი', 'Kura', 'Mtkvari'],
    geom: [[41.7000, 44.8083], [41.6950, 44.8098], [41.6900, 44.8115], [41.6860, 44.8133], [41.6820, 44.8128]] },
  /* 12 */ { ka: 'თავისუფლების მოედანი', ru: 'станция метро «Площадь Свободы»', en: 'Freedom Square (metro)', k: 'metro', fam: 2,
    alt: ['მეტრო თავისუფლების მოედანი'], geom: { point: [41.6936, 44.8009] } },
  /* 13 */ { ka: 'მთაწმინდის რაიონი', ru: 'район Мтацминда', en: 'Mtatsminda District', k: 'district', ar: 0, area: 'd0' },
  /* 14 */ { ka: 'სოლოლაკი', ru: 'Сололаки', en: 'Sololaki', k: 'neighbourhood', ar: 0, area: 'p0' },
  /* 15 */ { ka: 'კალა', ru: 'Кала', en: 'Kala', k: 'neighbourhood', ar: 1, area: 'p1', alt: ['ძველი თბილისი', 'Старый город'] },
  /* 16 */ { ka: 'ეკატერინე გაბაშვილის ქუჩა', ru: 'улица Екатерины Габашвили', en: 'Ekaterine Gabashvili Street', k: 'street', fam: 7,
    geom: [[41.6885, 44.7935], [41.6890, 44.7990]], houses: [['1', 1], ['7', 7], ['9', 9], ['10', 10]] },
  /* 17 */ { ka: 'ვახტანგ გორგასლის ქუჩა', ru: 'улица Вахтанга Горгасали', en: 'Vakhtang Gorgasali Street', k: 'street', fam: 8,
    geom: [[41.6905, 44.8095], [41.6880, 44.8100], [41.6860, 44.8112]], houses: [['1', 1], ['3', 3], ['5', 5], ['16', 16]] },
  /* 18 */ { ka: 'სოლოლაკის აღმართი', ru: 'Сололакский подъём', en: 'Sololaki Ascent', k: 'ascent', fam: 9,
    geom: [[41.6902, 44.7930], [41.6890, 44.7910]], houses: [['2', 2]] },
  /* 19 */ { ka: 'ზუბალაშვილების ქუჩა', ru: 'улица Зубалашвили', en: 'Zubalashvili Street', k: 'street', fam: 10,
    geom: [[41.6930, 44.7950], [41.6880, 44.7952]], houses: [['3-5', 3, '', '', 5], ['8', 8], ['10', 10], ['12', 12]] },
  /* 20 */ { ka: 'პუშკინის სკვერი', ru: 'сквер Пушкина', en: 'Pushkin Square', k: 'garden', fam: 11,
    geom: ring([[41.6941, 44.8025], [41.6946, 44.8025], [41.6946, 44.8034], [41.6941, 44.8034]]) },
  /* 21 */ { ka: 'რიყის პარკი', ru: 'парк Рике', en: 'Rike Park', k: 'park', fam: 12,
    geom: ring([[41.6928, 44.8111], [41.6936, 44.8132], [41.6918, 44.8146], [41.6910, 44.8126]]) },
  /* 22 */ { ka: 'ბესიკის ქუჩა', ru: 'улица Бесики', en: 'Besiki Street', k: 'street', fam: 13,
    geom: [[41.69904, 44.7975], [41.6962, 44.7952]], houses: [['3', 3], ['16', 16], ['18', 18]] },
  /* 23 */ { ka: 'ჩიტაძის ქუჩა', ru: 'улица Читадзе', en: 'Chitadze Street', k: 'street', fam: 14,
    geom: [[41.6990, 44.7945], [41.7012, 44.7975]], houses: [['7', 7], ['16', 16]] },
  /* 24 */ { ka: 'ალექსანდრე პუშკინის ქუჩა', ru: 'улица Пушкина', en: 'Pushkin Street', k: 'street', fam: 11, q: 'Q7200',
    geom: [S, [41.6955, 44.8050]], houses: [['3', 3], ['5', 5], ['16', 16]] },
  /* 25 */ { ka: 'ავლაბარი', ru: 'Авлабари', en: 'Avlabari', k: 'neighbourhood', ar: 2, area: 'p2' },
];

// ------------------------------------------------------------------ helpers
const proj = makeProjection(41.69, 44.80);
const ringsOfArea = (a) => a.poly.map((poly) => poly); // poly: [[outer, hole...], [outer2...]]
function areaPolygonRings(a) { // flat list of rings (even-odd)
  if (!a.poly.length) return [circlePolygon(a.c, 300)];
  return a.poly.flat(1);
}

function featureLines(f) { // Array<Array<[lat, lon]>>
  if (Array.isArray(f.geom)) return [f.geom];
  if (f.geom.ring) return [f.geom.ring];
  if (f.geom.point) return [[f.geom.point]];
  return [];
}
const shapeOf = (f) => (f.geom.point ? 'point' : f.geom.ring ? 'polygon' : 'line');

// resolve area-derived geometry for district/neighbourhood features
for (const f of F) {
  if (!f.area) continue;
  const a = f.area[0] === 'd' ? DISTRICTS[+f.area.slice(1)] : PLACES[+f.area.slice(1)];
  if (a.poly.length) f.geom = { ring: a.poly[0][0] };
  else f.geom = { point: a.c };
}

function polygonsIntersect(ringA, ringsB) {
  const pb = new PreparedPolygon(ringsB);
  if (clipLengthInside(ringA, pb).inside > 0) return true;
  if (ringA.some((p) => pb.contains(p[0], p[1]))) return true;
  const pa = new PreparedPolygon([ringA]);
  return ringsB.some((r) => r.some((p) => pa.contains(p[0], p[1])));
}

function inArea(f, rings) {
  const pp = new PreparedPolygon(rings);
  const sh = shapeOf(f);
  if (sh === 'point') return pp.contains(f.geom.point[0], f.geom.point[1]);
  if (sh === 'polygon') { // areas: by centroid (shared borders would make 'intersects' ambiguous)
    const r = f.geom.ring, m = r.length - 1;
    return pp.contains(r.slice(0, m).reduce((s, q) => s + q[0], 0) / m, r.slice(0, m).reduce((s, q) => s + q[1], 0) / m);
  }
  const r = clipLengthInside(f.geom, pp);
  return r.inside >= 30 || (r.total > 0 && r.inside / r.total >= 0.5);
}

function pointAlong(line, frac) {
  const L = lineLength(line);
  let target = L * frac, acc = 0;
  for (let i = 0; i + 1 < line.length; i++) {
    const a = proj(line[i]), b = proj(line[i + 1]);
    const d = Math.hypot(b[0] - a[0], b[1] - a[1]);
    if (acc + d >= target || i + 2 === line.length) {
      const t = d > 0 ? Math.min(1, (target - acc) / d) : 0;
      return { x: a[0] + (b[0] - a[0]) * t, y: a[1] + (b[1] - a[1]) * t, dx: (b[0] - a[0]) / (d || 1), dy: (b[1] - a[1]) / (d || 1) };
    }
    acc += d;
  }
  const a = proj(line[0]);
  return { x: a[0], y: a[1], dx: 1, dy: 0 };
}

// segment intersection in projected metres; tolerance for T-junctions
function segIntersections(A, B, tol = 1.0) {
  const out = [];
  const pa = A.map(proj), pb = B.map(proj);
  const near = (p, s0, s1) => {
    const dx = s1[0] - s0[0], dy = s1[1] - s0[1], L2 = dx * dx + dy * dy;
    let t = L2 > 0 ? ((p[0] - s0[0]) * dx + (p[1] - s0[1]) * dy) / L2 : 0;
    t = Math.max(0, Math.min(1, t));
    return Math.hypot(s0[0] + dx * t - p[0], s0[1] + dy * t - p[1]) <= tol;
  };
  for (let i = 0; i + 1 < pa.length; i++) {
    for (let j = 0; j + 1 < pb.length; j++) {
      const [ax, ay] = pa[i], [bx, by] = pa[i + 1], [cx, cy] = pb[j], [dx, dy] = pb[j + 1];
      const d1x = bx - ax, d1y = by - ay, d2x = dx - cx, d2y = dy - cy;
      const den = d1x * d2y - d1y * d2x;
      if (den !== 0) {
        const t = ((cx - ax) * d2y - (cy - ay) * d2x) / den;
        const u = ((cx - ax) * d1y - (cy - ay) * d1x) / den;
        if (t >= 0 && t <= 1 && u >= 0 && u <= 1) { out.push([ax + d1x * t, ay + d1y * t]); continue; }
      }
      for (const p of [pa[i], pa[i + 1]]) if (near(p, pb[j], pb[j + 1])) out.push(p);
      for (const p of [pb[j], pb[j + 1]]) if (near(p, pa[i], pa[i + 1])) out.push(p);
    }
  }
  return out;
}
const unproj = (x, y) => [y / proj.ky + proj.lat0, x / proj.kx + proj.lon0];

// ------------------------------------------------------------------ build core
const NO_CROSS = new Set(['district', 'neighbourhood', 'metro', 'other']);
const core = { v: 1, kinds: KINDS, districts: [], places: [], f: [], fam: FAMS, x: [], xp: [], xd: [], near: [] };
for (const d of DISTRICTS) core.districts.push({ ka: d.ka, ru: d.ru, en: d.en });
for (const p of PLACES) {
  const rings = areaPolygonRings(p);
  let c = p.c;
  if (!c) { const r = rings[0]; const m = r.length - 1; c = [r.slice(0, m).reduce((s, q) => s + q[0], 0) / m, r.slice(0, m).reduce((s, q) => s + q[1], 0) / m]; }
  core.places.push({ ka: p.ka, ru: p.ru, en: p.en, kind: p.kind, parent: p.parent, c: [e5(c[0]), e5(c[1])],
    km2: +polygonAreaKm2(p.poly.length ? p.poly : [rings]).toFixed(2) });
}

const geom = { v: 1, prec: 5, g: [] };
const houses = { v: 1, h: {}, place: {} };
for (let i = 0; i < F.length; i++) {
  const f = F[i];
  const lines = featureLines(f);
  const sh = shapeOf(f);
  const rec = { ka: f.ka, ru: f.ru, en: f.en, k: K[f.k] };
  if (f.o !== undefined) rec.o = f.o;
  if (f.fam !== undefined) rec.fam = f.fam;
  if (f.par !== undefined) rec.par = f.par;
  rec.d = DISTRICTS.map((d, j) => (inArea(f, areaPolygonRings(d)) ? j : -1)).filter((j) => j >= 0);
  rec.p = PLACES.map((p, j) => (inArea(f, areaPolygonRings(p)) ? j : -1)).filter((j) => j >= 0);
  rec.len = sh === 'line' ? Math.round(lineLength(f.geom)) : 0;
  let c;
  if (sh === 'point') c = f.geom.point;
  else if (sh === 'polygon') { const r = f.geom.ring, m = r.length - 1; c = [r.slice(0, m).reduce((s, q) => s + q[0], 0) / m, r.slice(0, m).reduce((s, q) => s + q[1], 0) / m]; }
  else { const q = pointAlong(f.geom, 0.5); c = unproj(q.x, q.y); }
  rec.c = [e5(c[0]), e5(c[1])];
  let a = Infinity, b = Infinity, cc = -Infinity, dd = -Infinity;
  for (const l of lines) for (const p of l) { a = Math.min(a, p[0]); b = Math.min(b, p[1]); cc = Math.max(cc, p[0]); dd = Math.max(dd, p[1]); }
  rec.bb = [e5(a), e5(b), e5(cc), e5(dd)];
  if (f.alt) rec.alt = f.alt;
  if (f.old) rec.old = f.old;
  rec.hn = f.houses ? f.houses.length : 0;
  if (f.q) rec.q = f.q;
  if (f.ar !== undefined) rec.ar = f.ar;
  core.f.push(rec);
  geom.g.push(lines.map((l) => encodePolyline(l)));
  if (f.houses) {
    const nums = f.houses.map((h) => h[1]);
    const lo = Math.min(...nums), hi = Math.max(...nums);
    houses.h[String(i)] = f.houses.map(([raw, n, lit = '', sub = '', nTo, corp], k) => {
      const frac = hi > lo ? 0.08 + 0.84 * (n - lo) / (hi - lo) : 0.5;
      const q = pointAlong(f.geom, frac);
      const side = n % 2 === 1 ? 1 : -1; // odd numbers on the left of the drawing direction
      const off = 12;
      const extra = (k % 3) * 2; // separate houses with the same number a little
      const [lat, lon] = unproj(q.x - q.dy * off * side + q.dx * extra, q.y + q.dx * off * side + q.dy * extra);
      const row = [raw, n, lit, sub, e5(lat), e5(lon)];
      if (nTo !== undefined || corp !== undefined) row.push(nTo === undefined ? null : nTo);
      if (corp !== undefined) row.push(corp);
      return row;
    });
  }
}
houses.place['2'] = [['12', 12, '', '', e5(41.69325), e5(44.81470)], ['14', 14, '', '', e5(41.69310), e5(44.81520)]];

// crossings
const crossPairs = [];
for (let i = 0; i < F.length; i++) {
  if (NO_CROSS.has(F[i].k)) continue;
  for (let j = i + 1; j < F.length; j++) {
    if (NO_CROSS.has(F[j].k)) continue;
    const li = featureLines(F[i])[0], lj = featureLines(F[j])[0];
    const pts = segIntersections(li, lj);
    // one crossing per distinct point (> 5 m apart): a pair may cross twice (export does the same)
    const kept = [];
    for (const p of pts) if (!kept.some((q) => Math.hypot(q[0] - p[0], q[1] - p[1]) < 5)) kept.push(p);
    for (const p of kept) {
      const [lat, lon] = unproj(p[0], p[1]);
      const P = [Math.round(lat * 1e5) / 1e5, Math.round(lon * 1e5) / 1e5];
      const mi = directionMask([li], P, 30, 3), mj = directionMask([lj], P, 30, 3);
      crossPairs.push([i, j, P, mi, mj]);
    }
  }
}
for (const [i, j, P, mi, mj] of crossPairs) {
  core.x.push(i, j); core.xp.push(e5(P[0]), e5(P[1])); core.xd.push(mi, mj);
}
// near pairs: line ends 8..25 m from the other line, no crossing
const crossed = new Set(crossPairs.map(([i, j]) => i + ':' + j));
for (let i = 0; i < F.length; i++) {
  if (shapeOf(F[i]) !== 'line') continue;
  for (let j = i + 1; j < F.length; j++) {
    if (shapeOf(F[j]) !== 'line' || crossed.has(i + ':' + j)) continue;
    const A = F[i].geom, B = F[j].geom;
    const d = Math.min(pointLineDistance(A[0], B), pointLineDistance(A[A.length - 1], B),
      pointLineDistance(B[0], A), pointLineDistance(B[B.length - 1], A));
    if (d >= 8 && d <= 25) core.near.push(i, j);
  }
}

const areas = {
  v: 1,
  districts: DISTRICTS.map((d) => ({ poly: d.poly.map((poly) => poly.map((r) => encodePolyline(r))) })),
  places: PLACES.map((p) => ({ poly: p.poly.map((poly) => poly.map((r) => encodePolyline(r))) })),
};
const eponyms = { v: 1, taxonomy: TAXONOMY, kinds: EP_KINDS, e: EPONYMS };

// ------------------------------------------------------------------ write + manifest
mkdirSync(OUT, { recursive: true });
const files = {};
function put(name, obj) {
  const text = JSON.stringify(obj);
  const buf = Buffer.from(text, 'utf8');
  writeFileSync(join(OUT, name), buf);
  files[name] = { bytes: buf.length, sha256: createHash('sha256').update(buf).digest('hex') };
}
put('core.json', core);
put('eponyms.json', eponyms);
put('geom.json', geom);
put('houses.json', houses);
put('areas.json', areas);
const nHouses = Object.values(houses.h).reduce((s, r) => s + r.length, 0) + Object.values(houses.place).reduce((s, r) => s + r.length, 0);
const manifest = {
  v: 1, version: '20261024-mini', built: '24/10/2026', osm: '2026-10-23T19:42:11Z', files, tiles: {},
  counts: { features: core.f.length,
    streets: core.f.filter((f) => ['street*', 'lane*'].includes(KINDS[f.k].group)).length,
    houses: nHouses, crossings: core.x.length / 2, eponyms: EPONYMS.length },
  attribution: ['© OpenStreetMap contributors, ODbL', 'Wikidata, CC0', 'Test fixture: hand-made, approximate geometry'],
};
writeFileSync(join(OUT, 'manifest.json'), JSON.stringify(manifest, null, 1));

// summary for humans
console.log(JSON.stringify(manifest.counts));
for (let t = 0; t < core.x.length / 2; t++) {
  const a = core.x[2 * t], b = core.x[2 * t + 1];
  console.log(`x${t}: ${a} ${F[a].ru} x ${b} ${F[b].ru} @ ${core.xp[2 * t] / 1e5},${core.xp[2 * t + 1] / 1e5} masks ${core.xd[2 * t].toString(2)} ${core.xd[2 * t + 1].toString(2)}`);
}
console.log('near', core.near);
core.f.forEach((f, i) => console.log(i, f.ru, 'len', f.len, 'd', f.d, 'p', f.p, 'hn', f.hn));
