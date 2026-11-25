/* CutLab - UI. Nessun framework: stato semplice, render esplicito.
   Regola: ogni modifica dell'utente (toggle di una battuta, slider) ricalcola il
   piano sul server e ridisegna, cosi' l'anteprima audio e' sempre quella vera. */

const $ = (id) => document.getElementById(id);

const S = {
  inv: null,
  meta: {},
  peaks: null,
  turns: [],
  plan: { segments: [], stats: {} },
  audioUrl: null,
  zoom: 1,
  viewT0: 0,          // secondo iniziale della vista
  busy: false,
  dur: 0,
  fps: 24,
  hoverT: null,
};

/* ------------------------------------------------------------------ utilita' */

const fmtTC = (sec, fps = 24) => {
  if (!isFinite(sec) || sec < 0) sec = 0;
  const f = Math.floor((sec % 1) * fps);
  const s = Math.floor(sec);
  const p = (n, l = 2) => String(n).padStart(l, '0');
  return `${p(Math.floor(s / 3600))}:${p(Math.floor(s / 60) % 60)}:${p(s % 60)}:${p(f)}`;
};
const fmtDur = (sec) => {
  if (!isFinite(sec)) return '—';
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return m ? `${m}:${String(s).padStart(2, '0')}` : `${s}s`;
};
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const SPEAKER_COLORS = () => [cssVar('--spk0'), cssVar('--spk1'), cssVar('--spk2'), cssVar('--spk3')];

let toastTimer = null;
function toast(msg, kind = '') {
  const el = $('toast');
  el.textContent = msg;
  el.className = 'toast show ' + kind;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = 'toast ' + kind; }, 5200);
}

async function api(path, body) {
  const opt = body === undefined
    ? { method: 'GET' }
    : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  const txt = await r.text();
  try { return JSON.parse(txt); } catch { throw new Error(txt.slice(0, 300)); }
}

/* ------------------------------------------------------------------ inventario */

async function refresh() {
  $('ctxMain').textContent = 'Connessione a Resolve…';
  const inv = await api('/api/inventory');
  if (!inv.ok) {
    $('ctxMain').textContent = 'Resolve non raggiungibile';
    $('ctxSub').textContent = inv.error || 'Apri DaVinci Resolve con una timeline.';
    $('btnAnalyze').disabled = true;
    return;
  }
  S.inv = inv;
  S.fps = inv.fps;
  S.dur = inv.seconds;
  $('ctxMain').textContent = `${inv.project} · ${inv.timeline}`;
  $('ctxSub').textContent =
    `${inv.width}×${inv.height} · ${inv.fps} fps · ${fmtDur(inv.seconds)}` +
    (inv.used_marks ? ' · range In/Out' : ' · tutta la timeline');

  const sel = $('audioTrack');
  sel.innerHTML = inv.audio_tracks.map((t) =>
    `<option value="${t.index}">A${t.index} · ${(t.names[0] || 'vuota')} · ${Math.round(t.coverage * 100)}%</option>`
  ).join('') || '<option value="1">nessuna traccia audio</option>';
  if (inv.default_audio) sel.value = String(inv.default_audio);

  const box = $('videoTracks');
  box.innerHTML = inv.video_tracks.map((t) => {
    const on = inv.default_video.includes(t.index) ? 'checked' : '';
    return `<label class="check">
      <input type="checkbox" value="${t.index}" ${on}>
      <b>V${t.index}</b> <span>${(t.names[0] || '—')}</span>
      <span class="tdir">${Math.round(t.coverage * 100)}%</span></label>`;
  }).join('') || '<p class="hint">Nessuna traccia video.</p>';

  $('btnAnalyze').disabled = false;
  const noAudio = inv.audio_tracks.length === 0;
  if (noAudio) {
    $('ctxSub').textContent += ' · nessuna traccia audio';
    $('btnAnalyze').disabled = true;
  }
  await loadState();
}

async function loadState() {
  const st = await api('/api/state');
  if (st.meta && Object.keys(st.meta).length) {
    S.meta = st.meta;
    S.peaks = st.peaks;
    S.turns = st.turns || [];
    S.plan = st.plan || { segments: [], stats: {} };
    if (st.params) applyParams(st.params);
    S.fps = S.meta.fps || S.fps;
    S.dur = S.meta.seconds || S.dur;
    $('emptyState').hidden = S.turns.length > 0;
    renderAll();
  }
}

/* ------------------------------------------------------------------ analisi */

