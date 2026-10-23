#!/usr/bin/env node
// Acceptance run over real riddles of past Runcity games in Tbilisi.
//
//   node tests/acceptance/run_acceptance.mjs [--pack DIR] [--tasks FILE] [--only SUBSTR]
//        [--verbose] [--json OUT.json] [--strict] [--no-addresses]
//
// Loads the pack (app/public/data/*.json) and the logic modules
// (app/src/search/index.js, app/src/dsl/{parser,eval}.js; interfaces in
// docs/client-architecture.md), runs every query of tests/acceptance/runcity_tasks.json
// without a zone and checks that the answer street is among the results and within the
// first `max_results_ok`. A task counts as solved when at least one of its queries passes.
// Metric: solved >= 90% of tasks with solvable_by_search = true.
//
// If the pack or the modules are missing (they are built by other parts of the project),
// the script says so and exits with code 0. With --strict it exits 1 when the metric fails.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..', '..');
const THRESHOLD = 0.9;

// ------------------------------------------------------------------ args
function parseArgs(argv) {
  const a = { pack: path.join(ROOT, 'app', 'public', 'data'), tasks: path.join(HERE, 'runcity_tasks.json'),
    addresses: path.join(HERE, 'runcity_addresses.json'), useAddresses: true,
    only: null, verbose: false, json: null, strict: false };
  for (let i = 2; i < argv.length; i++) {
    const k = argv[i];
    const next = () => { if (i + 1 >= argv.length) throw new Error(`${k} needs a value`); return argv[++i]; };
    if (k === '--pack') a.pack = path.resolve(next());
    else if (k === '--tasks') a.tasks = path.resolve(next());
    else if (k === '--addresses') a.addresses = path.resolve(next());
    else if (k === '--no-addresses') a.useAddresses = false;
    else if (k === '--only') a.only = next();
    else if (k === '--verbose' || k === '-v') a.verbose = true;
    else if (k === '--json') a.json = path.resolve(next());
    else if (k === '--strict') a.strict = true;
    else if (k === '--help' || k === '-h') { a.help = true; }
    else throw new Error(`unknown argument ${k}`);
  }
  return a;
}

// ------------------------------------------------------------------ logging (logs/acceptance_js.log)
const LOG_DIR = path.join(ROOT, 'logs');
let logStream = null;
function stamp() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${p(d.getDate())}/${p(d.getMonth() + 1)}/${d.getFullYear()} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}
function logLine(level, msg) {
  try {
    if (!logStream) { fs.mkdirSync(LOG_DIR, { recursive: true }); logStream = fs.createWriteStream(path.join(LOG_DIR, 'acceptance_js.log'), { flags: 'a' }); }
    logStream.write(`${stamp()} ${level.padEnd(7)} acceptance: ${msg}\n`);
  } catch { /* logging must never break the run */ }
}
const log = {
  info: (m) => logLine('INFO', m),
  warn: (m) => { logLine('WARNING', m); },
  error: (m) => { logLine('ERROR', m); },
};

function out(s = '') { process.stdout.write(s + '\n'); }

function finish(code) {
  if (logStream) logStream.end();
  process.exitCode = code;
}

// ------------------------------------------------------------------ loading
function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function loadPack(dir) {
  const need = ['core.json'];
  const optional = ['manifest.json', 'eponyms.json', 'geom.json', 'houses.json', 'areas.json'];
  for (const f of need) if (!fs.existsSync(path.join(dir, f))) return { missing: path.join(dir, f) };
  const pack = {};
  for (const f of [...need, ...optional]) {
    const p = path.join(dir, f);
    if (fs.existsSync(p)) {
      const t0 = Date.now();
      pack[f.replace('.json', '')] = readJson(p);
      log.info(`loaded ${p} in ${Date.now() - t0} ms`);
    }
  }
  pack.ready = Promise.resolve(pack);
  return { pack };
}

