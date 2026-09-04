'use strict';

// ==================================================
// V4 学习仪表盘：加载 /api/v4/dashboard 数据并渲染
// （雷达图 / 进度曲线用原生 Canvas 绘制，无第三方依赖）
// ==================================================

const DASHBOARD_VERSION = '4.0';

function dashEl(id) { return document.getElementById(id); }

function dashLessonFolder() {
    return (typeof currentLesson !== 'undefined' && currentLesson && currentLesson !== 'default')
        ? currentLesson : '';
}

// ---------- Canvas 通用工具 ----------
function dashResizeCanvas(canvas, width, height) {
    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    canvas.style.width = width + 'px';
    canvas.style.height = height + 'px';
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return ctx;
}

function dashColor(i) {
    const palette = ['#ff8fa3', '#ffd166', '#7bdff2', '#a8e6cf', '#b39ddb', '#f6bd60', '#84a98c'];
    return palette[i % palette.length];
}

// ---------- 雷达图：知识掌握度 ----------
function drawRadar(canvas, items) {
    const W = 300, H = 300, cx = W / 2, cy = H / 2, R = 105;
    const ctx = dashResizeCanvas(canvas, W, H);
    ctx.clearRect(0, 0, W, H);
    if (!items || items.length === 0) { dashEl('dash-radar-empty').style.display = 'block'; return; }
    dashEl('dash-radar-empty').style.display = 'none';

    const n = items.length;
    const angle = (i) => -Math.PI / 2 + (2 * Math.PI * i) / n;

    // 背景网格（5 层）
    for (let layer = 1; layer <= 5; layer++) {
        const r = (R * layer) / 5;
        ctx.beginPath();
        for (let i = 0; i <= n; i++) {
            const a = angle(i % n);
            const x = cx + r * Math.cos(a), y = cy + r * Math.sin(a);
            i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
        }
        ctx.strokeStyle = 'rgba(150,150,160,0.18)';
        ctx.lineWidth = 1;
        ctx.stroke();
    }
    // 轴线 + 标签
    ctx.font = '11px "Noto Serif SC", sans-serif';
    ctx.fillStyle = 'rgba(255,255,255,0.78)';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    for (let i = 0; i < n; i++) {
        const a = angle(i);
        const x = cx + (R + 26) * Math.cos(a), y = cy + (R + 26) * Math.sin(a);
        ctx.fillStyle = 'rgba(255,255,255,0.78)';
        ctx.fillText(String(items[i].name || '').slice(0, 6), x, y);
    }
    // 数据多边形
    ctx.beginPath();
    for (let i = 0; i < n; i++) {
        const v = Math.max(0, Math.min(1, items[i].mastery || 0));
        const a = angle(i);
        const x = cx + R * v * Math.cos(a), y = cy + R * v * Math.sin(a);
        i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    }
    ctx.closePath();
    ctx.fillStyle = 'rgba(125,195,255,0.30)';
    ctx.fill();
    ctx.strokeStyle = '#5ab0ff';
    ctx.lineWidth = 2;
    ctx.stroke();
    // 顶点圆点
    for (let i = 0; i < n; i++) {
        const v = Math.max(0, Math.min(1, items[i].mastery || 0));
        const a = angle(i);
        const x = cx + R * v * Math.cos(a), y = cy + R * v * Math.sin(a);
        ctx.beginPath();
        ctx.arc(x, y, 3.5, 0, Math.PI * 2);
        ctx.fillStyle = dashColor(i);
        ctx.fill();
    }
}

