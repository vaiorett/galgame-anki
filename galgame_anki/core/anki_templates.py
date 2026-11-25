"""Refold 三階段「登樓撤梯」習得型 Anki 卡片模板（嚴格沿用 Anki 原生默認日間/夜間主題）。

嚴格還原 galgame.dev/topic/639 方法論與 SuperMemo 最小資訊原則：
- 音畫情景優先（聲優原聲 + 當下遊戲/動畫 CG 截圖直接喚醒劇情記憶）
- Lv.1 五十音與讀音破冰：正面先自己念 → 背面超大平假名讀音 + 極簡 1 行核心意思 + 原聲跟讀
- Lv.2 精細沉浸 1T 句卡：正面純粹原句【焦點詞】→ 背面音畫 + 精煉 1 條本句釋義 + 本句語境理解 + 語序句型骨架
- Lv.3 登樓撤梯單語思維：背面以精煉日日單語釋義為主，雙語與整句理解自動收進安全網折疊區
"""

from __future__ import annotations

NOTE_TYPE_NAME = "GalImmersion-OneClick"

NOTE_FIELDS: list[str] = [
    "TargetWord",
    "TargetReading",
    "PitchAccent",
    "PitchGraph",
    "Sentence",
    "SentenceFurigana",
    "SentenceTranslation",
    "DefinitionBilingual",
    "DefinitionMonolingual",
    "SentenceAudio",
    "Screenshot",
    "AIGrammarNotes",
    "SourceGame",
    "StageMode",
]