async function loadModules() {
  const files = {
    index: path.join(ROOT, 'app', 'src', 'search', 'index.js'),
    parser: path.join(ROOT, 'app', 'src', 'dsl', 'parser.js'),
    eval: path.join(ROOT, 'app', 'src', 'dsl', 'eval.js'),
    normalize: path.join(ROOT, 'app', 'src', 'search', 'normalize.js'),
  };
  const missing = Object.values(files).filter((f) => !fs.existsSync(f));
  if (missing.length) return { missing };
  const mods = {};
  for (const [k, f] of Object.entries(files)) {
    try {
      mods[k] = await import(pathToFileURL(f).href);
    } catch (e) {
      return { importError: `${path.relative(ROOT, f)}: ${e && e.message}` };
    }
  }
  const api = {
    buildIndex: mods.index.buildIndex,
    parse: mods.parser.parse,
    evaluate: mods.eval.evaluate,
    skeleton: mods.normalize.skeleton,
    stripType: mods.normalize.stripType,
  };
  const absent = Object.entries(api).filter(([, v]) => typeof v !== 'function').map(([k]) => k);
  if (absent.length) return { importError: `missing exports: ${absent.join(', ')}` };
  return { api };
}

// ------------------------------------------------------------------ answer matching
function kindCode(core, f) {
  const k = core.kinds && core.kinds[f.k];
  return k ? k.code : null;
}

/** Feature indices that are the answer of a task (by street_ka/alt_ka, else by skeleton of street_ru). */
function answerFeatures(task, core, api) {
  const a = task.answer || {};
  const sk = (s) => api.skeleton(String(s || ''));
  const kaSet = new Set([a.street_ka, ...(a.alt_ka || [])].filter(Boolean).map(sk).filter(Boolean));
  const hits = [];
  if (kaSet.size) {
    core.f.forEach((f, i) => {
      const names = [f.ka, ...(f.alt || []), ...((f.old || []).map((o) => o && o.ka))].filter(Boolean);
      if (names.some((n) => kaSet.has(sk(n)))) hits.push(i);
    });
  }
  if (!hits.length && a.street_ru) {
    const base = sk(api.stripType(a.street_ru));
    if (base) {
      core.f.forEach((f, i) => {
        if (a.kind && a.kind !== 'other' && kindCode(core, f) !== a.kind) return;
        const names = [f.ru, ...((f.old || []).map((o) => o && o.ru))].filter(Boolean);
        if (names.some((n) => sk(api.stripType(n)) === base)) hits.push(i);
      });
    }
  }
  return hits;
}

