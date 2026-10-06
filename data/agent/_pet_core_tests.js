/* ============================================================================
 * 桌宠「演绎动作」引擎自测（不需要浏览器）
 * ----------------------------------------------------------------------------
 * 测的是 pet_core.js 里那套纯逻辑。为什么值得单独测：
 *   · 动作组名每个模型都不一样，挑错了**不会报错**，只是「永远不动」——最难的静默失效；
 *   · 参数演绎（呼吸/眨眼/说话嘴动）是给「白-免费版」这类没有动作文件的模型用的，
 *     写错了同样看不出来，只有盯着模型发呆的人才会发现；
 *   · 事件 → 动作的映射（上课开始→思考、出结果→高兴…）要对得上 ACTS 里的名字，
 *     名字对不上时 pet.act() 会静默什么都不做。
 * 用法：node data/agent/_pet_core_tests.js
 * ========================================================================== */
'use strict';

const fs = require('fs');
const path = require('path');

// pet_core.js 只在运行时用 window/document（模块加载时不碰），给个最小桩就能在 node 里 require
global.window = global.window || {};
global.document = global.document || { createElement: () => ({ style: {}, appendChild() {} }), head: { appendChild() {} } };

const CORE = path.join(__dirname, '..', '..', 'agent_studio', 'web', 'static', 'js', 'pet_core.js');
eval(fs.readFileSync(CORE, 'utf8'));            // eslint-disable-line no-eval
const PetCore = global.window.PetCore;

let fails = 0;
function check(name, ok, detail) {
  if (!ok) fails++;
  console.log((ok ? '  OK    ' : '  FAIL  ') + name + (detail ? '  →  ' + detail : ''));
}

/* ---------- 1) 事件 → 该演哪一段 ---------- */
console.log('--- 事件映射 ---');
const cases = [
  [{ type: 'run.start' }, 'think'],
  [{ type: 'run.end' }, 'speak'],
  [{ type: 'tool.result', data: { ok: true, name: 'graph_calculator' } }, 'happy'],
  [{ type: 'tool.result', data: { ok: false, name: 'x' } }, 'surprise'],
  [{ type: 'quiz', data: { percent: 80 } }, 'happy'],
  [{ type: 'reminder', data: { text: '该复习啦' } }, 'surprise'],
  [{ type: 'alert' }, 'surprise'],
  [{ type: 'notice' }, 'nod'],
  [{ type: 'filed' }, 'nod'],
  [{ type: 'unknown.type' }, null],
  [{}, null],
];
let mapOk = true;
cases.forEach(([ev, want]) => {
  const got = PetCore.actFor(ev);
  if (got !== want) { mapOk = false; check('事件 ' + JSON.stringify(ev).slice(0, 46), false, '期望 ' + want + ' 实际 ' + got); }
});
check('所有事件的映射都对', mapOk, cases.length + ' 种');

/* ---------- 2) 每个映射出来的动作都必须真的存在 ---------- */
console.log('--- 动作表 ---');
const kinds = new Set(cases.map(([, k]) => k).filter(Boolean));
const missing = [...kinds].filter((k) => !PetCore.ACTS[k]);
check('映射出来的动作都在 ACTS 里（否则点了没反应）', missing.length === 0, '缺：' + JSON.stringify(missing));

let shapeOk = true;
Object.keys(PetCore.ACTS).forEach((k) => {
  const a = PetCore.ACTS[k];
  if (!(a.dur > 0) || typeof a.params !== 'function') { shapeOk = false; check('动作 ' + k + ' 结构', false); }
  for (let t = 0; t <= a.dur; t += a.dur / 5) {
    const p = a.params(t) || {};
    Object.keys(p).forEach((id) => {
      const v = p[id];
      if (typeof v !== 'number' || !Number.isFinite(v)) { shapeOk = false; check('参数 ' + k + '.' + id + '@' + t.toFixed(2), false, String(v)); }
      // 幅度上限按 Live2D 参数的常规取值范围定（头部角度 ±30 是正常范围，
      // 超过 30 就是「甩头」了；写这条是为了防止手滑多打一个 0）
      if (Math.abs(v) > 30) { shapeOk = false; check('参数 ' + k + '.' + id + ' 幅度过大', false, String(v)); }
    });
    if (a.body) {
      const b = a.body(t) || {};
      ['x', 'y', 'rot'].forEach((f) => {
        if (b[f] != null && !Number.isFinite(b[f])) { shapeOk = false; check('身体 ' + k + '.' + f, false, String(b[f])); }
      });
    }
  }
});
check('每个动作的参数/身体在全程都是有限数值、幅度合理', shapeOk);

/* ---------- 3) 备用形象的表情要覆盖所有动作 ---------- */
const noSprite = [...kinds].filter((k) => !PetCore.SPRITE_STATE[k]);
check('备用形象也有对应表情（没有 Live2D 时不能僵住）', noSprite.length === 0, '缺：' + JSON.stringify(noSprite));