async function analyze() {
  const audio = Number($('audioTrack').value);
  const videos = [...$('videoTracks').querySelectorAll('input:checked')].map((i) => Number(i.value));
  if (!audio) { toast('Scegli la traccia audio da analizzare.', 'err'); return; }
  $('loading').hidden = false;
  $('emptyState').hidden = true;
  $('btnAnalyze').disabled = true;
  $('progressLabel').textContent = 'Analisi in corso…';
  $('log').textContent = '';
  try {
    const res = await api('/api/analyze', {
      audio_track: audio, video_tracks: videos,
      use_marks: !!S.inv?.used_marks,
      ...params(),
      transcribe: true,
    });
    if (!res.ok) throw new Error(res.error || 'analisi rifiutata');
    await pollJob();
  } catch (e) {
    $('progressLabel').textContent = 'Analisi fallita';
    $('log').textContent += `\n${e.message}`;
    toast(e.message, 'err');
  } finally {
    $('btnAnalyze').disabled = false;
  }
}

async function pollJob() {
  for (;;) {
    await new Promise((r) => setTimeout(r, 700));
    const j = await api('/api/job');
    const pct = Math.round((j.progress || 0) * 100);
    $('progressBar').style.width = pct + '%';
    $('progressPct').textContent = pct + '%';
    $('log').textContent = (j.log || []).join('\n');
    $('log').scrollTop = $('log').scrollHeight;
    if (j.state === 'done') { $('loading').hidden = true; await loadState(); return j; }
    if (j.state === 'error') {
      $('progressLabel').textContent = 'Errore';
      throw new Error(j.error || 'errore');
    }
  }
}

/* ------------------------------------------------------------------ parametri */

const PARAM_IDS = ['minPause', 'padStart', 'padEnd', 'minSpeech'];
const PARAM_KEY = { minPause: 'min_pause_ms', padStart: 'pad_start_ms', padEnd: 'pad_end_ms', minSpeech: 'min_speech_ms' };
const PARAM_OUT = { minPause: 'minPauseOut', padStart: 'padStartOut', padEnd: 'padEndOut', minSpeech: 'minSpeechOut' };

function params() {
  const p = {};
  for (const id of PARAM_IDS) p[PARAM_KEY[id]] = Number($(id).value);
  return p;
}

function applyParams(p) {
  for (const id of PARAM_IDS) {
    const v = p[PARAM_KEY[id]];
    if (v != null) {
      $(id).value = v;
      $(PARAM_OUT[id]).textContent = v + ' ms';
    }
  }
}

function syncOutputs() {
  for (const id of PARAM_IDS) $(PARAM_OUT[id]).textContent = $(id).value + ' ms';
}

let replanTimer = null;
function scheduleReplan(preview = true) {
  clearTimeout(replanTimer);
  replanTimer = setTimeout(() => replan(preview), 220);
}

async function replan(preview = true) {
  if (!S.turns.length) return;
  try {
    const res = await api('/api/plan', { ...params(), turns: S.turns, preview });
    if (!res.ok) throw new Error(res.error || 'piano non calcolato');
    S.plan = res.plan;
    if (res.preview) {
      S.audioUrl = res.preview.url;
      $('audio').src = res.preview.url;
      $('playerInfo').textContent = `Anteprima del taglio · ${fmtDur(res.preview.seconds)}`;
    }
    renderAll();
  } catch (e) {
    toast(e.message, 'err');
  }
}

/* ------------------------------------------------------------------ render */

function renderAll() {
  renderChips();
  renderSpeakers();
  renderTurns();
  renderStats();
  draw();
  drawRuler();
  $('btnBuild').disabled = !S.plan.segments?.length;
  $('legend').innerHTML = speakersUsed().map((sp) =>
    `<span><i style="background:${shipColor(sp)}"></i>voce ${sp + 1}</span>`).join('') +
    `<span><i style="background:${cssVar('--cut')}"></i>tagliato</span>`;
}

function speakersUsed() {
  return [...new Set(S.turns.filter((t) => t.keep).map((t) => t.speaker))].sort();
}

function renderChips() {
  const box = $('trackChips');
  const vids = [...$('videoTracks').querySelectorAll('input:checked')].map((i) => i.value);
  const aud = $('audioTrack').value;
  if (!S.inv) { box.innerHTML = ''; return; }
  const chips = vids.map((v) => {
    const t = S.inv.video_tracks.find((x) => String(x.index) === v) || {};
    return `<span class="track-chip"><b>V${v}</b> ${escapeHtml((t.names && t.names[0]) || '')}
      <span class="pill">${t.coverage != null ? Math.round(t.coverage * 100) + '%' : ''}</span></span>`;
  });
  if (aud) chips.push(`<span class="track-chip"><b>A${aud}</b> audio di riferimento</span>`);
  box.innerHTML = chips.join('');
}
function shipColor(sp) {
  const c = SPEAKER_COLORS();
  return c[sp % c.length];
}

