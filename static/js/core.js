/* ============================================================
   GRAPHSUM core.js — 共享工具：API / Toast / 对话框 / 任务控制台
   ============================================================ */
'use strict';

window.gs = (function () {

  /* ---------- API ---------- */
  async function api(path, opts = {}) {
    const init = { method: opts.method || 'GET', headers: {} };
    if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path, init);
    let data = null;
    try { data = await res.json(); } catch (e) { /* 非 JSON 响应 */ }
    if (!res.ok) {
      const msg = (data && (data.message || data.error)) || `请求失败 (HTTP ${res.status})`;
      const err = new Error(msg);
      err.status = res.status; err.data = data;
      throw err;
    }
    return data;
  }

  /* ---------- 工具 ---------- */
  function escapeHtml(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtSize(bytes) {
    if (bytes == null || isNaN(bytes)) return '—';
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1024 / 1024).toFixed(2) + ' MB';
  }

  function pad2(n) { return String(n).padStart(2, '0'); }

  function download(url, filename) {
    const a = document.createElement('a');
    a.href = url; a.download = filename || '';
    document.body.appendChild(a); a.click(); a.remove();
  }

  /* ---------- Toast ---------- */
  let toastHost = null;
  function toast(message, kind = 'info', title) {
    if (!toastHost) {
      toastHost = document.createElement('div');
      toastHost.className = 'toasts';
      toastHost.setAttribute('aria-live', 'polite');
      document.body.appendChild(toastHost);
    }
    const titles = { ok: '完成', err: '错误', info: '提示' };
    const el = document.createElement('div');
    el.className = `toast toast--${kind}`;
    el.innerHTML = `<span class="sq"></span><div class="tx"><b>${escapeHtml(title || titles[kind] || '提示')}</b>${escapeHtml(message)}</div>`;
    toastHost.appendChild(el);
    const life = kind === 'err' ? 7000 : 4200;
    setTimeout(() => {
      el.classList.add('out');
      setTimeout(() => el.remove(), 220);
    }, life);
    while (toastHost.children.length > 4) toastHost.firstChild.remove();
    return el;
  }

  /* ---------- 确认对话框（瑞士风格，替代原生 confirm） ---------- */
  function confirmDialog({ title = '确认操作', body = '', okLabel = '确认', cancelLabel = '取消', danger = false, kicker = 'CONFIRM' }) {
    return new Promise((resolve) => {
      const modal = document.createElement('div');
      modal.className = 'modal open';
      modal.innerHTML = `
        <div class="modal-box" role="dialog" aria-modal="true">
          <div class="m-kicker"><span class="no">${escapeHtml(kicker)}</span><span class="dash"></span></div>
          <h3>${escapeHtml(title)}</h3>
          <div class="m-body">${escapeHtml(body)}</div>
          <div class="m-acts">
            <button class="btn-inline" data-act="cancel">${escapeHtml(cancelLabel)}</button>
            <span class="sp"></span>
            <button class="btn-inline ${danger ? 'btn-inline--red' : ''}" data-act="ok">${escapeHtml(okLabel)}</button>
          </div>
        </div>`;
      document.body.appendChild(modal);
      const okBtn = modal.querySelector('[data-act="ok"]');
      const done = (val) => {
        document.removeEventListener('keydown', onKey);
        modal.remove();
        resolve(val);
      };
      const onKey = (e) => {
        if (e.key === 'Escape') done(false);
        if (e.key === 'Enter') done(true);
      };
      modal.querySelector('[data-act="ok"]').addEventListener('click', () => done(true));
      modal.querySelector('[data-act="cancel"]').addEventListener('click', () => done(false));
      modal.addEventListener('click', (e) => { if (e.target === modal) done(false); });
      document.addEventListener('keydown', onKey);
      okBtn.focus();
    });
  }

  /* ---------- 步进器 ---------- */
  function bindStepper(root) {
    const input = root.querySelector('input');
    const [minus, plus] = root.querySelectorAll('button');
    const clamp = (v) => {
      const min = parseFloat(input.min), max = parseFloat(input.max);
      if (!isNaN(min)) v = Math.max(min, v);
      if (!isNaN(max)) v = Math.min(max, v);
      return v;
    };
    const step = parseFloat(input.step) || 1;
    minus.addEventListener('click', () => { input.value = clamp((parseFloat(input.value) || 0) - step); input.dispatchEvent(new Event('change')); });
    plus.addEventListener('click', () => { input.value = clamp((parseFloat(input.value) || 0) + step); input.dispatchEvent(new Event('change')); });
    input.addEventListener('change', () => { input.value = clamp(parseFloat(input.value) || parseFloat(input.min) || 0); });
  }

  /* ---------- 入场动效 ---------- */
  function initReveal() {
    const els = document.querySelectorAll('.reveal');
    if (!('IntersectionObserver' in window)) { els.forEach(el => el.classList.add('in')); return; }
    const io = new IntersectionObserver((entries) => {
      entries.forEach(en => { if (en.isIntersecting) { en.target.classList.add('in'); io.unobserve(en.target); } });
    }, { threshold: 0.08 });
    els.forEach(el => io.observe(el));
  }

  /* ---------- 任务控制台 ----------
     蓝图图版：阶段管线 + 尺寸线进度 + SSE 流式日志 + 轮询状态。
     stages: [{ label, en, match: [关键词...] }]，按日志/状态消息关键词推进。 */
  class TaskConsole {
    constructor(root, { stages, fig = 'FIG. 02', title = '生成控制台', onDone, onError, onCancel }) {
      this.root = root;
      this.stages = stages;
      this.onDone = onDone || (() => {});
      this.onError = onError || (() => {});
      this.onCancel = onCancel || (() => {});
      this.taskId = null;
      this.es = null;
      this.pollTimer = null;
      this.paused = false;
      this.pendingLines = [];
      this.pendingFeed = '';
      this._feedChars = 0;
      this.stageIdx = -1;
      this.running = false;
      this.lastStatus = null;
      this._build(fig, title);
    }

    _build(fig, title) {
      this.root.innerHTML = `
        <div class="plate">
          <div class="plate-cap">
            <span class="fig">${escapeHtml(fig)}</span>
            <span class="ttl">${escapeHtml(title)}</span>
            <span class="meta"><span data-el="clock">STANDBY</span></span>
          </div>
          <div class="plate-body grid-bed bp">
            <div class="console">
              <div class="console-left">
                <div>
                  <div class="console-pct"><span data-el="pct">0</span><span class="sign">%</span></div>
                  <div class="console-file" data-el="file">等待任务 · AWAITING JOB</div>
                </div>
                <div class="stages" data-el="stages"></div>
                <div class="progress-dim">
                  <div class="track">
                    <div class="ticks">${'<i></i>'.repeat(11)}</div>
                    <div class="fill" data-el="fill"></div>
                    <div class="head" data-el="head"></div>
                  </div>
                  <div class="scale"><span>0</span><span>25</span><span>50</span><span>75</span><span>100</span></div>
                </div>
                <div data-el="stampWrap"></div>
              </div>
              <div class="console-right">
                <div class="console-bar">
                  <span class="ttl">推理日志 / EVENT LOG</span>
                  <span class="live" data-el="live" style="display:none"><i></i>LIVE</span>
                  <span class="sp">
                    <button class="btn-draft btn-draft--sm" data-el="pause">暂停</button>
                    <button class="btn-draft btn-draft--sm" data-el="clear">清空</button>
                  </span>
                </div>
                <div class="console-log bp-scroll" data-el="log"><span class="ln ln--dim">— 日志将在此处实时绘制 —</span></div>
                <div class="feed" data-el="feedWrap">
                  <div class="feed-cap">
                    <span class="live"><i></i><span data-el="feedState">LIVE OUTPUT</span></span>
                    <span>模型实时输出</span>
                    <span class="chars"><span data-el="feedChars">0</span> CHARS</span>
                  </div>
                  <div class="feed-body bp-scroll" data-el="feed"><span data-el="feedText"></span><span class="cursor"></span></div>
                </div>
                <div class="console-foot">
                  <span class="msg" data-el="msg">就绪</span>
                  <button class="btn-draft btn-draft--orange btn-draft--sm" data-el="cancel" disabled>中断任务</button>
                </div>
              </div>
            </div>
          </div>
        </div>`;
      this.el = {};
      this.root.querySelectorAll('[data-el]').forEach(n => { this.el[n.dataset.el] = n; });
      this._renderStages();
      this.el.pause.addEventListener('click', () => this.togglePause());
      this.el.clear.addEventListener('click', () => this.clearLog());
      this.el.cancel.addEventListener('click', () => this.cancel());
    }

    _renderStages() {
      this.el.stages.innerHTML = this.stages.map((s, i) => `
        <div class="stage" data-stage="${i}">
          <span class="mk"></span>
          <span class="tx">${escapeHtml(s.label)}<small>${escapeHtml(s.en || '')}</small></span>
        </div>`).join('');
    }

    _setStage(idx) {
      if (idx === this.stageIdx) return;
      this.stageIdx = idx;
      this.el.stages.querySelectorAll('.stage').forEach((n, i) => {
        n.classList.toggle('done', i < idx);
        n.classList.toggle('active', i === idx);
      });
    }

    _advanceStages(text, trusted) {
      if (!text) return;
      /* 仅权威状态（轮询消息）或带 [标签] 的日志行可推进阶段；
         原始 LLM 流式 token 中可能包含关键词，不得驱动阶段。 */
      if (!trusted && !/^\[[^\]"'\s]{1,12}\]/.test(text)) return;
      for (let i = this.stages.length - 1; i >= 0; i--) {
        const kws = this.stages[i].match || [];
        if (kws.some(k => text.includes(k))) {
          if (i > this.stageIdx) this._setStage(i);
          return;
        }
      }
    }

    _appendLine(text, cls) {
      if (this.paused) { this.pendingLines.push([text, cls]); return; }
      const log = this.el.log;
      const placeholder = log.querySelector('.ln--dim');
      if (placeholder && placeholder.textContent.startsWith('—')) placeholder.remove();
      const nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 60;
      const span = document.createElement('span');
      span.className = 'ln' + (cls ? ' ' + cls : '');
      span.textContent = text;
      log.appendChild(span);
      while (log.children.length > 900) log.firstChild.remove();
      if (nearBottom) log.scrollTop = log.scrollHeight;
    }

    /* 模型流式输出：追加到连续流动文本区，不逐段换行 */
    _feed(text) {
      if (this.paused) { this.pendingFeed += text; return; }
      this.el.feedWrap.classList.add('show');
      this.el.feedWrap.classList.remove('closed');
      this.el.feedState.textContent = 'LIVE OUTPUT';
      const body = this.el.feed;
      const nearBottom = body.scrollHeight - body.scrollTop - body.clientHeight < 60;
      const span = this.el.feedText;
      span.textContent += text;
      this._feedChars += text.length;
      /* 超长输出裁掉头部，保留最近的流动内容 */
      if (span.textContent.length > 20000) span.textContent = span.textContent.slice(-16000);
      this.el.feedChars.textContent = this._feedChars;
      if (nearBottom) body.scrollTop = body.scrollHeight;
    }

    _resetFeed() {
      this.pendingFeed = '';
      this._feedChars = 0;
      this.el.feedText.textContent = '';
      this.el.feedChars.textContent = '0';
      this.el.feedWrap.classList.remove('show', 'closed');
      this.el.feedState.textContent = 'LIVE OUTPUT';
    }

    _classify(text) {
      if (/\[错误\]|\[ERROR\]|失败|error/i.test(text)) return 'ln--err';
      if (/\[步骤|\[处理文件|\[任务完成\]|\[批量生成\]/.test(text)) return 'ln--step';
      if (/完成|✓|成功/.test(text)) return 'ln--ok';
      if (/^={3,}$|^-{3,}$/.test(text.trim())) return 'ln--dim';
      return '';
    }

    togglePause() {
      this.paused = !this.paused;
      this.el.pause.textContent = this.paused ? '继续' : '暂停';
      this.el.pause.classList.toggle('on', this.paused);
      if (!this.paused) {
        const buf = this.pendingLines; this.pendingLines = [];
        buf.forEach(([t, c]) => this._appendLine(t, c));
        if (this.pendingFeed) { const f = this.pendingFeed; this.pendingFeed = ''; this._feed(f); }
      }
    }

    clearLog() {
      this.el.log.innerHTML = '<span class="ln ln--dim">— 日志已清空 —</span>';
      this.pendingLines = [];
      this._resetFeed();
    }

    setProgress(pct, message) {
      const p = Math.max(0, Math.min(100, Math.round(pct || 0)));
      this.el.pct.textContent = p;
      this.el.fill.style.width = p + '%';
      this.el.head.style.left = p + '%';
      if (message) {
        this.el.msg.textContent = message;
        this._advanceStages(message, true);
      }
    }

    async start(taskId, { fileLabel = '' } = {}) {
      this.stopStream();
      this.taskId = taskId;
      this.running = true;
      this.stageIdx = -1;
      this._renderStages();
      this._resetFeed();
      this.el.stampWrap.innerHTML = '';
      this.el.cancel.disabled = false;
      this.el.live.style.display = '';
      this.el.clock.textContent = 'RUNNING';
      this.el.file.textContent = fileLabel || ('JOB ' + taskId);
      this.setProgress(0, '任务已启动…');
      this._appendLine(`[连接] 任务 ${taskId} 已建立，开始接收实时输出`, 'ln--step');

      /* SSE 流 */
      try {
        this.es = new EventSource(`/api/stream_logs/${encodeURIComponent(taskId)}`);
        this.es.onmessage = (ev) => {
          let msg; try { msg = JSON.parse(ev.data); } catch (e) { return; }
          if (msg.type === 'heartbeat') return;
          const text = msg.message || '';
          if (!text) return;
          /* stream = 原始 LLM token → 连续流动文本区；log/error = 结构化日志行 */
          if (msg.type === 'stream') { this._feed(text); return; }
          this._appendLine(text, this._classify(text));
          this._advanceStages(text);
        };
        this.es.onerror = () => { /* 连接中断时静默，轮询兜底 */ };
      } catch (e) { /* SSE 不可用时仅轮询 */ }

      /* 状态轮询 */
      const poll = async () => {
        if (!this.running || this.taskId !== taskId) return;
        try {
          const st = await api(`/api/task_status/${encodeURIComponent(taskId)}`);
          this.lastStatus = st;
          if (this.taskId !== taskId) return;
          this.setProgress(st.progress, st.message);
          if (st.message) this.el.file.textContent = st.message;
          if (st.status === 'completed') {
            this._finish('ok', st);
          } else if (st.status === 'error') {
            this._finish('err', st);
          } else if (st.status === 'cancelled') {
            this._finish('cancel', st);
          } else if (st.status === 'not_found') {
            this._finish('err', { message: '任务不存在或已过期' });
          }
        } catch (e) { /* 网络抖动，下轮重试 */ }
      };
      this.pollTimer = setInterval(poll, 1000);
      poll();
    }

    _finish(kind, st) {
      this.running = false;
      this.stopStream();
      this.el.cancel.disabled = true;
      this.el.live.style.display = 'none';
      if (this.el.feedWrap.classList.contains('show')) {
        this.el.feedWrap.classList.add('closed');
        this.el.feedState.textContent = 'STREAM CLOSED';
      }
      if (kind === 'ok') {
        this.setProgress(100, st.message || '完成');
        this._setStage(this.stages.length);
        this.el.clock.textContent = 'COMPLETE';
        this.el.stampWrap.innerHTML = '<span class="stamp stamp--ok">✓ DRAWING COMPLETE</span>';
        this._appendLine('[任务完成] ' + (st.message || ''), 'ln--step');
        this.onDone(st);
      } else if (kind === 'cancel') {
        this.el.clock.textContent = 'CANCELLED';
        this.el.stampWrap.innerHTML = '<span class="stamp stamp--violet">■ 任务已中断 CANCELLED</span>';
        this._appendLine('[中断] 任务已被用户取消', 'ln--err');
        this.onCancel(st);
      } else {
        this.el.clock.textContent = 'ERROR';
        this.el.stampWrap.innerHTML = '<span class="stamp stamp--err">✕ 生成失败 ERROR</span>';
        this._appendLine('[错误] ' + (st.message || '未知错误'), 'ln--err');
        this.onError(st);
      }
    }

    stopStream() {
      if (this.es) { this.es.close(); this.es = null; }
      if (this.pollTimer) { clearInterval(this.pollTimer); this.pollTimer = null; }
    }

    async cancel() {
      if (!this.taskId || !this.running) return;
      this.el.cancel.disabled = true;
      try {
        await api(`/api/cancel_task/${encodeURIComponent(this.taskId)}`, { method: 'POST' });
        this._appendLine('[中断] 已发送取消信号…', 'ln--err');
      } catch (e) {
        toast('取消失败：' + e.message, 'err');
        this.el.cancel.disabled = false;
      }
    }

    destroy() { this.stopStream(); }
  }

  /* ---------- 导航当前分区高亮 ---------- */
  function initNavSpy(links) {
    const map = new Map();
    links.forEach(a => {
      const id = a.getAttribute('href').slice(1);
      const sec = document.getElementById(id);
      if (sec) map.set(sec, a);
    });
    if (!map.size) return;
    const io = new IntersectionObserver((entries) => {
      entries.forEach(en => {
        if (en.isIntersecting) {
          links.forEach(a => a.classList.remove('active'));
          const a = map.get(en.target);
          if (a) a.classList.add('active');
        }
      });
    }, { rootMargin: '-40% 0px -55% 0px' });
    map.forEach((a, sec) => io.observe(sec));
  }

  return { api, escapeHtml, fmtSize, pad2, download, toast, confirm: confirmDialog, bindStepper, initReveal, initNavSpy, TaskConsole };
})();