function houseNumberOf(q) {
  const m = /(?:^|[\s(])(?:дом|h)\s*[:=]\s*=?(\d+)/i.exec(q);
  return m ? parseInt(m[1], 10) : null;
}

function featureHasHouse(index, i, n) {
  try {
    const rows = typeof index.houses === 'function' ? index.houses(i) : null;
    if (!rows) return null;
    return rows.some((r) => r && (r[1] === n || (r.length > 6 && typeof r[6] === 'number' && r[1] <= n && n <= r[6])));
  } catch {
    return null;
  }
}

function nearestFeatures(core, point, k = 3) {
  if (!point) return [];
  const [lat, lon] = point;
  const cos = Math.cos((lat * Math.PI) / 180);
  const scored = [];
  core.f.forEach((f, i) => {
    if (!f.c) return;
    const dy = (f.c[0] / 1e5 - lat) * 110540;
    const dx = (f.c[1] / 1e5 - lon) * 111320 * cos;
    scored.push([Math.hypot(dx, dy), i]);
  });
  scored.sort((a, b) => a[0] - b[0]);
  return scored.slice(0, k).map(([d, i]) => `${core.f[i].ru || core.f[i].ka} (${Math.round(d)} м)`);
}

// ------------------------------------------------------------------ running
function runQuery(api, index, q) {
  const t0 = Date.now();
  let p;
  try {
    p = api.parse(q.q);
  } catch (e) {
    return { ok: false, reason: 'parse', detail: `исключение парсера: ${e && e.message}`, ms: Date.now() - t0 };
  }
  if (!p || p.error) {
    const e = (p && p.error) || {};
    const sugg = e.suggestions && e.suggestions.length ? ` (варианты: ${e.suggestions.slice(0, 5).join(', ')})` : '';
    return { ok: false, reason: 'parse', detail: `ошибка разбора: ${e.message || '?'}${sugg}`, ms: Date.now() - t0 };
  }
  let r;
  try {
    r = api.evaluate(p.ast, index, { zone: null, me: null, mapCenter: null });
  } catch (e) {
    return { ok: false, reason: 'eval', detail: `исключение evaluate: ${e && e.message}`, ms: Date.now() - t0 };
  }
  if (!r) return { ok: false, reason: 'eval', detail: 'evaluate вернул пусто', ms: Date.now() - t0 };
  if (r.error) {
    const sugg = r.error.suggestions && r.error.suggestions.length ? ` (варианты: ${r.error.suggestions.slice(0, 5).join(', ')})` : '';
    return { ok: false, reason: 'eval', detail: `ошибка значения: ${r.error.message}${sugg}`, ms: Date.now() - t0, count: 0 };
  }
  return { r, ms: Date.now() - t0 };
}

function checkQuery(api, index, core, task, answerSet, q) {
  const res = runQuery(api, index, q);
  if (!res.r) return { q: q.q, mode: q.mode, ...res };
  const order = res.r.order || [];
  const count = typeof res.r.count === 'number' ? res.r.count : order.length;
  let rank = -1;
  for (let k = 0; k < order.length; k++) if (answerSet.has(order[k])) { rank = k; break; }
  const max = q.max_results_ok || 30;
  const base = { q: q.q, mode: q.mode, count, rank, max, ms: res.ms, warnings: res.r.warnings || [] };
  if (!answerSet.size) return { ...base, ok: false, reason: 'no_answer_in_pack', detail: 'ответной улицы нет в пакете' };
  if (rank >= 0 && rank < max) return { ...base, ok: true };
  if (rank >= max) return { ...base, ok: false, reason: 'rank', detail: `ответ на месте ${rank + 1} из ${count} (допустимо ${max})` };
  // not in results: try to explain
  const n = houseNumberOf(q.q);
  if (n !== null) {
    const has = [...answerSet].map((i) => featureHasHouse(index, i, n));
    if (has.every((h) => h === false)) return { ...base, ok: false, reason: 'no_house', detail: `у ответной улицы в пакете нет дома ${n} (найдено ${count})` };
  }
  return { ...base, ok: false, reason: 'not_found', detail: `ответа нет среди ${count} результатов` };
}

function pct(a, b) { return b ? `${((100 * a) / b).toFixed(0)}%` : '—'; }

function pad(s, n) { s = String(s); return s.length >= n ? s : s + ' '.repeat(n - s.length); }

async function main() {
  let args;
  try { args = parseArgs(process.argv); } catch (e) { out(`Ошибка аргументов: ${e.message}`); return finish(2); }
  if (args.help) {
    out('node tests/acceptance/run_acceptance.mjs [--pack DIR] [--tasks FILE] [--only SUBSTR] [--verbose] [--json OUT] [--strict] [--no-addresses]');
    return finish(0);
  }
  log.info(`start: pack=${args.pack} tasks=${args.tasks}`);
  out('Приёмка Quchebi: реальные задания «Бегущего города» в Тбилиси');

  if (!fs.existsSync(args.tasks)) { out(`Нет файла заданий ${args.tasks}`); log.error('no tasks file'); return finish(2); }
  const doc = readJson(args.tasks);
  let tasks = doc.tasks || [];
  if (args.only) tasks = tasks.filter((t) => t.id.includes(args.only) || (t.task_type || []).includes(args.only));

  const lp = loadPack(args.pack);
  if (lp.missing) {
    out(`Пакет данных ещё не собран: нет ${path.relative(ROOT, lp.missing)}. Приёмка пропущена.`);
    log.warn(`pack missing: ${lp.missing}`);
    return finish(0);
  }
  const lm = await loadModules();
  if (lm.missing) {
    out(`Модули поиска ещё не готовы: нет ${lm.missing.map((f) => path.relative(ROOT, f)).join(', ')}. Приёмка пропущена.`);
    log.warn(`modules missing: ${lm.missing.join(', ')}`);
    return finish(0);
  }
  if (lm.importError) {
    out(`Модули поиска не загружаются (${lm.importError}). Приёмка пропущена.`);
    log.warn(`modules import error: ${lm.importError}`);
    return finish(0);
  }
  const { api } = lm;
  const { pack } = lp;
  const core = pack.core;
  const nF = (core.f || []).length;
  const counts = (pack.manifest && pack.manifest.counts) || {};
  out(`Пакет: ${pack.manifest ? pack.manifest.version : '?'} — объектов ${nF}, домов ${counts.houses ?? '?'}, пересечений ${counts.crossings ?? '?'}`);
  if (nF < 1000) out('ВНИМАНИЕ: пакет очень маленький (похоже на тестовую фикстуру) — результаты не показательны.');

  let index;
  const tIdx = Date.now();
  try {
    index = api.buildIndex(pack);
    if (index && index.ready && typeof index.ready.then === 'function') await index.ready;
  } catch (e) {
    out(`buildIndex упал: ${e && e.message}. Приёмка пропущена.`);
    log.error(`buildIndex failed: ${e && e.stack}`);
    return finish(0);
  }
  out(`Индекс построен за ${Date.now() - tIdx} мс. Заданий: ${tasks.length}.`);
  log.info(`index built in ${Date.now() - tIdx} ms, ${tasks.length} tasks`);

  // ---------------------------------------------------------------- riddles
  const results = [];
  for (const t of tasks) {
    const ans = answerFeatures(t, core, api);
    const answerSet = new Set(ans);
    const qres = (t.queries || []).map((q) => checkQuery(api, index, core, t, answerSet, q));
    const solved = qres.some((r) => r.ok);
    const solvedFacts = qres.some((r) => r.ok && r.mode === 'facts');
    results.push({ id: t.id, task_type: t.task_type || [], event: t.event, solvable: !!t.solvable_by_search,
      solved, solvedFacts, answerInPack: answerSet.size > 0, answerFeatures: ans, queries: qres,
      near: answerSet.size ? [] : nearestFeatures(core, t.kp_point) });
    log.info(`${t.id}: solvable=${t.solvable_by_search} solved=${solved} answer_features=${ans.length} ` +
      qres.map((r) => `[${r.q} -> ${r.ok ? 'ok' : r.reason}${r.count !== undefined ? ' n=' + r.count : ''}]`).join(' '));
  }

  const solv = results.filter((r) => r.solvable);
  const solvedN = solv.filter((r) => r.solved).length;
  const factsN = solv.filter((r) => r.solvedFacts).length;
  const missingAns = solv.filter((r) => !r.answerInPack).length;

  out('');
  out(`Заданий всего: ${results.length}; решаемых поиском: ${solv.length}; решено: ${solvedN} (${pct(solvedN, solv.length)}); ` +
    `из них только по фактам загадки: ${factsN}; ответной улицы нет в пакете: ${missingAns}`);

  // by task type
  const byType = new Map();
  for (const r of solv) for (const tt of r.task_type) {
    const s = byType.get(tt) || { n: 0, ok: 0, facts: 0 };
    s.n++; if (r.solved) s.ok++; if (r.solvedFacts) s.facts++;
    byType.set(tt, s);
  }
  out('');
  out('По типам заданий (решаемые поиском):');
  out(`  ${pad('тип', 20)} ${pad('решено', 12)} по фактам`);
  for (const [tt, s] of [...byType.entries()].sort((a, b) => b[1].n - a[1].n)) {
    out(`  ${pad(tt, 20)} ${pad(`${s.ok}/${s.n} ${pct(s.ok, s.n)}`, 12)} ${s.facts}`);
  }
  const byEvent = new Map();
  for (const r of solv) {
    const s = byEvent.get(r.event) || { n: 0, ok: 0 };
    s.n++; if (r.solved) s.ok++;
    byEvent.set(r.event, s);
  }
  out('');
  out('По играм:');
  for (const [ev, s] of [...byEvent.entries()].sort()) out(`  ${pad(ev, 20)} ${s.ok}/${s.n} ${pct(s.ok, s.n)}`);

  // reasons
  const reasons = new Map();
  for (const r of solv) if (!r.solved) {
    const best = r.queries.find((q) => q.reason === 'rank') || r.queries.find((q) => q.reason === 'no_house') || r.queries[0];
    const key = best ? best.reason : 'no_queries';
    reasons.set(key, (reasons.get(key) || 0) + 1);
  }
  if (reasons.size) {
    out('');
    out('Причины провалов (по лучшему запросу задания): ' + [...reasons.entries()].map(([k, v]) => `${k}=${v}`).join(', '));
  }

  out('');
  out('Провалы:');
  let nFail = 0;
  for (const r of solv) {
    if (r.solved) continue;
    nFail++;
    const t = tasks.find((x) => x.id === r.id);
    out(`  ${r.id} [${r.task_type.join(',')}] ответ: ${t.answer.street_ru || t.answer.street_ka}${t.answer.house ? ', д. ' + t.answer.house : ''}`);
    if (!r.answerInPack && r.near.length) out(`      в пакете нет ответной улицы; рядом с КП: ${r.near.join('; ')}`);
    for (const q of r.queries) out(`      ${pad(q.q, 44)} -> ${q.detail || q.reason}`);
  }
  if (!nFail) out('  нет');

  if (args.verbose) {
    out('');
    out('Все запросы:');
    for (const r of results) {
      out(`  ${r.id}${r.solvable ? '' : ' (не решается поиском)'}: ${r.solved ? 'РЕШЕНО' : 'нет'}`);
      for (const q of r.queries) out(`      ${q.ok ? '+' : '-'} ${pad(q.q, 44)} n=${q.count ?? '-'} rank=${q.rank !== undefined && q.rank >= 0 ? q.rank + 1 : '-'} ${q.ms ?? ''} мс ${q.ok ? '' : q.detail || q.reason}`);
    }
  }

  const unsolvHit = results.filter((r) => !r.solvable && r.solved);
  if (unsolvHit.length) {
    out('');
    out(`Помечены как нерешаемые, но запрос нашёл ответ (пересмотреть разметку): ${unsolvHit.map((r) => r.id).join(', ')}`);
  }

  // ---------------------------------------------------------------- KP addresses (latin)
  let addrSummary = null;
  if (args.useAddresses && fs.existsSync(args.addresses)) {
    const ad = readJson(args.addresses);
    const items = (ad.items || []).filter((t) => !args.only || t.id.includes(args.only));
    let ok = 0, okLatin = 0, nLatin = 0, inPack = 0;
    const fails = [];
    for (const t of items) {
      const ans = new Set(answerFeatures(t, core, api));
      if (ans.size) inPack++;
      const qres = (t.queries || []).map((q) => checkQuery(api, index, core, t, ans, q));
      const lat = qres.filter((q) => q.mode === 'latin');
      if (lat.length) { nLatin++; if (lat.some((q) => q.ok)) okLatin++; }
      if (qres.some((q) => q.ok)) ok++; else fails.push({ t, qres, ans });
    }
    addrSummary = { total: items.length, solved: ok, latin: nLatin, latinSolved: okLatin, answerInPack: inPack };
    out('');
    out(`Адреса КП (латиница с табличек и карточек КП): ${items.length}; найдено: ${ok} (${pct(ok, items.length)}); ` +
      `по латинице: ${okLatin}/${nLatin}; ответная улица есть в пакете: ${inPack}`);
    const show = args.verbose ? fails : fails.slice(0, 15);
    for (const f of show) {
      out(`  ${f.t.id}: ${f.t.raw_address}`);
      for (const q of f.qres) out(`      ${pad(q.q, 44)} -> ${q.detail || q.reason}`);
    }
    if (fails.length > show.length) out(`  ... ещё ${fails.length - show.length} (--verbose)`);
  }

  // ---------------------------------------------------------------- verdict
  const share = solv.length ? solvedN / solv.length : 0;
  const passed = share >= THRESHOLD;
  out('');
  out(`ИТОГ: решено ${solvedN} из ${solv.length} решаемых поиском (${pct(solvedN, solv.length)}), порог ${THRESHOLD * 100}% — ` +
    (passed ? 'ПРИЁМКА ПРОЙДЕНА' : 'ПРИЁМКА НЕ ПРОЙДЕНА'));
  log.info(`verdict: ${solvedN}/${solv.length} passed=${passed}`);

  if (args.json) {
    const report = { built: stamp(), pack: pack.manifest ? pack.manifest.version : null, features: nF,
      solvable: solv.length, solved: solvedN, solvedByFacts: factsN, threshold: THRESHOLD, passed,
      byType: Object.fromEntries(byType), addresses: addrSummary, results };
    fs.mkdirSync(path.dirname(args.json), { recursive: true });
    fs.writeFileSync(args.json, JSON.stringify(report, null, 1));
    out(`Отчёт: ${args.json}`);
  }
  return finish(args.strict && !passed ? 1 : 0);
}

main().catch((e) => {
  out(`Непредвиденная ошибка: ${e && e.stack}`);
  log.error(`unexpected: ${e && e.stack}`);
  finish(0);
});