// ---------- 折线图：学习进度曲线 ----------
function drawCurve(canvas, scores) {
    const W = 300, H = 180, padL = 30, padR = 10, padT = 12, padB = 24;
    const ctx = dashResizeCanvas(canvas, W, H);
    ctx.clearRect(0, 0, W, H);
    if (!scores || scores.length < 2) { dashEl('dash-curve-empty').style.display = 'block'; return; }
    dashEl('dash-curve-empty').style.display = 'none';

    const pts = scores.map(s => Math.max(0, Math.min(100, s)));
    const minV = Math.floor(Math.min(...pts, 0) / 10) * 10;
    const maxV = Math.ceil(Math.max(...pts, 100) / 10) * 10;
    const range = (maxV - minV) || 1;
    const x = (i) => padL + (i / (pts.length - 1)) * (W - padL - padR);
    const y = (v) => H - padB - ((v - minV) / range) * (H - padT - padB);

    // 网格
    ctx.strokeStyle = 'rgba(150,150,160,0.15)';
    ctx.lineWidth = 1;
    for (let g = 0; g <= 4; g++) {
        const gy = padT + (g / 4) * (H - padT - padB);
        ctx.beginPath(); ctx.moveTo(padL, gy); ctx.lineTo(W - padR, gy); ctx.stroke();
    }
    // Y 轴标签
    ctx.font = '10px sans-serif';
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    for (let g = 0; g <= 4; g++) {
        const val = Math.round(maxV - (g / 4) * range);
        ctx.fillText(String(val), padL - 5, padT + (g / 4) * (H - padT - padB));
    }
    // 折线 + 渐变
    const grad = ctx.createLinearGradient(0, padT, 0, H - padB);
    grad.addColorStop(0, 'rgba(90,176,255,0.35)');
    grad.addColorStop(1, 'rgba(90,176,255,0.02)');
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
        i === 0 ? ctx.moveTo(x(i), y(pts[i])) : ctx.lineTo(x(i), y(pts[i]));
    }
    ctx.lineTo(x(pts.length - 1), H - padB);
    ctx.lineTo(x(0), H - padB);
    ctx.closePath();
    ctx.fillStyle = grad;
    ctx.fill();
    ctx.beginPath();
    for (let i = 0; i < pts.length; i++) {
        i === 0 ? ctx.moveTo(x(i), y(pts[i])) : ctx.lineTo(x(i), y(pts[i]));
    }
    ctx.strokeStyle = '#5ab0ff';
    ctx.lineWidth = 2;
    ctx.stroke();
    // 顶点
    for (let i = 0; i < pts.length; i++) {
        ctx.beginPath();
        ctx.arc(x(i), y(pts[i]), 3, 0, Math.PI * 2);
        ctx.fillStyle = i === pts.length - 1 ? '#ffd166' : '#5ab0ff';
        ctx.fill();
    }
    // X 轴标签（首/中/末）
    ctx.textAlign = 'center';
    ctx.fillStyle = 'rgba(255,255,255,0.55)';
    ctx.fillText('第 1 次', x(0), H - 8);
    if (pts.length > 2) ctx.fillText('…', x(Math.floor(pts.length / 2)), H - 8);
    ctx.fillText(`第 ${pts.length} 次`, x(pts.length - 1), H - 8);
}

// ---------- 列表项渲染 ----------
function dashItem(label, value, color) {
    return '<div class="dash-item"><span class="dash-item-label">' + label + '</span>' +
        '<span class="dash-item-value" style="' + (color ? 'color:' + color + ';' : '') + '">' + value + '</span></div>';
}