function renderSpeakers() {
  const box = $('speakers');
  if (!S.turns.length) {
    box.innerHTML = '<p class="hint">Le voci compaiono dopo l\'analisi.</p>';
    return;
  }
  const groups = {};
  for (const t of S.turns) {
    groups[t.speaker] = groups[t.speaker] || { sec: 0, n: 0, keep: 0 };
    groups[t.speaker].sec += t.end - t.start;
    groups[t.speaker].n++;
    if (t.keep) groups[t.speaker].keep++;
  }
  const ids = Object.keys(groups).map(Number).sort((a, b) => groups[b].sec - groups[a].sec);
  box.innerHTML = ids.map((sp) => {
    const g = groups[sp];
    const on = g.keep > 0;
    return `<button class="speaker ${on ? '' : 'off'}" data-sp="${sp}" title="Tieni o scarta questa voce">
      <span class="dot" style="background:${shipColor(sp)}"></span>
      <span class="name">Voce ${sp + 1}${sp === ids[0] ? ' · chi parla di più' : ''}</span>
      <span class="meta">${fmtDur(g.sec)} · ${g.keep}/${g.n}</span>
    </button>`;
  }).join('');
  box.querySelectorAll('.speaker').forEach((b) => {
    b.onclick = () => {
      const sp = Number(b.dataset.sp);
      const anyOn = S.turns.some((t) => t.speaker === sp && t.keep);
      S.turns.forEach((t) => { if (t.speaker === sp) t.keep = !anyOn; });
      replan();
    };
  });
}

function renderTurns() {
  const box = $('turns');
  $('turnsCount').textContent = S.turns.length || '';
  if (!S.turns.length) { box.innerHTML = ''; return; }
  box.innerHTML = S.turns.map((t, i) => `
    <div class="turn ${t.keep ? '' : 'off'}" data-i="${i}">
      <span class="t-time">${fmtTC(t.start, S.fps)}</span>
      <span class="t-dot" style="background:${shipColor(t.speaker)}"></span>
      <span class="t-text"><span class="who">voce ${t.speaker + 1}${t.keep ? '' : ' · tagliata'}</span>${escapeHtml(t.text || '—')}</span>
    </div>`).join('');
  box.querySelectorAll('.turn').forEach((el) => {
    el.onclick = (ev) => {
      const i = Number(el.dataset.i);
      if (ev.shiftKey) {
        S.turns[i].speaker = (S.turns[i].speaker + 1) % Math.max(2, speakersUsed().length + 1);
      } else {
        S.turns[i].keep = !S.turns[i].keep;
      }
      replan();
    };
  });
}

const escapeHtml = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

function renderStats() {
  const st = S.plan.stats || {};
  $('statStart').textContent = st.total_s != null ? fmtDur(st.total_s) : '—';
  $('statEnd').textContent = st.kept_s != null ? fmtDur(st.kept_s) : '—';
  $('statPieces').textContent = st.pieces ?? '—';
  $('statCuts').textContent = st.cuts ?? '—';
  const bits = [];
  if (st.removed_pct != null) bits.push(`−${st.removed_pct}% di durata`);
  if (S.plan.dropped_frames > 0) bits.push(`${S.plan.dropped_frames} frame scartati perché una traccia non li copre`);
  $('statNote').textContent = bits.join(' · ');
}

/* ------------------------------------------------------------------ canvas */

function viewSpan() { return Math.max(0.4, S.dur / S.zoom); }
function t2x(t, W) { return ((t - S.viewT0) / viewSpan()) * W; }
function x2t(x, W) { return S.viewT0 + (x / W) * viewSpan(); }

function fitCanvas(cv) {
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = cv.clientHeight;
  if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
    cv.width = Math.round(w * dpr);
    cv.height = Math.round(h * dpr);
  }
  const ctx = cv.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

