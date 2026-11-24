// reads {pairs: [...], moves: [...]} from stdin, prints the results of the editor's collision code as JSON
const G = require('../../components/sheet_editor/geom.js');
const input = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const out = { pairs: [], moves: [], turns: [], labels: [] };
for (const c of input.pairs) {
  out.pairs.push(G.collides(G.makePart(c.a), c.ax, c.ay, G.makePart(c.b), c.bx, c.by, c.gap));
}
for (const m of input.moves) {
  const parts = m.parts.map(G.makePart);
  const offs = m.parts.map(() => [0, 0]);
  out.moves.push(G.moveConstrained(parts, offs, m.i, 0, 0, m.dx, m.dy, m.W, m.H, m.margin, m.gap));
}
for (const t of (input.turns || [])) out.turns.push(G.transformPolys(t.polys, t.ox, t.oy, t.op, t.value));
for (const l of (input.labels || [])) out.labels.push(G.bestLabel(G.makePart(l.polys), l.name, l.maxFs, l.minFs));
process.stdout.write(JSON.stringify(out));
