/* ============================================================
   GRAPHSUM blueprint.js — 工程纸风格图谱渲染器（vis-network 封装）
   浅色制图纸底 + 点阵网格；白底节点卡片、深墨文字、类别色条；
   灰线箭头、工程蓝选中。蝶形/层级/力导向布局。
   查看器与编辑器共用。
   ============================================================ */
'use strict';

window.BlueprintGraph = (function () {

  /* 层级色板（浅色底高对比，与图例一致） */
  const LEVEL_COLORS = {
    1: '#0055ff',   /* 根节点 · 工程蓝 */
    2: '#e11d48',   /* 核心话题 · 玫红 */
    3: '#059669',   /* 子话题 · 翡翠绿 */
    4: '#d97706'    /* 细节 · 琥珀 */
  };
  const LEVEL_NAMES = { 1: 'L1 根节点', 2: 'L2 核心话题', 3: 'L3 子话题', 4: 'L4 细节' };
  const LEVEL_CODES = { 1: 'ROOT', 2: 'CORE', 3: 'SUB', 4: 'DETAIL' };
  const UNKNOWN_COLOR = '#7c3aed';

  const ENG = '#0055ff';        /* 工程蓝 · 选中/高亮 */
  const INK = '#0f172a';        /* 深墨 · 节点标题 */
  const SLATE = '#64748b';      /* 次要标注 */
  const BORDER = '#94a3b8';     /* 卡片描边 */
  const EDGE = '#94a3b8';       /* 连线 */

  function levelColor(level) { return LEVEL_COLORS[level] || UNKNOWN_COLOR; }

  /* ---------- 节点卡片度量（离屏 canvas 测宽 + 缓存） ---------- */
  const measurer = document.createElement('canvas').getContext('2d');
  const TITLE_FONT = "700 13px 'Inter', 'Microsoft YaHei', sans-serif";
  const CODE_FONT = "400 9px 'JetBrains Mono', Consolas, monospace";
  const MAX_TEXT_W = 180;
  const MAX_LINES = 3;
  const metricsCache = new Map();

  function wrapText(text) {
    const lines = [];
    let line = '';
    for (const ch of String(text)) {
      if (ch === '\n') { lines.push(line); line = ''; continue; }
      const test = line + ch;
      if (line && measurer.measureText(test).width > MAX_TEXT_W) { lines.push(line); line = ch; }
      else line = test;
    }
    if (line) lines.push(line);
    if (lines.length > MAX_LINES) {
      lines.length = MAX_LINES;
      let last = lines[MAX_LINES - 1];
      while (last && measurer.measureText(last + '…').width > MAX_TEXT_W) last = last.slice(0, -1);
      lines[MAX_LINES - 1] = last + '…';
    }
    return lines;
  }

  function cardMetrics(card) {
    const key = card.label + '|' + card.level + '|' + card.visible;
    const hit = metricsCache.get(key);
    if (hit) return hit;
    const code = 'L' + (card.level || '?') + ' // ' + (LEVEL_CODES[card.level] || 'NODE');
    measurer.font = CODE_FONT;
    let textW = measurer.measureText(code).width;
    let lines = [];
    if (card.visible) {
      measurer.font = TITLE_FONT;
      lines = wrapText(card.label);
      lines.forEach(l => { textW = Math.max(textW, measurer.measureText(l).width); });
    }
    const w = Math.min(224, Math.max(118, Math.ceil(textW) + 28));
    const h = card.visible ? 48 + (lines.length - 1) * 16 : 28;
    const m = { code, lines, w, h };
    if (metricsCache.size > 800) metricsCache.clear();
    metricsCache.set(key, m);
    return m;
  }

  /* 节点：白底技术卡片（左侧类别色条 + 等宽编号 + 深墨标题）
     vis 的 custom 形状仅在构造时绑定 ctxRenderer，故渲染时按 id 实时读取
     DataSet 中的 _card（label/level/visible），更新只需替换 _card + redraw。 */
  function nodeStyle(n, labelsVisible, liveCard) {
    const label = n.label || n.id;
    const card = { label, level: n.level, visible: labelsVisible !== false };
    return {
      id: n.id,
      label,
      _origLabel: label,
      _card: card,
      level: n.level,
      category: n.category || '',
      x: n.x, y: n.y,
      shape: 'custom',
      ctxRenderer: ({ ctx, x, y, state }) => {
        const cd = (liveCard && liveCard(n.id)) || card;
        const m = cardMetrics(cd);
        const c = levelColor(cd.level);
        const isRoot = cd.level === 1;
        const sel = state.selected, hov = state.hover;
        return {
          drawNode() {
            const left = x - m.w / 2, top = y - m.h / 2;
            /* 卡片底 */
            ctx.fillStyle = isRoot ? '#eff6ff' : '#ffffff';
            ctx.strokeStyle = sel ? ENG : hov ? SLATE : (isRoot ? ENG : BORDER);
            ctx.lineWidth = sel ? 2.2 : hov ? 1.5 : (isRoot ? 1.6 : 1.2);
            ctx.beginPath();
            ctx.rect(left, top, m.w, m.h);
            ctx.fill();
            ctx.stroke();
            /* 类别色条 */
            ctx.fillStyle = c;
            ctx.fillRect(left + ctx.lineWidth / 2, top + ctx.lineWidth / 2, 5, m.h - ctx.lineWidth);
            /* 编号行 */
            ctx.font = CODE_FONT;
            ctx.fillStyle = SLATE;
            ctx.textAlign = 'left';
            ctx.textBaseline = 'alphabetic';
            ctx.fillText(m.code, left + 14, top + 17);
            /* 标题行 */
            if (m.lines.length) {
              ctx.font = TITLE_FONT;
              ctx.fillStyle = INK;
              m.lines.forEach((ln, i) => ctx.fillText(ln, left + 14, top + 35 + i * 16));
            }
          },
          drawExternalLabel() {},
          nodeDimensions: { width: m.w, height: m.h }
        };
      },
      shadow: false
    };
  }

  function edgeStyle(e, i) {
    return {
      id: e.id != null ? e.id : 'e_' + Date.now().toString(36) + '_' + i + '_' + Math.random().toString(36).slice(2, 7),
      from: e.source,
      to: e.target,
      label: '',
      arrows: { to: { enabled: true, scaleFactor: 0.55, type: 'arrow' } },
      color: { color: EDGE, highlight: ENG, hover: SLATE, opacity: 1 },
      width: 1.2,
      selectionWidth: 1.8,
      smooth: { enabled: false },
      font: { face: "'JetBrains Mono', Consolas, monospace", size: 9, color: SLATE, strokeWidth: 0 },
      shadow: false
    };
  }

  /* ---------- 布局算法 ---------- */

  /* 蝶形（放射树）布局 */
  function butterflyLayout(nodeArray, edgeArray) {
    const positions = {};
    const nodeMap = {};
    nodeArray.forEach(n => { nodeMap[n.id] = n; });

    const children = {};
    edgeArray.forEach(e => {
      const from = nodeMap[e.from], to = nodeMap[e.to];
      if (from && to && from.level < to.level) {
        (children[e.from] = children[e.from] || []).push(e.to);
      }
    });

    const root = nodeArray.find(n => n.level === 1);
    if (!root) return positions;

    const leafCache = {};
    function countLeaves(id) {
      if (leafCache[id] != null) return leafCache[id];
      const kids = children[id] || [];
      const v = kids.length === 0 ? 1 : kids.reduce((s, k) => s + countLeaves(k), 0);
      leafCache[id] = v;
      return v;
    }
    nodeArray.forEach(n => countLeaves(n.id));

    const scaleFactor = Math.max(1, Math.sqrt(nodeArray.length / 40));
    /* 绝对半径环：L2/L3/L4 各居一环，避免逐边累加导致外环过远 */
    const ringR = { 2: 190 * scaleFactor, 3: 350 * scaleFactor, 4: 500 * scaleFactor };
    const ringFor = lv => ringR[lv] || (500 + (lv - 4) * 150) * scaleFactor;

    function layout(id, centerAngle, angleSpan, level) {
      if (!nodeMap[id]) return;
      if (level === 1) positions[id] = { x: 0, y: 0 };
      const kids = children[id] || [];
      if (!kids.length) return;
      const totalLeaves = kids.reduce((s, k) => s + countLeaves(k), 0);
      const effectiveSpan = Math.max(angleSpan, kids.length * 0.15);
      let cur = centerAngle - effectiveSpan / 2;
      kids.forEach(kid => {
        const span = effectiveSpan * (countLeaves(kid) / totalLeaves);
        const mid = cur + span / 2;
        const r = ringFor(level + 1);
        positions[kid] = { x: r * Math.cos(mid), y: r * Math.sin(mid) };
        layout(kid, mid, span, level + 1);
        cur += span;
      });
    }
    layout(root.id, -Math.PI / 2, Math.PI * 2, 1);
    return positions;
  }

  /* 层级（自上而下）布局 */
  function hierarchicalLayout(nodeArray, edgeArray) {
    const positions = {};
    const byLevel = {};
    nodeArray.forEach(n => { (byLevel[n.level] = byLevel[n.level] || []).push(n); });
    const f = Math.max(1, Math.sqrt(nodeArray.length / 50));
    const hSpacing = Math.max(150, 170 * f);
    const vSpacing = Math.max(130, 150 * f);
    Object.keys(byLevel).forEach(lv => {
      const arr = byLevel[lv];
      const width = arr.length * hSpacing;
      arr.forEach((n, i) => {
        positions[n.id] = { x: -width / 2 + i * hSpacing + hSpacing / 2, y: (parseInt(lv, 10) - 1) * vSpacing };
      });
    });
    return positions;
  }

  /* ---------- 渲染器 ---------- */
  class BlueprintGraph {
    constructor(container, { editor = false, labelsVisible = true } = {}) {
      this.container = container;
      this.editor = editor;
      this.labelsVisible = labelsVisible;
      this.nodes = new vis.DataSet([]);
      this.edges = new vis.DataSet([]);
      this.undoStack = [];
      this.redoStack = [];
      this.layoutMode = 'butterfly';
      this._forceTimer = null;
      this._liveCard = (id) => { const n = this.nodes.get(id); return n && n._card; };

      this.network = new vis.Network(container, { nodes: this.nodes, edges: this.edges }, {
        nodes: { shape: 'custom', shadow: false },
        edges: { smooth: { enabled: false }, shadow: false },
        physics: { enabled: false },
        layout: { improvedLayout: false },
        interaction: {
          hover: true,
          multiselect: true,
          dragNodes: editor,
          dragView: true,
          zoomView: false,
          navigationButtons: false,
          keyboard: false,
          tooltipDelay: 200,
          selectConnectedEdges: true
        },
        manipulation: { enabled: false }
      });

      /* 字体就绪后重测卡片宽度，避免回退字体导致的裁切 */
      if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(() => { metricsCache.clear(); this.network.redraw(); });
      }

      /* 滚轮：以鼠标为中心缩放（vis 默认 zoomView 已关闭，避免双重缩放） */
      container.addEventListener('wheel', e => {
        e.preventDefault();
        const rect = container.getBoundingClientRect();
        let dy = e.deltaY;
        if (e.deltaMode === 1) dy *= 33; else if (e.deltaMode === 2) dy *= 120;
        const factor = Math.max(0.5, Math.min(2, Math.exp(-dy * 0.00125)));
        this.zoomAtPointer({ x: e.clientX - rect.left, y: e.clientY - rect.top }, factor);
      }, { passive: false });

      /* 右键拖拽：平移画布（左键保留给节点拖拽 / 框选 / vis 默认平移） */
      let pan = null;
      container.addEventListener('pointerdown', e => {
        if (e.button !== 2) return;
        this.cancelViewAnim();
        pan = { x: e.clientX, y: e.clientY, pos: this.network.getViewPosition() };
        container.setPointerCapture(e.pointerId);
        container.style.cursor = 'grabbing';
        e.preventDefault();
      });
      container.addEventListener('pointermove', e => {
        if (!pan) return;
        const s = this.network.getScale();
        this.network.moveTo({
          position: { x: pan.pos.x - (e.clientX - pan.x) / s, y: pan.pos.y - (e.clientY - pan.y) / s },
          animation: false
        });
      });
      const endPan = e => {
        if (!pan) return;
        if (e.type === 'pointercancel' || e.button === 2) { pan = null; container.style.cursor = ''; }
      };
      container.addEventListener('pointerup', endPan);
      container.addEventListener('pointercancel', endPan);
      container.addEventListener('contextmenu', e => e.preventDefault());
    }

    /* 自驱动视图动画：vis 内置动画仅在 redraw 时推进 easing，hover 开启后
       每次 mousemove 都会把未收敛的动画推进一格（整图抽动）。改用 rAF 主动
       插值 + 瞬时 moveTo，保证动画必然收敛，hover redraw 不再影响视图。 */
    animateViewTo(target, duration = 400) {
      this.cancelViewAnim();
      const net = this.network;
      const fromPos = net.getViewPosition(), fromScale = net.getScale();
      const toPos = target.position || fromPos;
      const toScale = target.scale != null ? target.scale : fromScale;
      if (!duration) {
        net.moveTo({ position: toPos, scale: toScale, animation: false });
        net.body.emitter.emit('zoom', { scale: toScale });
        return;
      }
      const t0 = performance.now();
      const ease = t => t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
      const step = now => {
        const t = Math.min(1, (now - t0) / duration);
        const k = ease(t);
        net.moveTo({
          position: { x: fromPos.x + (toPos.x - fromPos.x) * k, y: fromPos.y + (toPos.y - fromPos.y) * k },
          scale: fromScale + (toScale - fromScale) * k,
          animation: false
        });
        net.body.emitter.emit('zoom', { scale: net.getScale() });
        if (t < 1) this._viewAnim = requestAnimationFrame(step);
        else { this._viewAnim = null; net.body.emitter.emit('animationFinished', {}); }
      };
      this._viewAnim = requestAnimationFrame(step);
    }

    cancelViewAnim() {
      if (this._viewAnim) { cancelAnimationFrame(this._viewAnim); this._viewAnim = null; }
    }

    /* 以鼠标指针为锚点缩放：保持指针下的画布坐标不动 */
    zoomAtPointer(p, factor) {
      this.cancelViewAnim();
      const net = this.network;
      const scale = Math.max(0.2, Math.min(4, net.getScale() * factor));
      const c = net.DOMtoCanvas(p);
      const cw = this.container.clientWidth || 1, ch = this.container.clientHeight || 1;
      net.moveTo({
        position: { x: c.x - (p.x - cw / 2) / scale, y: c.y - (p.y - ch / 2) / scale },
        scale,
        animation: false
      });
      net.body.emitter.emit('zoom', { scale });
    }

    on(event, cb) { this.network.on(event, cb); }

    /* 载入图谱数据 {nodes, edges} */
    load(graph, { keepPositions = true } = {}) {
      const ns = (graph.nodes || []).map(n => nodeStyle({
        id: n.id, label: n.label, level: n.level, category: n.category,
        x: keepPositions ? n.x : undefined, y: keepPositions ? n.y : undefined
      }, this.labelsVisible, this._liveCard));
      const es = (graph.edges || []).map((e, i) => edgeStyle(e, i));
      this.nodes.clear(); this.edges.clear();
      this.nodes.add(ns); this.edges.add(es);
      this.undoStack = []; this.redoStack = [];
      const hasPos = ns.some(n => n.x != null && n.y != null);
      if (!hasPos) this.applyLayout(this.layoutMode, { fit: true });
      else this.fit();
    }

    /* 导出为后端存储格式 */
    toGraphData() {
      return {
        nodes: this.nodes.get().map(n => ({
          id: n.id, label: n._origLabel || n.label, level: n.level, category: n.category || '',
          x: Math.round(n.x || 0), y: Math.round(n.y || 0)
        })),
        edges: this.edges.get().map(e => ({
          source: e.from, target: e.to, type: '层级', label: e.label || '次级'
        }))
      };
    }

    applyLayout(mode, { fit = true } = {}) {
      this.layoutMode = mode;
      if (this._forceTimer) { clearTimeout(this._forceTimer); this._forceTimer = null; }
      if (mode === 'force') {
        this.network.setOptions({
          physics: {
            enabled: true, solver: 'forceAtlas2Based',
            forceAtlas2Based: { gravitationalConstant: -60, springLength: 150, springConstant: 0.06 },
            stabilization: { iterations: 160 }
          }
        });
        this._forceTimer = setTimeout(() => {
          this.network.setOptions({ physics: { enabled: false } });
          if (fit) this.fit();
        }, 2200);
        return;
      }
      this.network.setOptions({ physics: { enabled: false } });
      const ns = this.nodes.get(), es = this.edges.get();
      const pos = mode === 'hierarchical' ? hierarchicalLayout(ns, es) : butterflyLayout(ns, es);
      const updates = ns.filter(n => pos[n.id]).map(n => ({ id: n.id, x: pos[n.id].x, y: pos[n.id].y }));
      if (updates.length) this.nodes.update(updates);
      if (fit) this.fit();
    }

    /* 自适应取景：保证缩放不低于可读下限（标签不糊成小点） */
    fit() {
      const ids = this.nodes.getIds();
      if (!ids.length) { this.network.fit({ animation: false }); return; }
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      /* shape:'custom' 时 vis 的 getBoundingBox 返回 0，改用坐标 + 卡片尺寸计算 */
      const positions = this.network.getPositions(ids);
      const nodeMap = new Map(this.nodes.get().map(n => [n.id, n]));
      ids.forEach(id => {
        const p = positions[id]; const n = nodeMap.get(id);
        if (!p || !n) return;
        const m = cardMetrics({ label: n._origLabel || n.label || n.id, level: n.level, visible: this.labelsVisible !== false });
        minX = Math.min(minX, p.x - m.w / 2); maxX = Math.max(maxX, p.x + m.w / 2);
        minY = Math.min(minY, p.y - m.h / 2); maxY = Math.max(maxY, p.y + m.h / 2);
      });
      if (!isFinite(minX)) return;
      const pad = 70;
      const cw = this.container.clientWidth || 800, ch = this.container.clientHeight || 600;
      let scale = Math.min(cw / (maxX - minX + pad * 2), ch / (maxY - minY + pad * 2));
      scale = Math.max(0.55, Math.min(scale, 1.3));
      this.animateViewTo({
        position: { x: (minX + maxX) / 2, y: (minY + maxY) / 2 },
        scale
      }, 400);
    }
    zoomBy(f) { this.animateViewTo({ scale: this.network.getScale() * f }, 200); }
    getScale() { return this.network.getScale(); }

    setLabels(visible) {
      this.labelsVisible = visible;
      /* 替换共享卡片对象（快照安全），渲染闭包按 id 实时读取 */
      this.nodes.update(this.nodes.get().map(n => ({
        id: n.id,
        _card: { label: (n._card && n._card.label) || n._origLabel || n.label, level: n.level, visible }
      })));
      this.network.redraw();
    }

    /* ---------- 编辑操作 ---------- */
    snapshot() {
      /* 深拷贝卡片状态，避免后续 update 污染历史快照 */
      this.undoStack.push(this._copyState());
      if (this.undoStack.length > 60) this.undoStack.shift();
      this.redoStack = [];
    }

    _restore(state) {
      this.nodes.clear(); this.edges.clear();
      this.nodes.add(state.nodes); this.edges.add(state.edges);
    }

    _copyState() {
      return {
        nodes: this.nodes.get().map(n => Object.assign({}, n, { _card: n._card ? Object.assign({}, n._card) : undefined })),
        edges: this.edges.get().map(e => Object.assign({}, e))
      };
    }

    undo() {
      if (!this.undoStack.length) return false;
      this.redoStack.push(this._copyState());
      this._restore(this.undoStack.pop());
      return true;
    }

    redo() {
      if (!this.redoStack.length) return false;
      this.undoStack.push(this._copyState());
      this._restore(this.redoStack.pop());
      return true;
    }

    addNodeAt(x, y, level = 2) {
      const id = 'n_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
      this.snapshot();
      this.nodes.add(nodeStyle({ id, label: '新节点', level, category: '', x, y }, this.labelsVisible, this._liveCard));
      return id;
    }

    updateNode(id, { label, level, category }) {
      this.snapshot();
      const n = this.nodes.get(id);
      const visible = n && n._card ? n._card.visible : this.labelsVisible !== false;
      this.nodes.update({ id, label, _origLabel: label, level, category, _card: { label, level, visible } });
      this.network.redraw();
    }

    /* 连线：校验层级方向（父.level < 子.level）；replaceParent 时替换已有父连线 */
    addEdge(fromId, toId, { replaceParent = false } = {}) {
      const a = this.nodes.get(fromId), b = this.nodes.get(toId);
      if (!a || !b || fromId === toId) return { ok: false, reason: '无效节点' };
      if ((a.level || 0) >= (b.level || 0)) return { ok: false, reason: '方向须为上层 → 下层（L1→L2→L3→L4）' };
      const dup = this.edges.get().some(e => e.from === fromId && e.to === toId);
      if (dup) return { ok: false, reason: '连线已存在' };
      const oldParentEdge = this.edges.get().find(e => e.to === toId);
      if (oldParentEdge && !replaceParent) return { ok: false, reason: 'has-parent', parentId: oldParentEdge.from, edgeId: oldParentEdge.id };
      this.snapshot();
      if (oldParentEdge) this.edges.remove(oldParentEdge.id);
      this.edges.add(edgeStyle({ source: fromId, target: toId }, this.edges.length));
      return { ok: true, replaced: !!oldParentEdge };
    }

    /* 某节点是否已有父节点 */
    parentOf(id) {
      const e = this.edges.get().find(e => e.to === id);
      return e ? e.from : null;
    }

    removeSelection() {
      const sel = this.network.getSelection();
      if (!sel.nodes.length && !sel.edges.length) return 0;
      this.snapshot();
      /* 删除节点时同步删除其关联边 */
      const edgeIds = new Set(sel.edges);
      this.edges.get().forEach(e => {
        if (sel.nodes.includes(e.from) || sel.nodes.includes(e.to)) edgeIds.add(e.id);
      });
      this.nodes.remove(sel.nodes);
      this.edges.remove(Array.from(edgeIds));
      return sel.nodes.length + edgeIds.size;
    }

    selectNode(id) { this.network.selectNodes([id]); }
    getSelection() { return this.network.getSelection(); }

    counts() { return { nodes: this.nodes.length, edges: this.edges.length }; }

    levelHistogram() {
      const h = { 1: 0, 2: 0, 3: 0, 4: 0 };
      this.nodes.get().forEach(n => { if (h[n.level] != null) h[n.level]++; });
      return h;
    }

    destroy() {
      if (this._forceTimer) clearTimeout(this._forceTimer);
      this.network.destroy();
    }
  }

  return { BlueprintGraph, LEVEL_COLORS, LEVEL_NAMES, levelColor, butterflyLayout, hierarchicalLayout };
})();