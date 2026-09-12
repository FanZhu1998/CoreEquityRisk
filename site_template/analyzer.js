/* EQRisk portfolio analyzer: the algebra of eqrisk.analytics.risk (blueprint §10), monthly units.

   snap: {factors, groups: {country, industry, style}, tickers, X (N x K), F (K x K), spec_var (N),
          capw (ESTU cap weights, N), in_estu (N)}
   h, hb: holdings over snap.tickers; hb (benchmark) is optional. Without it, betas are measured
   against the cap-weighted estimation universe. Loaded by index.html and by the Python tests. */
(function (root) {
  "use strict";

  function dot(a, b) {
    let s = 0;
    for (let i = 0; i < a.length; i++) s += a[i] * b[i];
    return s;
  }

  function matVec(A, v) {
    return A.map((row) => dot(row, v));
  }

  function exposuresOf(X, w, K) {
    const x = new Array(K).fill(0);
    for (let i = 0; i < X.length; i++) {
      const wi = w[i];
      if (wi === 0) continue;
      const row = X[i];
      for (let k = 0; k < K; k++) x[k] += row[k] * wi;
    }
    return x;
  }

  function marketPortfolio(snap) {
    const w = snap.capw.map((c, i) => (snap.in_estu[i] && Number.isFinite(c) ? c : 0));
    const s = w.reduce((a, b) => a + b, 0);
    return w.map((x) => x / s);
  }

  function portfolioRisk(snap, h, hb) {
    const X = snap.X, F = snap.F, spec = snap.spec_var, K = snap.factors.length, N = X.length;
    const w = hb ? h.map((v, i) => v - hb[i]) : h.slice();
    const x = exposuresOf(X, w, K);
    const Fx = matVec(F, x);
    const factorVar = dot(x, Fx);
    let specificVar = 0;
    for (let i = 0; i < N; i++) specificVar += w[i] * w[i] * spec[i];
    const sigma = Math.sqrt(factorVar + specificVar);
    const vol = F.map((row, k) => Math.sqrt(row[k]));
    const contrib = x.map((v, k) => v * Fx[k]);
    const corr = Fx.map((v, k) => (sigma > 0 ? v / (vol[k] * sigma) : 0));
    const xsr = x.map((v, k) => v * vol[k] * corr[k]);
    const mctr = X.map((row, i) => (sigma > 0 ? (dot(row, Fx) + spec[i] * w[i]) / sigma : 0));
    const bench = hb || marketPortfolio(snap);
    const xb = exposuresOf(X, bench, K);
    const Fxb = matVec(F, xb);
    let varB = dot(xb, Fxb);
    for (let i = 0; i < N; i++) varB += bench[i] * bench[i] * spec[i];
    const betas = X.map((row, i) => (dot(row, Fxb) + spec[i] * bench[i]) / varB);
    const groups = {};
    for (const [g, idx] of Object.entries(snap.groups)) groups[g] = idx.reduce((s, k) => s + contrib[k], 0);
    groups.specific = specificVar;
    return {
      sigma: sigma, sigma_ann: sigma * Math.sqrt(12), factor_var: factorVar, specific_var: specificVar,
      exposures: x, vol: vol, contrib_var: contrib, corr: corr, xsr: xsr, mctr: mctr, betas: betas,
      beta: dot(h, betas), groups: groups,
    };
  }

  /* rows: [{ticker, weight, bench_weight}] -> {h, hb (or null), unmatched tickers} */
  function holdingsVectors(snap, rows) {
    const pos = new Map(snap.tickers.map((t, i) => [t, i]));
    const N = snap.tickers.length;
    const h = new Array(N).fill(0);
    const hasBench = rows.some((r) => r.bench_weight !== undefined && r.bench_weight !== "");
    const hb = hasBench ? new Array(N).fill(0) : null;
    const unmatched = [];
    for (const r of rows) {
      const i = pos.get(String(r.ticker).trim().toUpperCase());
      if (i === undefined) {
        unmatched.push(r.ticker);
        continue;
      }
      h[i] += Number(r.weight || 0);
      if (hb) hb[i] += Number(r.bench_weight || 0);
    }
    return { h: h, hb: hb, unmatched: unmatched };
  }

  root.EQRisk = { portfolioRisk: portfolioRisk, marketPortfolio: marketPortfolio, holdingsVectors: holdingsVectors };
})(typeof globalThis !== "undefined" ? globalThis : this);
