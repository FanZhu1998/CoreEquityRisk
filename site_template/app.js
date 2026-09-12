/* EQRisk static viewer (blueprint §15.2): pages 1-7 of the workbench from data/*.json.
   Charts are plain SVG so the site has no dependencies and works offline. */
(function () {
  "use strict";
  const SVG_NS = "http://www.w3.org/2000/svg"; // XML namespace identifier, not a network request
  const PALETTE = ["#2f5fb3", "#d9480f", "#2b8a3e", "#ae3ec9", "#e67700", "#1098ad", "#c2255c", "#5c940d",
                   "#495057", "#7048e8", "#f59f00", "#0b7285"];
  const ANN = Math.sqrt(12);
  const D = {};

  const $ = (sel, el) => (el || document).querySelector(sel);
  function el(tag, attrs, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    for (const c of kids) e.append(c);
    return e;
  }
  function sv(tag, attrs) {
    const e = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
    return e;
  }
  function label(x, y, s, anchor) {
    const t = sv("text", { x: x, y: y, "text-anchor": anchor || "start" });
    t.textContent = s;
    return t;
  }
  const fmt = (v, d) => (v === null || v === undefined || !Number.isFinite(v) ? "" : v.toFixed(d === undefined ? 3 : d));
  const pct = (v, d) => (Number.isFinite(v) ? (100 * v).toFixed(d === undefined ? 1 : d) + "%" : "");
  const color = (j) => PALETTE[j % PALETTE.length];

  function legend(series) {
    const box = el("div", { class: "legend" });
    series.forEach((s, j) => box.append(el("span", {}, el("i", { style: "background:" + color(j) }), s.name)));
    return box;
  }

  function extent(arrays, zero) {
    let lo = Infinity, hi = -Infinity;
    for (const a of arrays) for (const v of a) if (v !== null && Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
    if (!Number.isFinite(lo)) return null;
    if (zero) { lo = Math.min(lo, 0); hi = Math.max(hi, 0); }
    if (hi === lo) { hi += 1; lo -= 1; }
    return [lo, hi];
  }

  /* xs: labels (dates); series: [{name, values}] */
  function lineChart(target, xs, series, opt) {
    opt = opt || {};
    const W = 960, H = opt.height || 260, m = { l: 58, r: 14, t: 10, b: 26 };
    const ext = extent(series.map((s) => s.values), opt.zero);
    const box = el("div", { class: "chart" });
    if (!ext || xs.length === 0) { box.textContent = "no data"; target.append(box); return; }
    const [lo, hi] = ext, n = xs.length;
    const X = (i) => m.l + (W - m.l - m.r) * (n > 1 ? i / (n - 1) : 0.5);
    const Y = (v) => m.t + (H - m.t - m.b) * (1 - (v - lo) / (hi - lo));
    const g = sv("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%" });
    for (let k = 0; k <= 4; k++) {
      const v = lo + ((hi - lo) * k) / 4, y = Y(v);
      g.append(sv("line", { x1: m.l, x2: W - m.r, y1: y, y2: y, class: "grid" }));
      g.append(label(m.l - 6, y + 4, (opt.yfmt || fmt)(v), "end"));
    }
    for (let k = 0; k < 6; k++) {
      const i = Math.round(((n - 1) * k) / 5);
      g.append(label(X(i), H - 8, String(xs[i]).slice(0, 7), "middle"));
    }
    if (opt.hline !== undefined && opt.hline >= lo && opt.hline <= hi)
      g.append(sv("line", { x1: m.l, x2: W - m.r, y1: Y(opt.hline), y2: Y(opt.hline), stroke: "#999", "stroke-dasharray": "4 3" }));
    series.forEach((s, j) => {
      let d = "", pen = false;
      s.values.forEach((v, i) => {
        if (v === null || !Number.isFinite(v)) { pen = false; return; }
        d += (pen ? "L" : "M") + X(i).toFixed(1) + "," + Y(v).toFixed(1);
        pen = true;
      });
      g.append(sv("path", { d: d, fill: "none", stroke: color(j), "stroke-width": 1.4 }));
    });
    box.append(g, legend(series));
    target.append(box);
  }

  /* horizontal bars, one row per label */
  function barChart(target, labels, values, opt) {
    opt = opt || {};
    const row = 18, W = 960, m = { l: 190, r: 70, t: 6, b: 6 }, H = m.t + m.b + row * labels.length;
    const ext = extent([values], true), box = el("div", { class: "chart" });
    if (!ext) { box.textContent = "no data"; target.append(box); return; }
    const [lo, hi] = ext, X = (v) => m.l + ((W - m.l - m.r) * (v - lo)) / (hi - lo);
    const g = sv("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%" });
    if (opt.band) {
      const [a, b] = opt.band;
      g.append(sv("rect", { x: X(a), y: m.t, width: X(b) - X(a), height: H - m.t - m.b, fill: "#e9ecef" }));
    }
    labels.forEach((lab, i) => {
      const v = values[i], y = m.t + i * row;
      g.append(label(m.l - 6, y + 13, lab, "end"));
      if (v === null || !Number.isFinite(v)) return;
      const x0 = X(opt.base === undefined ? Math.max(lo, 0) : opt.base), x1 = X(v);
      g.append(sv("rect", { x: Math.min(x0, x1), y: y + 3, width: Math.max(1, Math.abs(x1 - x0)), height: row - 6, fill: color(0) }));
      g.append(label(Math.max(x0, x1) + 4, y + 13, (opt.fmt || fmt)(v)));
    });
    if (opt.ref !== undefined) g.append(sv("line", { x1: X(opt.ref), x2: X(opt.ref), y1: m.t, y2: H - m.b, stroke: "#333" }));
    box.append(g);
    target.append(box);
  }

  function histogram(target, values, bins, opt) {
    const v = values.filter((x) => x !== null && Number.isFinite(x));
    if (!v.length) { target.append(el("div", { class: "chart" }, "no data")); return; }
    const lo = Math.min(...v), hi = Math.max(...v), w = (hi - lo) / bins || 1, counts = new Array(bins).fill(0);
    for (const x of v) counts[Math.min(bins - 1, Math.floor((x - lo) / w))]++;
    const labels = counts.map((_, i) => (opt && opt.xfmt ? opt.xfmt(lo + (i + 0.5) * w) : fmt(lo + (i + 0.5) * w, 2)));
    barChart(target, labels, counts, { fmt: (x) => String(x), base: 0 });
  }

  function heatmap(target, labels, M) {
    const n = labels.length, cell = 22, m = { l: 190, t: 150 }, W = m.l + n * cell + 10, H = m.t + n * cell + 10;
    const g = sv("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%" });
    const shade = (c) => {
      const a = Math.min(1, Math.abs(c));
      return c >= 0 ? `rgb(${255},${Math.round(255 - 180 * a)},${Math.round(255 - 180 * a)})`
        : `rgb(${Math.round(255 - 180 * a)},${Math.round(255 - 140 * a)},255)`;
    };
    labels.forEach((lab, i) => {
      g.append(label(m.l - 6, m.t + i * cell + 15, lab, "end"));
      const t = label(0, 0, lab);
      t.setAttribute("transform", `translate(${m.l + i * cell + 15},${m.t - 6}) rotate(-60)`);
      g.append(t);
      labels.forEach((_, j) => {
        const r = sv("rect", { x: m.l + j * cell, y: m.t + i * cell, width: cell - 1, height: cell - 1, fill: shade(M[i][j]) });
        const tip = sv("title");
        tip.textContent = `${labels[i]} / ${labels[j]}: ${fmt(M[i][j], 2)}`;
        r.append(tip);
        g.append(r);
      });
    });
    target.append(el("div", { class: "chart" }, g));
  }

  function table(target, header, rows, classes) {
    const t = el("table");
    t.append(el("tr", {}, ...header.map((h) => el("th", {}, h))));
    rows.forEach((r, i) => t.append(el("tr", {}, ...r.map((c, j) => el("td", classes && classes(i, j) ? { class: classes(i, j) } : {}, c === null || c === undefined ? "" : String(c))))));
    target.append(t);
  }

  function select(options, value, onchange) {
    const s = el("select");
    for (const o of options) { const opt = el("option", { value: o }, o); if (o === value) opt.selected = true; s.append(opt); }
    s.addEventListener("change", () => onchange(s.value));
    return s;
  }

  const groupNames = (g) => D.snapshot.groups[g].map((k) => D.snapshot.factors[k]);

  /* ---------------- pages ---------------- */

  function pageStatus(root) {
    const st = D.status, h = D.history, last = st.runs.length ? st.runs[st.runs.length - 1] : null;
    if (last && last.status === "QUARANTINED")
      root.append(el("div", { class: "banner" }, `Session ${last.as_of} is QUARANTINED; LATEST_GOOD stays at ${st.latest_good ? st.latest_good.as_of : "none"}.`));
    const lf = h.lambda_F.filter(Number.isFinite), ls = h.lambda_S.filter(Number.isFinite);
    const cards = el("div", { class: "cards" });
    const card = (k, v) => cards.append(el("div", { class: "card" }, el("div", { class: "muted" }, k), el("div", { class: "v" }, v)));
    card("Snapshot", D.snapshot.as_of);
    card("LATEST_GOOD", st.latest_good ? st.latest_good.as_of : st.latest);
    card("Last daily run", last ? `${last.as_of} ${last.status}` : "none yet");
    card("λ_F", fmt(lf[lf.length - 1], 2));
    card("λ_S", fmt(ls[ls.length - 1], 2));
    root.append(cards);
    root.append(el("h2", {}, "Volatility regime multipliers"));
    lineChart(root, h.vra_dates, [{ name: "lambda_F", values: h.lambda_F }, { name: "lambda_S", values: h.lambda_S }], { hline: 1 });
    root.append(el("h2", {}, "Recent daily runs"));
    if (!st.runs.length) root.append(el("p", { class: "muted" }, "No daily runs yet (backfill only)."));
    else table(root, ["session", "status", "failed gates"], st.runs.slice().reverse().map((r) => [r.as_of, r.status, r.failed_gates.join(", ")]), (i, j) => (j === 1 ? st.runs[st.runs.length - 1 - i].status : null));
    if (last && last.gates.length) {
      root.append(el("h2", {}, `Gates for ${last.as_of}`));
      table(root, ["gate", "level", "ok", "detail"], last.gates.map((g) => [g.gate, g.level, g.ok ? "yes" : "no", g.detail]));
    }
  }

  function pageReturns(root) {
    const h = D.history, ctl = el("div", { class: "controls" }), out = el("div");
    const draw = (g) => {
      out.replaceChildren();
      lineChart(out, h.dates, groupNames(g).filter((f) => h.cum[f]).map((f) => ({ name: f, values: h.cum[f] })), { yfmt: (v) => pct(v, 0), hline: 0 });
    };
    ctl.append("Group ", select(["style", "industry", "country"], "style", draw));
    root.append(el("h2", {}, "Cumulative factor returns"), ctl, out);
    draw("style");
    root.append(el("h2", {}, "Regression R² (cap-weighted)"));
    lineChart(root, h.r2_dates, [{ name: "R²", values: h.r2 }]);
  }

  function pageRisk(root) {
    const h = D.history, s = D.snapshot, ctl = el("div", { class: "controls" }), out = el("div");
    const draw = (g) => {
      out.replaceChildren();
      lineChart(out, h.vol_dates, groupNames(g).filter((f) => h.vol_ann[f]).map((f) => ({ name: f, values: h.vol_ann[f] })), { yfmt: (v) => pct(v, 0) });
    };
    ctl.append("Group ", select(["style", "industry", "country"], "style", draw));
    root.append(el("h2", {}, "Annualized factor volatility"), ctl, out);
    draw("style");
    root.append(el("h2", {}, `Factor correlations on ${s.as_of}`));
    const vol = s.F.map((r, k) => Math.sqrt(r[k]));
    heatmap(root, s.factors, s.F.map((r, i) => r.map((v, j) => v / (vol[i] * vol[j]))));
    root.append(el("h2", {}, "Volatility regime vs cross-sectional factor volatility"));
    const cs = h.cs_vol.filter(Number.isFinite), scale = cs.length ? 1 / (cs.reduce((a, b) => a + b, 0) / cs.length) : 1;
    lineChart(root, h.vra_dates, [{ name: "lambda_F", values: h.lambda_F }, { name: "cs_vol / mean", values: h.cs_vol.map((v) => (Number.isFinite(v) ? v * scale : null)) }], { hline: 1 });
  }

  function pageExposures(root) {
    const s = D.snapshot, styles = groupNames("style"), si = D.snapshot.groups.style;
    const ctl = el("div", { class: "controls" }), prof = el("div"), dist = el("div");
    const input = el("input", { list: "tickers", value: s.tickers.includes("AAPL") ? "AAPL" : s.tickers[0] });
    const dl = el("datalist", { id: "tickers" }, ...s.tickers.map((t) => el("option", { value: t })));
    const showProfile = () => {
      prof.replaceChildren();
      const i = s.tickers.indexOf(input.value.trim().toUpperCase());
      if (i < 0) { prof.append(el("p", { class: "muted" }, "Unknown ticker")); return; }
      prof.append(el("p", {}, `${s.tickers[i]}: ${s.industry[i] || "no industry"}, ${s.in_estu[i] ? "in" : "not in"} the estimation universe`));
      barChart(prof, styles, si.map((k) => s.X[i][k]), { ref: 0 });
    };
    input.addEventListener("change", showProfile);
    ctl.append("Ticker ", input, dl);
    root.append(el("h2", {}, "Exposure profile"), ctl, prof);
    showProfile();
    const c2 = el("div", { class: "controls" });
    const drawDist = (style) => { dist.replaceChildren(); const k = s.factors.indexOf(style); histogram(dist, s.X.map((r) => r[k]), 30); };
    c2.append("Style ", select(styles, "SIZE", drawDist));
    root.append(el("h2", {}, "Cross-sectional distribution"), c2, dist);
    drawDist("SIZE");
    root.append(el("h2", {}, "Industries (estimation universe)"));
    const agg = {};
    s.tickers.forEach((_, i) => {
      if (!s.in_estu[i] || !s.industry[i]) return;
      const a = (agg[s.industry[i]] = agg[s.industry[i]] || { n: 0, w: 0, w2: 0 });
      a.n += 1; a.w += s.capw[i] || 0; a.w2 += (s.capw[i] || 0) ** 2;
    });
    const rows = Object.entries(agg).sort((a, b) => b[1].w - a[1].w).map(([k, a]) => [k, a.n, pct(a.w), fmt(a.w * a.w / a.w2, 1)]);
    table(root, ["industry", "names", "cap weight", "N_eff"], rows);
  }

  function pageSpecific(root) {
    const s = D.snapshot, sp = D.specific;
    root.append(el("h2", {}, "Annualized specific volatility (final)"));
    histogram(root, sp.sigma_final, 30, { xfmt: (v) => pct(v, 0) });
    root.append(el("h2", {}, "Specific-risk layers by name"));
    const ctl = el("div", { class: "controls" }), out = el("div");
    const input = el("input", { list: "tickers2", value: s.tickers.includes("AAPL") ? "AAPL" : s.tickers[0] });
    const show = () => {
      out.replaceChildren();
      const i = s.tickers.indexOf(input.value.trim().toUpperCase());
      if (i < 0) return;
      table(out, ["layer", "value"], [["time series (annualized)", pct(sp.sigma_ts[i])], ["structural", pct(sp.sigma_str[i])],
        ["blend", pct(sp.sigma_blend[i])], ["final (shrunk, regime-adjusted)", pct(sp.sigma_final[i])], ["gamma (weight on time series)", fmt(sp.gamma[i], 2)]]);
    };
    input.addEventListener("change", show);
    ctl.append("Ticker ", input, el("datalist", { id: "tickers2" }, ...s.tickers.map((t) => el("option", { value: t }))));
    root.append(ctl, out);
    show();
    root.append(el("h2", {}, "Gamma"));
    histogram(root, sp.gamma, 20);
    const v = D.validation;
    if (v && v.specific_deciles.length) {
      root.append(el("h2", {}, `Bias by decile, ${v.start} to ${v.end}`));
      table(root, ["grouping", "decile", "full stack", "time series only"], v.specific_deciles.map((r) => [r.grouping, r.decile, fmt(+r["full stack"]), fmt(+r["time series only"])]));
    }
  }

  function parseCsv(text) {
    const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
    const head = lines[0].toLowerCase().split(",").map((x) => x.trim());
    const hasHead = head.includes("ticker");
    const cols = hasHead ? head : ["ticker", "weight", "bench_weight"];
    return (hasHead ? lines.slice(1) : lines).map((l) => {
      const v = l.split(",");
      const r = {};
      cols.forEach((c, j) => (r[c] = v[j] === undefined ? undefined : v[j].trim()));
      return r;
    });
  }

  function pageAnalyzer(root) {
    const s = D.snapshot, out = el("div");
    const area = el("textarea", { spellcheck: "false" });
    area.value = "ticker,weight\nAAPL,0.25\nMSFT,0.25\nJPM,0.25\nXOM,0.25";
    const file = el("input", { type: "file", accept: ".csv" });
    file.addEventListener("change", () => { const f = file.files[0]; if (f) f.text().then((t) => { area.value = t; run(); }); });
    const btn = el("button", {}, "Analyze");
    const run = () => {
      out.replaceChildren();
      const { h, hb, unmatched } = EQRisk.holdingsVectors(s, parseCsv(area.value));
      const r = EQRisk.portfolioRisk(s, h, hb);
      if (unmatched.length) out.append(el("p", { class: "banner" }, "Not in the model on this date: " + unmatched.join(", ")));
      const cards = el("div", { class: "cards" });
      const card = (k, v) => cards.append(el("div", { class: "card" }, el("div", { class: "muted" }, k), el("div", { class: "v" }, v)));
      const tot = r.sigma * r.sigma;
      card(hb ? "Active risk (ann.)" : "Total risk (ann.)", pct(r.sigma_ann, 2));
      card("Factor share", pct(r.factor_var / tot));
      card("Specific share", pct(r.specific_var / tot));
      card(hb ? "Beta vs benchmark" : "Beta vs ESTU", fmt(r.beta, 3));
      out.append(cards, el("h2", {}, "Risk by group (share of variance)"));
      table(out, ["group", "variance share"], Object.entries(r.groups).map(([g, v]) => [g, pct(v / tot)]));
      out.append(el("h2", {}, "Factors: exposure, volatility, x-σ-ρ contribution"));
      const order = s.factors.map((_, k) => k).filter((k) => Math.abs(r.exposures[k]) > 1e-12).sort((a, b) => Math.abs(r.xsr[b]) - Math.abs(r.xsr[a]));
      table(out, ["factor", "exposure", "vol (ann.)", "corr with portfolio", "x-σ-ρ (ann.)", "variance share"],
        order.map((k) => [s.factors[k], fmt(r.exposures[k]), pct(r.vol[k] * ANN), fmt(r.corr[k], 2), pct(r.xsr[k] * ANN, 2), pct(r.contrib_var[k] / tot)]));
      out.append(el("h2", {}, "Largest risk contributors"));
      const w = hb ? h.map((x, i) => x - hb[i]) : h;
      const idx = w.map((_, i) => i).filter((i) => w[i] !== 0).sort((a, b) => Math.abs(w[b] * r.mctr[b]) - Math.abs(w[a] * r.mctr[a])).slice(0, 15);
      table(out, ["ticker", "weight", "MCTR (ann.)", "risk share", "beta"], idx.map((i) => [s.tickers[i], pct(w[i], 2), pct(r.mctr[i] * ANN, 2), pct(w[i] * r.mctr[i] / r.sigma), fmt(r.betas[i], 2)]));
    };
    btn.addEventListener("click", run);
    root.append(el("h2", {}, "Holdings (CSV: ticker, weight[, bench_weight])"), area, el("div", { class: "controls" }, file, btn), out);
    run();
  }

  function pageValidation(root) {
    const v = D.validation;
    if (!v) { root.append(el("p", { class: "muted" }, "No validation report: run `eqrisk validate`, then export again.")); return; }
    root.append(el("p", {}, `${v.start} to ${v.end}, ${v.periods} non-overlapping periods; a bias statistic's 95% band is 1 ± ${fmt(Math.sqrt(2 / v.periods))}.`));
    root.append(el("h2", {}, "Success criteria (§1.3)"));
    table(root, ["area", "criterion", "value", "status"], v.scorecard.map((r) => [r.area, r.criterion, r.value, r.status]), (i, j) => (j === 3 ? v.scorecard[i].status.replace(" ", "_") : null));
    root.append(el("h2", {}, "External checks (§11.3)"));
    table(root, ["area", "check", "value", "status"], v.external.map((r) => [r.area, r.criterion, r.value, r.status]), (i, j) => (j === 3 ? v.external[i].status : null));
    root.append(el("h2", {}, "Pure factor portfolios: bias statistic"));
    const band = Math.sqrt(2 / v.periods);
    barChart(root, v.factor.map((r) => r.portfolio), v.factor.map((r) => +r.bias), { band: [1 - band, 1 + band], ref: 1, base: 0 });
    if (v.eigen.length) {
      root.append(el("h2", {}, "Eigenfactor portfolios before and after the adjustment"));
      lineChart(root, v.eigen.map((r) => String(+r.k + 1)), [{ name: "before", values: v.eigen.map((r) => +r.bias_before) }, { name: "after", values: v.eigen.map((r) => +r.bias_after) }], { hline: 1 });
    }
  }

  const PAGES = { status: pageStatus, returns: pageReturns, risk: pageRisk, exposures: pageExposures, specific: pageSpecific, analyzer: pageAnalyzer, validation: pageValidation };
  const drawn = {};
  function show(name) {
    document.querySelectorAll("nav button").forEach((b) => b.classList.toggle("active", b.dataset.page === name));
    document.querySelectorAll(".page").forEach((p) => p.classList.toggle("active", p.id === name));
    if (!drawn[name]) { drawn[name] = true; PAGES[name]($("#" + name)); }
  }

  Promise.all(["snapshot", "history", "status", "specific", "validation"].map((n) => fetch(`data/${n}.json`).then((r) => r.json()).then((j) => (D[n] = j))))
    .then(() => {
      $("#asof").textContent = `${D.snapshot.model_id} · as of ${D.snapshot.as_of} · ${D.snapshot.tickers.length} names × ${D.snapshot.factors.length} factors`;
      document.querySelectorAll("nav button").forEach((b) => b.addEventListener("click", () => show(b.dataset.page)));
      show("status");
    })
    .catch((e) => { document.body.prepend(el("div", { class: "banner" }, "Could not load data/ (serve this folder with `python -m http.server`): " + e)); });
})();