/* ---------- 4) 动作组挑选（真实模型的动作组，名字各不相同） ---------- */
console.log('--- 动作组挑选 ---');
const realWorld = {
  // 艾琳（自己那份模型）
  'my_teacher': [['hello', 'idle', 'listen', 'speak', 'think'], { idle: 'idle', think: 'think', speak: 'speak', listen: 'listen' }],
  // hiyori 官方免费版：只有 Idle/Flick/Tap 这类名字 —— 反应动作就拿 Tap/Flick 顶上
  'hiyori_free': [['Idle', 'Flick', 'FlickDown', 'Tap', 'Tap@Body', 'Flick@Body'], { idle: 'Idle', tap: 'Tap', happy: 'Tap', surprise: 'FlickDown' }],
  // mao_pro：注意它有一个空字符串的动作组（真实存在，别被它带偏）
  'mao_pro': [['Idle', ''], { idle: 'Idle' }],
  // shizuku
  'shizuku': [['FlickUp', 'Tap', 'Flick3', 'Idle'], { idle: 'Idle', tap: 'Tap' }],
  // 白-免费版：一个动作都没有
  'bai_free': [[], {}],
};
Object.keys(realWorld).forEach((name) => {
  const [groups, want] = realWorld[name];
  Object.keys(want).forEach((kind) => {
    const got = PetCore.pickGroup(groups, kind);
    check(name + ' 的 ' + kind + ' → ' + want[kind], got === want[kind], '实际 ' + JSON.stringify(got));
  });
  if (!groups.length) {
    check(name + ' 没有动作时应返回 null（交给参数演绎）', PetCore.pickGroup(groups, 'idle') === null);
  }
});

/* ---------- 5) 基础待机参数（呼吸/摇摆/眨眼） ---------- */
console.log('--- 基础待机 ---');
const st = { nextBlink: 1.0, blinkAt: -9 };
let finite = true, sawClose = false, sawOpen = false;
let breathMin = 9, breathMax = -9;
const seen = {};
for (let t = 0; t < 12; t += 0.05) {
  const p = PetCore.baseParams(t, st);
  Object.keys(p).forEach((id) => { if (!Number.isFinite(p[id])) finite = false; seen[id] = 1; });
  breathMin = Math.min(breathMin, p.ParamBreath);
  breathMax = Math.max(breathMax, p.ParamBreath);
  if (p.ParamEyeLOpen < 0.5) sawClose = true;
  if (p.ParamEyeLOpen > 0.95) sawOpen = true;
}
check('待机参数都是有限数值', finite);
check('呼吸有起伏（不是一动不动）', breathMax - breathMin > 0.5, breathMin.toFixed(2) + '~' + breathMax.toFixed(2));
check('会眨眼（闭得下去也睁得开）', sawClose && sawOpen, 'closed=' + sawClose + ' open=' + sawOpen);
const need = ['ParamAngleX', 'ParamAngleY', 'ParamAngleZ', 'ParamEyeLOpen', 'ParamEyeROpen', 'ParamBreath', 'ParamBodyAngleX'];
const lack = need.filter((k) => !seen[k]);
check('包含关键的几个参数', lack.length === 0, '缺：' + JSON.stringify(lack) + '；有：' + Object.keys(seen).join(','));

/* ---------- 6) TTS：念出来的文字要先洗一遍 ---------- */
console.log('--- TTS 台词清洗 ---');
const clean = PetCore.cleanForSpeech;
const c1 = clean('**重点**：看 https://example.com/a 里的 `x^2` 和第 3 题 😀');
check('去掉 markdown/链接/emoji', c1.indexOf('**') < 0 && c1.indexOf('http') < 0 &&
  c1.indexOf('`') < 0 && c1.indexOf('😀') < 0, JSON.stringify(c1));
check('保留了知识点本身', c1.indexOf('重点') >= 0 && c1.indexOf('x^2') >= 0, JSON.stringify(c1));
check('超长截断（不能给学生念一整篇）', clean('啊'.repeat(300)).length === 121, String(clean('啊'.repeat(300)).length));
check('代码块变成「代码」不会念一堆符号',
  clean('看这个：\n```js\nconst a=1;\n```\n就这样').indexOf('const') < 0, JSON.stringify(clean('看这个：```js\nconst a=1;\n```\n就这样')));

/* ---------- 7) 什么时候不该念 ---------- */
console.log('--- TTS 开关与静默规则 ---');
const calls = [];
global.fetch = async function (url, init) {
  calls.push({ url: String(url), body: init && init.body });
  return { ok: true, blob: async () => ({ size: 10 }), json: async () => ({}) };
};
global.Audio = function () { return { play: async () => {}, pause() {} }; };
global.URL = global.URL || {}; global.URL.createObjectURL = () => 'blob:x';

(async function () {
  PetCore.setSpeak(false);
  calls.length = 0;
  let r = await PetCore.speak('这句话不该发出去');
  check('关着的时候一句都不发', calls.length === 0 && r === null);

  PetCore.setSpeak(true);
  calls.length = 0;
  r = await PetCore.speak('老师在想了…');
  check('过程提示（想了/正在…）不念', calls.length === 0 && r === null);

  calls.length = 0;
  r = await PetCore.speak('**下课啦**，记得做错题本 https://a.b/c');
  check('正常内容会发一次合成请求', calls.length === 1, JSON.stringify(calls.map((x) => x.url)));
  check('发的是洗过的文字', String(calls[0].body).indexOf('http') < 0 && String(calls[0].body).indexOf('**') < 0,
    String(calls[0].body));

  calls.length = 0;
  await PetCore.speak('   ');
  check('空白内容不发请求', calls.length === 0);

  console.log('\n' + (fails === 0 ? '全部通过 ✓' : '失败 ' + fails + ' 项 ✗'));
  process.exit(fails ? 1 : 0);
})();
