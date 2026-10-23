// "О приложении": purpose, data sources and licences, versions.
import { h } from './dom.js';
import { registerScreen, navigate } from './router.js';
import { state } from './state.js';
import { fmtDate } from './format.js';

export function initAbout() {
  const root = document.getElementById('screen-about');
  registerScreen('about', { onShow: () => render(root) });
}

function render(root) {
  const m = state.pack?.manifest;
  const link = (href, text) => h('a', { href, target: '_blank', rel: 'noopener' }, text);
  const page = h('div.page',
    h('div.btn-row', h('button.btn.small', { type: 'button', onclick: () => navigate('ready') }, 'Назад')),
    h('div.plate-big', h('span.ka', 'ქუჩები'), h('span.lat', 'Quchebi')),
    h('p', 'Офлайн-поиск улиц, площадей и других топонимов Тбилиси для игры «Бегущий город»: названия на грузинском, русском и латинице, язык запросов, эпонимы, номера домов, пересечения и зона соревнований.'),
    h('div.card-box',
      h('h3', 'Данные и лицензии'),
      h('p', 'Улицы, дома, районы: ', link('https://www.openstreetmap.org/copyright', '© участники OpenStreetMap'), ', лицензия ', link('https://opendatacommons.org/licenses/odbl/', 'ODbL 1.0'), '. Пакет данных — производная база данных OSM и распространяется на тех же условиях.'),
      h('p', 'Сведения об эпонимах: ', link('https://www.wikidata.org/', 'Wikidata'), ' (CC0).'),
      h('p', 'Факты из статей ', link('https://ru.wikipedia.org/', 'Википедии'), ' (годы, прежние названия) — CC BY-SA 4.0; тексты статей в приложении не хранятся.'),
      h('p', 'Подложка карты: ', link('https://protomaps.com/', 'Protomaps'), ' (basemap © OpenStreetMap, ODbL), шрифты Noto (SIL OFL).'),
      h('p', 'Категории и описания с меткой «оценка ИИ» сформированы языковой моделью и могут содержать ошибки.'),
      (m?.attribution || []).length ? h('ul.small', m.attribution.map((a) => h('li', a))) : null),
    h('div.card-box',
      h('h3', 'Версии'),
      h('p.small', `Пакет данных: ${m?.version || '—'}${m?.built ? `, собран ${fmtDate(m.built)}` : ''}${m?.osm ? `, снимок OSM ${fmtDate(m.osm)}` : ''}.`),
      h('p.small', `Приложение: ${__APP_VERSION__}, сборка ${fmtDate(__BUILD_TIME__)}.`),
      h('p.small', 'Библиотеки: MapLibre GL JS, PMTiles, Protomaps basemaps, Terra Draw, Turf, simplify-js, MiniSearch, idb-keyval, Workbox.')),
  );
  root.replaceChildren(page);
}