# 嚴格繼承 Anki 原生默認底色與文字色（日間/夜間模式自動融合，絕不硬寫突兀色塊）
CARD_CSS = """
.card {
  font-family: "Noto Sans CJK JP", "Hiragino Sans", "Yu Gothic UI", "Meiryo", sans-serif;
  font-size: 18px;
  line-height: 1.75;
  text-align: left;
  padding: 16px 20px;
  max-width: 680px;
  margin: 0 auto;
}

.gal-meta {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  opacity: 0.55;
  margin-bottom: 12px;
}

.sentence-box {
  font-size: 26px;
  line-height: 1.8;
  padding: 6px 2px 8px 2px;
  letter-spacing: 0.02em;
}

.sentence-box .focus-word {
  font-weight: 700;
  text-decoration: underline;
  text-underline-offset: 5px;
  text-decoration-thickness: 2px;
  padding: 0 3px;
  border-radius: 4px;
  background: rgba(127, 127, 127, 0.14);
}

.furigana-hint {
  font-size: 17px;
  opacity: 0.85;
  margin-top: 4px;
}

ruby rt {
  font-size: 0.56em;
  opacity: 0.78;
}

.scene-stage {
  margin: 12px 0;
  text-align: center;
}

.scene-stage img {
  max-width: 100%;
  max-height: 270px;
  border-radius: 10px;
  border: 1px solid rgba(127, 127, 127, 0.22);
}

.audio-row {
  margin: 8px 0 6px 0;
  display: flex;
  align-items: center;
  gap: 10px;
}

.divider {
  border: none;
  border-top: 1px solid rgba(127, 127, 127, 0.22);
  margin: 14px 0;
}

/* Lv.1 破冰超大讀音焦點 */
.lv1-reading-hero {
  font-size: 32px;
  font-weight: 700;
  letter-spacing: 0.04em;
  margin: 4px 0 6px 0;
}

.word-header {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 10px;
}

.target-word {
  font-size: 27px;
  font-weight: 700;
}

.target-reading {
  font-size: 19px;
  opacity: 0.82;
}

.pitch-badge {
  font-size: 13px;
  padding: 1px 7px;
  border-radius: 6px;
  border: 1px solid rgba(127, 127, 127, 0.3);
  opacity: 0.78;
}

/* AJATT 風格高低音調視覺曲線與四型語義色（嚴格相容 Anki 原生日間／夜間主題） */
.pitch-graph-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin: 4px 0 12px 0;
}

.pitch-item {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 8px 2px 4px;
  border-radius: 8px;
  background: rgba(127, 127, 127, 0.06);
  border: 1px solid rgba(127, 127, 127, 0.16);
}

.pitch-svg {
  display: inline-block;
  vertical-align: middle;
}

.pitch-chip-tag {
  font-size: 11.5px;
  font-weight: 700;
  padding: 1px 6px;
  border-radius: 5px;
  border: 1px solid currentColor;
}

.pt-heiban .pt-line, .pt-heiban .pt-dash, .pt-heiban .pt-drop-ring, .pt-heiban .pt-particle { stroke: #2563eb; }
.pt-heiban .pt-dot { fill: #2563eb; }
.pt-tag-heiban { color: #2563eb; background: rgba(37, 99, 235, 0.10); }

.pt-atamadaka .pt-line, .pt-atamadaka .pt-dash, .pt-atamadaka .pt-drop-ring, .pt-atamadaka .pt-particle { stroke: #dc2626; }
.pt-atamadaka .pt-dot { fill: #dc2626; }
.pt-tag-atamadaka { color: #dc2626; background: rgba(220, 38, 38, 0.10); }

.pt-nakadaka .pt-line, .pt-nakadaka .pt-dash, .pt-nakadaka .pt-drop-ring, .pt-nakadaka .pt-particle { stroke: #d97706; }
.pt-nakadaka .pt-dot { fill: #d97706; }
.pt-tag-nakadaka { color: #d97706; background: rgba(217, 119, 6, 0.11); }

.pt-odaka .pt-line, .pt-odaka .pt-dash, .pt-odaka .pt-drop-ring, .pt-odaka .pt-particle { stroke: #059669; }
.pt-odaka .pt-dot { fill: #059669; }
.pt-tag-odaka { color: #059669; background: rgba(5, 150, 105, 0.10); }

.nightMode .pt-heiban .pt-line, .nightMode .pt-heiban .pt-dash, .nightMode .pt-heiban .pt-drop-ring, .nightMode .pt-heiban .pt-particle { stroke: #589cf5; }
.nightMode .pt-heiban .pt-dot { fill: #589cf5; }
.nightMode .pt-tag-heiban { color: #589cf5; background: rgba(88, 156, 245, 0.16); }

.nightMode .pt-atamadaka .pt-line, .nightMode .pt-atamadaka .pt-dash, .nightMode .pt-atamadaka .pt-drop-ring, .nightMode .pt-atamadaka .pt-particle { stroke: #f87171; }
.nightMode .pt-atamadaka .pt-dot { fill: #f87171; }
.nightMode .pt-tag-atamadaka { color: #f87171; background: rgba(248, 113, 113, 0.16); }

.nightMode .pt-nakadaka .pt-line, .nightMode .pt-nakadaka .pt-dash, .nightMode .pt-nakadaka .pt-drop-ring, .nightMode .pt-nakadaka .pt-particle { stroke: #fbbf24; }
.nightMode .pt-nakadaka .pt-dot { fill: #fbbf24; }
.nightMode .pt-tag-nakadaka { color: #fbbf24; background: rgba(251, 191, 36, 0.16); }

.nightMode .pt-odaka .pt-line, .nightMode .pt-odaka .pt-dash, .nightMode .pt-odaka .pt-drop-ring, .nightMode .pt-odaka .pt-particle { stroke: #34d399; }
.nightMode .pt-odaka .pt-dot { fill: #34d399; }
.nightMode .pt-tag-odaka { color: #34d399; background: rgba(52, 211, 153, 0.16); }

.core-meaning {
  padding: 11px 15px;
  border-radius: 9px;
  border-left: 3.5px solid rgba(127, 127, 127, 0.48);
  background: rgba(127, 127, 127, 0.07);
  margin-bottom: 10px;
  white-space: pre-wrap;
  font-size: 19px;
  line-height: 1.65;
}

.meaning-label {
  font-size: 12px;
  font-weight: 600;
  opacity: 0.6;
  margin-bottom: 3px;
}

.context-quote {
  padding: 8px 13px;
  border-radius: 8px;
  border: 1px solid rgba(127, 127, 127, 0.2);
  background: rgba(127, 127, 127, 0.04);
  margin-bottom: 10px;
  font-size: 16px;
  opacity: 0.9;
}

details.fold-box {
  border: 1px solid rgba(127, 127, 127, 0.2);
  background: rgba(127, 127, 127, 0.04);
  border-radius: 8px;
  padding: 8px 12px;
  margin-top: 10px;
  font-size: 15px;
}

details.fold-box summary {
  cursor: pointer;
  font-weight: 600;
  opacity: 0.78;
  outline: none;
}

.grammar-body {
  margin-top: 8px;
  line-height: 1.7;
  font-size: 14.5px;
}

/* 三階段精準分層（登樓撤梯） */
.stage-lv1 .lv23-only { display: none; }
.stage-lv2 .lv1-only, .stage-lv3 .lv1-only { display: none; }
.stage-lv1 .primary-mono, .stage-lv2 .primary-mono { display: none; }
.stage-lv1 .secondary-bi, .stage-lv2 .secondary-bi { display: none; }
.stage-lv3 .primary-bi { display: none; }
.stage-lv3 .secondary-mono { display: none; }
.stage-lv3 .context-primary { display: none; }
.stage-lv1 .context-fold, .stage-lv2 .context-fold { display: none; }
"""