function draw() {
  const cv = $('wave');
  if (!cv.clientWidth) return;
  const { ctx, w, h } = fitCanvas(cv);
  ctx.clearRect(0, 0, w, h);

  const mid = h / 2;
  const span = viewSpan();
  const t0 = S.viewT0, t1 = t0 + span;

  // tacche di secondo
  ctx.strokeStyle = 'rgba(255,255,255,0.05)';
  ctx.lineWidth = 1;
  const step = chooseStep(span, w);
  for (let t = Math.ceil(t0 / step) * step; t < t1; t += step) {
    const x = t2x(t, w);
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }

  // forma d'onda
  if (S.peaks && S.peaks.max?.length) {
    const n = S.peaks.max.length;
    ctx.fillStyle = 'rgba(245,243,236,0.30)';
    for (let x = 0; x < w; x++) {
      const tA = x2t(x, w), tB = x2t(x + 1, w);
      const i0 = Math.floor((tA / S.dur) * n), i1 = Math.max(i0 + 1, Math.floor((tB / S.dur) * n));
      let lo = 0, hi = 0;
      for (let i = i0; i < i1 && i < n; i++) { lo = Math.min(lo, S.peaks.min[i]); hi = Math.max(hi, S.peaks.max[i]); }
      const y1 = mid - hi * (h / 2 - 26), y2 = mid - lo * (h / 2 - 26);
      ctx.fillRect(x, y1, 1, Math.max(1, y2 - y1));
    }
  }

  // fasce delle battute (sopra): colore voce = tenuta, grigio = tagliata
  const barH = 14, barY = 12;
  for (const t of S.turns) {
    const x1 = t2x(t.start, w), x2 = t2x(t.end, w);
    if (x2 < 0 || x1 > w) continue;
    const col = t.keep ? shipColor(t.speaker) : cssVar('--cut');
    ctx.fillStyle = col;
    ctx.globalAlpha = t.keep ? 0.85 : 0.9;
    ctx.fillRect(x1, barY, Math.max(1, x2 - x1), barH);
    if (!t.keep) {
      ctx.globalAlpha = 1;
      ctx.strokeStyle = 'rgba(255,255,255,0.10)';
      for (let x = Math.max(x1, 0); x < Math.min(x2, w); x += 6) {
        ctx.beginPath(); ctx.moveTo(x, barY); ctx.lineTo(x + barH, barY + barH); ctx.stroke();
      }
    }
  }
  ctx.globalAlpha = 1;

  // pezzi tenuti (sotto): banda piena
  const keepY = h - 22;
  for (const s of (S.plan.segments || [])) {
    const a = (s.f0 - (S.meta.start || 0)) / S.fps;
    const b = (s.f1 - (S.meta.start || 0)) / S.fps;
    const x1 = t2x(a, w), x2 = t2x(b, w);
    if (x2 < 0 || x1 > w) continue;
    ctx.fillStyle = cssVar('--keep');
    ctx.globalAlpha = 0.75;
    ctx.fillRect(x1, keepY, Math.max(1, x2 - x1), 6);
  }
  ctx.globalAlpha = 1;

  // cursore del mouse
  if (S.hoverT != null) {
    const x = t2x(S.hoverT, w);
    ctx.strokeStyle = 'rgba(229,164,59,0.7)';
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, h); ctx.stroke();
  }
}

function chooseStep(span, w) {
  const target = span / Math.max(4, w / 110);
  const steps = [0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600];
  return steps.find((s) => s >= target) || 600;
}

function drawRuler() {
  const cv = $('ruler');
  if (!cv.clientWidth) return;
  const { ctx, w, h } = fitCanvas(cv);
  ctx.clearRect(0, 0, w, h);
  ctx.fillStyle = 'rgba(155,155,155,0.85)';
  ctx.font = "11px 'Manrope', sans-serif";
  ctx.textBaseline = 'top';
  const span = viewSpan(), t0 = S.viewT0, t1 = t0 + span;
  const step = chooseStep(span, w);
  for (let t = Math.ceil(t0 / step) * step; t < t1; t += step) {
    const x = t2x(t, w);
    ctx.fillRect(x, 0, 1, 6);
    ctx.fillText(fmtTC(t, S.fps), Math.min(x + 4, w - 60), 9);
  }
}

/* ------------------------------------------------------------------ eventi */

