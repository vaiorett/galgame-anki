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

BACK_TEMPLATE = """
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
  if (!el) return;
  el.innerHTML = el.innerHTML.replace(/【([^】]+)】/g, '<span class="focus-word">【$1】</span>');
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