FRONT_TEMPLATE = """
<div class="stage-{{StageMode}}">
  <div class="gal-meta">
    <span>{{#SourceGame}}{{SourceGame}}{{/SourceGame}}</span>
    <span>{{StageMode}}</span>
  </div>

  <div class="sentence-box" id="gal-sentence">{{Sentence}}</div>

  {{#SentenceFurigana}}
  <div class="lv1-only">
    <details class="fold-box">
      <summary>先自己念念看，卡住再展開假名提示（Lv.1）</summary>
      <div class="furigana-hint">{{SentenceFurigana}}</div>
    </details>
  </div>
  {{/SentenceFurigana}}
</div>

<script>
(function () {
  var el = document.getElementById("gal-sentence");
  if (!el) return;
  el.innerHTML = el.innerHTML.replace(/【([^】]+)】/g, '<span class="focus-word">【$1】</span>');
})();
</script>
"""

BACK_TEMPLATE = r"""
<div class="stage-{{StageMode}}">
  <div class="gal-meta">
    <span>{{#SourceGame}}{{SourceGame}}{{/SourceGame}}</span>
    <span>{{StageMode}}</span>
  </div>

  <!-- 原句與精準假名讀音 -->
  <div class="sentence-box" id="gal-sentence-back">{{Sentence}}</div>
  {{#SentenceFurigana}}
  <div class="furigana-hint">{{SentenceFurigana}}</div>
  {{/SentenceFurigana}}

  <!-- 音畫情景記憶核心（聲優原聲 + 當下遊戲/動畫畫面 CG） -->
  {{#SentenceAudio}}
  <div class="audio-row">{{SentenceAudio}} <span style="font-size:12px; opacity:0.6;">跟著聲優語氣一起聽與念</span></div>
  {{/SentenceAudio}}

  {{#Screenshot}}
  <div class="scene-stage">{{Screenshot}}</div>
  {{/Screenshot}}

  <hr class="divider">

  <!-- Lv.1 專屬：超大平假名讀音焦點（對應攻略階段一） -->
  <div class="lv1-only">
    {{#TargetReading}}<div class="lv1-reading-hero">{{TargetReading}}</div>{{/TargetReading}}
    <div class="word-header">
      <span class="target-word" style="font-size:21px; opacity:0.85;">{{TargetWord}}</span>
      {{#PitchAccent}}<span class="pitch-badge">[{{PitchAccent}}]</span>{{/PitchAccent}}
    </div>
  </div>

  <!-- Lv.2 / Lv.3 詞頭 -->
  <div class="lv23-only word-header">
    <span class="target-word">{{TargetWord}}</span>
    {{#TargetReading}}<span class="target-reading">「{{TargetReading}}」</span>{{/TargetReading}}
    {{#PitchAccent}}<span class="pitch-badge">[{{PitchAccent}}]</span>{{/PitchAccent}}
  </div>

  <!-- AJATT 風格高低音調視覺曲線（支援既有舊卡自動於瀏覽器即時渲染 SVG） -->
  <div class="pitch-graph-row" id="gal-pitch-graph" data-reading="{{TargetReading}}" data-pitch="{{PitchAccent}}">{{PitchGraph}}</div>

  <!-- Lv.1 / Lv.2 主顯示：精煉核心釋義（1~2行，拒絕整頁詞典硬搬） -->
  {{#DefinitionBilingual}}
  <div class="core-meaning primary-bi">
    <div class="meaning-label">本句核心意思</div>
    <div>{{DefinitionBilingual}}</div>
  </div>
  {{/DefinitionBilingual}}

  <!-- Lv.3 主顯示：精煉日日單語釋義（登樓撤梯） -->
  {{#DefinitionMonolingual}}
  <div class="core-meaning primary-mono">
    <div class="meaning-label">日日單語核心釋義</div>
    <div>{{DefinitionMonolingual}}</div>
  </div>
  {{/DefinitionMonolingual}}

  <!-- 本句自然語境理解（Lv.1–2 直接可見，Lv.3 收入安全網） -->
  {{#SentenceTranslation}}
  <div class="context-quote context-primary">
    <div class="meaning-label">本句語境理解</div>
    <div>{{SentenceTranslation}}</div>
  </div>
  <details class="fold-box context-fold">
    <summary>整句語境理解（安全網）</summary>
    <div style="margin-top:6px;">{{SentenceTranslation}}</div>
  </details>
  {{/SentenceTranslation}}

  <!-- Lv.1 / Lv.2 進階對照日日單語 -->
  {{#DefinitionMonolingual}}
  <details class="fold-box secondary-mono">
    <summary>日日單語對照（進階）</summary>
    <div style="margin-top:8px; white-space:pre-wrap;">{{DefinitionMonolingual}}</div>
  </details>
  {{/DefinitionMonolingual}}

  <!-- Lv.3 雙語備援安全網 -->
  {{#DefinitionBilingual}}
  <details class="fold-box secondary-bi">
    <summary>雙語核心釋義（安全網）</summary>
    <div style="margin-top:8px; white-space:pre-wrap;">{{DefinitionBilingual}}</div>
  </details>
  {{/DefinitionBilingual}}

  <!-- 語序直譯骨架與句型活用拆解 -->
  {{#AIGrammarNotes}}
  <details class="fold-box">
    <summary>🧭 語序骨架・句型與助詞拆解</summary>
    <div class="grammar-body">{{AIGrammarNotes}}</div>
  </details>
  {{/AIGrammarNotes}}
</div>

<script>
(function () {
  var el = document.getElementById("gal-sentence-back");
  if (el) {
    el.innerHTML = el.innerHTML.replace(/【([^】]+)】/g, '<span class="focus-word">【$1】</span>');
  }
  // 舊卡自動補繪 SVG 高低音調曲線
  var pg = document.getElementById("gal-pitch-graph");
  if (!pg || pg.innerHTML.trim() !== "") return;
  var reading = (pg.getAttribute("data-reading") || "").replace(/[^ぁ-んァ-ヶー]/g, "");
  var rawPitch = pg.getAttribute("data-pitch") || "";
  var nums = rawPitch.match(/\d+/g);
  if (!reading || !nums || !nums.length) { pg.style.display = "none"; return; }
  var small = "ゃゅょぁぃぅぇぉゎャュョァィゥェォヮ";
  var moras = [];
  for (var i = 0; i < reading.length; i++) {
    var ch = reading[i];
    if (small.indexOf(ch) >= 0 && moras.length) moras[moras.length - 1] += ch;
    else moras.push(ch);
  }
  var n = moras.length;
  var labels = { heiban: "平板", atamadaka: "頭高", nakadaka: "中高", odaka: "尾高" };
  var out = [];
  nums.slice(0, 2).forEach(function (ns) {
    var ds = Math.min(Math.max(parseInt(ns, 10) || 0, 0), n);
    var pt = ds === 0 ? "heiban" : (ds === 1 ? "atamadaka" : (ds >= n ? "odaka" : "nakadaka"));
    var lv = [];
    for (var j = 0; j < n + 1; j++) {
      if (ds === 0) lv.push(j > 0);
      else if (ds === 1) lv.push(j === 0);
      else lv.push(j > 0 && j < ds);
    }
    var sx = 28, px = 16, yh = 11, yl = 31, yt = 51, w = px * 2 + sx * n, h = 58;
    var pts = lv.map(function (hi, idx) { return [px + idx * sx, hi ? yh : yl]; });
    var poly = n >= 2 ? '<polyline class="pt-line" fill="none" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" points="' + pts.slice(0, n).map(function (p) { return p[0] + "," + p[1]; }).join(" ") + '" />' : "";
    var dash = '<line class="pt-dash" x1="' + pts[n - 1][0] + '" y1="' + pts[n - 1][1] + '" x2="' + pts[n][0] + '" y2="' + pts[n][1] + '" stroke-width="2.0" stroke-dasharray="3,2.5" opacity="0.72" />';
    var dots = moras.map(function (m, idx) {
      var ring = (ds > 0 && idx + 1 === ds) ? '<circle class="pt-drop-ring" cx="' + pts[idx][0] + '" cy="' + pts[idx][1] + '" r="7.1" fill="none" stroke-width="1.3" opacity="0.55" />' : "";
      var fs = m.length > 1 ? 12.2 : 14;
      return ring + '<circle class="pt-dot" cx="' + pts[idx][0] + '" cy="' + pts[idx][1] + '" r="4.5" /><text class="pt-kana" x="' + pts[idx][0] + '" y="' + yt + '" text-anchor="middle" fill="currentColor" font-size="' + fs + '">' + m + '</text>';
    }).join("");
    var part = '<circle class="pt-particle" cx="' + pts[n][0] + '" cy="' + pts[n][1] + '" r="4.0" fill="none" stroke-width="1.8" />';
    var svg = '<svg class="pitch-svg pt-' + pt + '" width="' + w + '" height="' + h + '" viewBox="0 0 ' + w + ' ' + h + '">' + poly + dash + dots + part + '</svg>';
    out.push('<span class="pitch-item pt-' + pt + '">' + svg + '<span class="pitch-chip-tag pt-tag-' + pt + '">' + labels[pt] + ' [' + ds + ']</span></span>');
  });
  pg.innerHTML = out.join("");
})();
</script>
"""


def build_model_payload(note_type: str = NOTE_TYPE_NAME) -> dict:
    """組裝 AnkiConnect createModel 所需參數。"""
    return {
        "modelName": note_type,
        "inOrderFields": NOTE_FIELDS,
        "css": CARD_CSS,
        "isCloze": False,
        "cardTemplates": [
            {
                "Name": "Galgame 語境卡",
                "Front": FRONT_TEMPLATE,
                "Back": BACK_TEMPLATE,
            }
        ],
    }