function bind() {
  $('btnRefresh').onclick = () => refresh();
  $('btnAnalyze').onclick = analyze;
  $('btnAnalyze2').onclick = analyze;

  $('audioTrack').onchange = () => { /* la traccia si applica alla prossima analisi */ };

  for (const id of PARAM_IDS) {
    $(id).oninput = () => { syncOutputs(); scheduleReplan(true); };
  }

  $('zoom').oninput = (e) => {
    const prevSpan = viewSpan();
    S.zoom = Number(e.target.value);
    const c = S.viewT0 + prevSpan / 2;
    S.viewT0 = Math.max(0, Math.min(S.dur - viewSpan(), c - viewSpan() / 2));
    draw(); drawRuler();
  };

  const cv = $('wave');
  cv.addEventListener('wheel', (e) => {
    e.preventDefault();
    const rect = cv.getBoundingClientRect();
    const tAt = x2t(e.clientX - rect.left, rect.width);
    const prevSpan = viewSpan();
    S.zoom = Math.max(1, Math.min(12, S.zoom * (e.deltaY < 0 ? 1.15 : 0.87)));
    $('zoom').value = S.zoom;
    const newSpan = viewSpan();
    S.viewT0 = Math.max(0, Math.min(S.dur - newSpan, tAt - (tAt - S.viewT0) * (newSpan / prevSpan)));
    draw(); drawRuler();
  }, { passive: false });

  let drag = null;
  cv.addEventListener('mousedown', (e) => { drag = { x: e.clientX, t0: S.viewT0, moved: false }; });
  window.addEventListener('mouseup', (e) => {
    if (drag && !drag.moved) {
      const rect = cv.getBoundingClientRect();
      onWaveClick(e.clientX - rect.left, rect.width, e.shiftKey);
    }
    drag = null;
  });
  window.addEventListener('mousemove', (e) => {
    const rect = cv.getBoundingClientRect();
    if (drag && e.buttons === 1) {
      const dx = e.clientX - drag.x;
      if (Math.abs(dx) > 3) drag.moved = true;
      const perPx = viewSpan() / rect.width;
      S.viewT0 = Math.max(0, Math.min(S.dur - viewSpan(), drag.t0 - dx * perPx));
      draw(); drawRuler();
      return;
    }
    if (e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom) {
      S.hoverT = x2t(e.clientX - rect.left, rect.width);
      draw();
    }
  });

  $('btnPlay').onclick = () => {
    const a = $('audio');
    if (!a.src) { toast('Prima analizza o ricalcola il piano.'); return; }
    if (a.paused) a.play(); else a.pause();
    $('btnPlay').classList.toggle('playing', !a.paused);
  };
  $('audio').onended = () => $('btnPlay').classList.remove('playing');

  $('btnBuild').onclick = buildResult;

  window.addEventListener('resize', () => { draw(); drawRuler(); });
  window.addEventListener('keydown', (e) => {
    if (e.code === 'Space' && e.target.tagName !== 'INPUT') { e.preventDefault(); $('btnPlay').click(); }
  });
}

function onWaveClick(x, W, shift) {
  const t = x2t(x, W);
  const i = S.turns.findIndex((tu) => t >= tu.start && t <= tu.end);
  if (i < 0) return;
  if (shift) {
    S.turns[i].speaker = (S.turns[i].speaker + 1) % Math.max(2, speakersUsed().length + 1);
  } else {
    S.turns[i].keep = !S.turns[i].keep;
  }
  replan();
}

/* ------------------------------------------------------------------ build */

async function buildResult() {
  const videos = [...$('videoTracks').querySelectorAll('input:checked')].map((i) => Number(i.value));
  if (!videos.length) { toast('Seleziona almeno una traccia video.', 'err'); return; }
  const audio = S.meta.audio_track || Number($('audioTrack').value);
  $('btnBuild').disabled = true;
  $('buildNote').textContent = 'Scrivo in Resolve…';
  try {
    const res = await api('/api/build', { video_tracks: videos, audio_track: audio });
    if (!res.ok) throw new Error(res.error || 'build rifiutata');
    $('loading').hidden = false;
    $('progressLabel').textContent = 'Costruzione timeline…';
    const j = await pollJob();
    const r = j.result || {};
    if (r.ok) {
      toast(`Timeline «${r.timeline}» creata: ${r.pieces} pezzi, ${fmtDur(r.seconds)}.`, 'ok');
      $('buildNote').textContent = `«${r.timeline}» · ${r.clips} clip · verifica ${r.verification?.ok ? 'superata' : 'fallita'}`;
      if (r.backup) $('buildNote').textContent += ` · backup .drp salvato`;
    } else {
      throw new Error((r.verification?.problems || []).join(' · ') || 'verifica non superata');
    }
  } catch (e) {
    toast(e.message, 'err');
    $('buildNote').textContent = e.message;
  } finally {
    $('btnBuild').disabled = false;
    $('loading').hidden = true;
  }
}

/* ------------------------------------------------------------------ avvio */

bind();
refresh().catch((e) => {
  $('ctxMain').textContent = 'Resolve non raggiungibile';
  $('ctxSub').textContent = e.message;
});