function renderDashboard(data) {
    // 元信息
    dashEl('dash-learner-id').textContent = '学生：' + (data.learner_id || '001');
    dashEl('dash-lesson').textContent = data.lesson_folder ? '课程：' + data.lesson_folder.slice(-20) : '未选择课程';

    // 1) 知识掌握度雷达图
    const nodes = (data.knowledge_state || []).filter(n => n && n.mastery !== undefined);
    if (nodes.length) {
        drawRadar(dashEl('dash-radar'), nodes);
    } else {
        dashEl('dash-radar-empty').style.display = 'block';
        dashEl('dash-radar').style.display = 'none';
    }

    // 2) 进度曲线
    const curve = data.progress_curve || {};
    const scores = (curve.score_curve || []).map(p => p.score);
    if (scores.length >= 2) {
        drawCurve(dashEl('dash-curve'), scores);
        dashEl('dash-curve').style.display = 'block';
    } else {
        dashEl('dash-curve-empty').style.display = 'block';
        dashEl('dash-curve').style.display = 'none';
    }

    // 3) 推荐下一步
    const rec = data.recommended;
    const nextEl = dashEl('dash-next');
    if (rec && rec.name) {
        const pct = Math.round((rec.current_mastery || 0) * 100);
        nextEl.innerHTML =
            dashItem('推荐学习', escapeHtml(String(rec.name))) +
            dashItem('当前掌握度', pct + '%', pct < 60 ? '#ff8fa3' : '#a8e6cf') +
            dashItem('预计时长', (rec.estimated_time || 30) + ' 分钟') +
            '<div class="dash-reason">' + escapeHtml(data.recommend_reason || '') + '</div>';
    } else {
        nextEl.innerHTML = '<div class="dash-reason">' + escapeHtml(data.recommend_reason || '暂无推荐') + '</div>';
    }

    // 4) 学习增益统计（异步补充）
    loadGainStats();
    loadInsights();

    // 5) 错题本
    const wrong = data.error_memory || [];
    const wrongEl = dashEl('dash-wrong-book');
    if (wrong.length) {
        wrongEl.innerHTML = wrong.slice(-15).reverse().map(function(w) {
            return '<div class="dash-wrong-item">' +
                '<span class="dash-wrong-concept">' + escapeHtml(w.concept || '') + '</span>' +
                '<span class="dash-wrong-text">' + escapeHtml(String(w.error || '').slice(0, 60)) + '</span></div>';
        }).join('') || '<div class="dash-empty">暂无错题</div>';
    } else {
        wrongEl.innerHTML = '<div class="dash-empty">暂无错题，继续保持！</div>';
    }
}

function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
        return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
}

function loadGainStats() {
    const lid = '001';
    fetch('/api/v4/learner/' + lid + '/gain_report?learner_id=' + lid)
        .then(function(r) { return r.json(); })
        .then(function(report) {
            const el = dashEl('dash-gain');
            if (!el) return;
            if (report.message && report.gain === 0 && report.assessment_count < 2) {
                el.innerHTML = '<div class="dash-empty">' + escapeHtml(report.message) + '</div>';
                return;
            }
            el.innerHTML =
                dashItem('首测分数', report.pre_score != null ? Math.round(report.pre_score) + ' 分' : '—') +
                dashItem('最近分数', report.post_score != null ? Math.round(report.post_score) + ' 分' : '—') +
                dashItem('学习增益', (report.gain >= 0 ? '+' : '') + report.gain + ' 分',
                    report.gain >= 0 ? '#a8e6cf' : '#ff8fa3') +
                dashItem('学习效率', (report.efficiency || 0) + ' 分/小时') +
                dashItem('测验次数', (report.assessment_count || 0) + ' 次');
        })
        .catch(function() {
            const el = dashEl('dash-gain');
            if (el) el.innerHTML = '<div class="dash-empty">增益数据加载失败</div>';
        });
}

function loadInsights() {
    const lid = '001';
    fetch('/api/v4/learner/' + lid + '/insights?learner_id=' + lid)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            const el = dashEl('dash-insights');
            if (!el) return;
            const arr = data.insights || [];
            el.innerHTML = arr.length
                ? arr.map(function(t) { return '<div class="dash-insight-item">💡 ' + escapeHtml(t) + '</div>'; }).join('')
                : '<div class="dash-empty">暂无学习洞察</div>';
        })
        .catch(function() {
            const el = dashEl('dash-insights');
            if (el) el.innerHTML = '<div class="dash-empty">洞察加载失败</div>';
        });
}

function loadDashboard() {
    const folder = dashLessonFolder();
    const lid = '001';
    const url = '/api/v4/dashboard?lesson_folder=' + encodeURIComponent(folder) + '&learner_id=' + lid;
    fetch(url)
        .then(function(r) { return r.json(); })
        .then(function(data) {
            if (data.error) { dashEl('dash-next').innerHTML = '<div class="dash-empty">' + escapeHtml(data.error) + '</div>'; return; }
            renderDashboard(data);
        })
        .catch(function() {
            dashEl('dash-next').innerHTML = '<div class="dash-empty">仪表盘数据加载失败</div>';
        });
}

// 刷新按钮
document.addEventListener('DOMContentLoaded', function() {
    const btn = dashEl('dash-refresh-btn');
    if (btn) btn.addEventListener('click', loadDashboard);
});
