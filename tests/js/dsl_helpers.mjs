// Shared helpers for the DSL tests (not a test file itself).

/** Compact one-line rendering of a DSL AST, used to compare parse results in tables. */
export function sexpr(node) {
  if (!node) return '∅';
  if (node.type === 'query') {
    const dirs = [];
    if (node.zone) dirs.push(`zone=${node.zone}`);
    if (node.sort) dirs.push(`sort=${node.sort}`);
    const body = sexpr(node.expr);
    return dirs.length ? `[${dirs.join(' ')}] ${body}` : body;
  }
  switch (node.type) {
    case 'or': return `(or ${node.items.map(sexpr).join(' ')})`;
    case 'and': return `(and ${node.items.map(sexpr).join(' ')})`;
    case 'not': return `(not ${sexpr(node.item)})`;
    case 'text':
      if (node.mode === 'regex') return `/${node.value}/`;
      if (node.mode === 'quoted') return `"${node.value}"`;
      return `'${node.value}'`;
    case 'filter': return filterExpr(node);
    default: return `?${node.type}`;
  }
}

function num(nm) {
  if (nm.op === 'range') return `${nm.a}-${nm.b}`;
  return `${nm.op}${nm.a}`;
}

function house(q) {
  const p = q.probable ? '?' : '';
  switch (q.kind) {
    case 'any': return `any${q.n}${p}`;
    case 'exact': return `exact${q.n}${p}`;
    case 'lit': return `${q.exact ? 'exact' : ''}lit${q.n}${q.lit}${p}`;
    case 'sub': return `${q.exact ? 'exact' : ''}sub${q.n}${q.lit || ''}/${q.sub}${p}`;
    case 'corp': return `corp${q.n}k${q.corp}${p}`;
    case 'range': return `range${q.from}-${q.to}`;
    default: return q.kind;
  }
}

function filterExpr(f) {
  switch (f.key) {
    case 'name': return `name:${f.items.map((i) => `${i.mode}=${i.value}`).join(',')}`;
    case 'type': return `type:${f.items.map((i) => i.value + (i.star ? '*' : '')).join(',')}`;
    case 'district': case 'in': case 'who':
      return `${f.key}:${f.items.map((i) => i.value).join(',')}`;
    case 'bio': return `bio:${f.items.map((i) => (i.mode === 'regex' ? `/${i.value}/` : i.value)).join(',')}`;
    case 'century': case 'houses': case 'len': case 'ord':
      return `${f.key}:${f.nums.map(num).join(',')}`;
    case 'gender': case 'own': return `${f.key}:${f.items.map((i) => i.value).join(',')}`;
    case 'house': return `house:${f.items.map((i) => house(i.q)).join(',')}`;
    case 'cross': return `x(${sexpr(f.sub)})${f.dir ? '~' + f.dir.bit : ''}`;
    case 'near': return `near(${f.me ? 'me' : sexpr(f.sub)})${f.radius ? '~' + f.radius.m : ''}`;
    case 'family': return `fam(${sexpr(f.sub)})`;
    case 'old': return `old(${sexpr(f.sub)})`;
    default: return `${f.key}?`;
  }
}

/** Feature names of a result for readable assertions. */
export function names(result, F) {
  const byIdx = new Map(Object.entries(F).map(([k, v]) => [v, k]));
  return [...result.order].map((i) => byIdx.get(i) ?? String(i));
}

/** Sorted feature-key list of a result (order-insensitive comparisons). */
export function nameSet(result, F) {
  return names(result, F).sort();
}
