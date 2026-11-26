"""以當前台詞為索引的語法與詞彙關係。只用可核對的詞形或實際相鄰的詞組。"""
from dataclasses import dataclass
import re

from galgame_anki.core.study_steps import Walk, conjugation_walk

from galgame_anki.core.verb_lesson import FORMS, build_lesson, lesson_for_token, verb_group
from galgame_anki.core.tokenizer import Token
from galgame_anki.core.context_meaning import explain_lookup_in_sentence


@dataclass(frozen=True)
class Concept:
    branch: str
    title: str
    parent: str
    rule: str
    first: str
    second: str
    contrast: str
    prompt: str
    solution: str
    module: str


CONCEPTS = {
    "roles": Concept("語法", "誰對什麼做什麼", "", "找出句尾的動作，再看前面哪個名詞用助詞接到它。",
        "猫が魚を食べる。", "魚が餌を食べる。", "が 引出做動作的一方；を 標出這兩句裡被吃的東西。",
        "「猫が魚を食べる」裡，哪個字告訴你魚是被吃的？", "を。整組讀成「魚を食べる」。", "gmod/"),
    "form": Concept("語法", "動詞怎麼換詞尾", "roles", "先找原形。保留不變的部分，看看新的詞尾對動作加了什麼意思。",
        "明日、行く。", "昨日、行った。", "動作都是去。第二句把動作看成已發生。",
        "把「昨日、帰る」改成已經回家了。", "昨日、帰った。", "gmod/"),
    "negative": Concept("語法", "動作有沒有發生", "form", "否定和時間可以疊在一起。先變否定，再把「ない」換成「なかった」。",
        "本音を言わない。", "本音を言わなかった。", "前一句說不說真心話，後一句回顧當時沒說。",
        "「沒去」和「不去」怎麼區分？", "行かない＝不去；行かなかった＝那次沒去。", "gmod/"),
    "ability": Concept("語法", "沒做，還是沒能做到", "negative", "表示能力的形式也能再接否定和過去；這兩步各自改變一層意思。",
        "本音を言わなかった。", "本音を言えなかった。", "前句只說當時沒說。後句多了「沒能說出口」，原因要聽上下文。",
        "如果知道答案，卻緊張得說不出口，應選哪句？", "言えなかった。言う → 言える（能說）→ 言えない → 言えなかった。", "dmod/lp/15_1_1.html"),
    "connect": Concept("語法", "把動作接下去", "form", "停在「て／で」時，把後面接的是另一個動作、請求，還是補助動詞一起讀。",
        "座って、話す。", "座ってください。", "前句把坐下和說話接起來；後句請對方坐下。",
        "「待って」後面接什麼可以明確表示禮貌請求？", "待ってください。", "dmod/lp/21_1_1.html"),
    "state": Concept("語法", "持續的動作或狀態", "connect", "動詞接「ている」能說正在做，也能說變化後留下的狀態。時間與動詞會影響理解。",
        "今、窓を開けている。", "もう窓が開いている。", "前句可描述開窗的動作，後句描述窗戶敞開的狀態。",
        "「駅で待ってた」省掉哪個音？事情在什麼時候？", "待っていた省掉「い」。說的是之前正在等。", "gmod/"),
    "completion": Concept("語法", "做完或已經發生", "connect", "「てしまう」把注意力放在結果。是否帶著懊惱，可從情境和聲音判斷。",
        "全部読んだ。", "全部読んじゃった。", "兩句都說讀完。後句把讀到最後這個結果說得更突出。",
        "「食べちゃった」還原成沒有縮約的寫法。", "食べてしまった。", "gmod/"),
    "prepare": Concept("語法", "先做以供之後使用", "connect", "「ておく」說的是事先準備或維持狀態。口語裡「ておく／でおく」常變成「とく／どく」。",
        "資料を読んで。", "先に資料を読んどいて。", "後句多了事先讀好的意思。",
        "「買っとく」省略前怎麼說？", "買っておく。", "gmod/"),
    "condition": Concept("語法", "把前句當作條件", "form", "た形加「ら」把一件事設為條件。後項有時欲言又止，要從下一句尋找。",
        "ドアが開いた。", "ドアが開いたら、入って。", "前句陳述門開了；後句把門打開設成進去的條件。",
        "「地球が壊れたら…」有沒有說地球已經壞了？", "這裡提出「要是地球毀了……」的假設，後面仍可接結果。", "gmod/"),
    "stance": Concept("語法", "句尾的說話用意", "roles", "命題相同，換個句尾就能補充說明、確認或向對方傳達自己的態度。",
        "待ってた。", "待ってたんだよ。", "前句交代等過；後句把這件事當作解釋，再用 よ 告訴對方。",
        "要解釋剛才為什麼沒走，「待ってた」後面能加什麼？", "んだ。加 よ 則更明顯地把解釋說給對方聽。", "dmod/lp/"),
    "word": Concept("詞彙", "詞義與搭配", "", "把一個詞連同前後搭配一起記。核對詞義後，試著只替換一個成分。",
        "手紙を書く。", "日記を書く。", "保留「を＋書く」，把寫的內容從信換成日記。",
        "用「書く」說自己會寫名字。", "名前を書くことができる。短句也可說「名前が書ける」。", "vmod/"),
    "pair": Concept("詞彙", "自動詞和他動詞", "word", "先看說的是東西本身的變化，還是某人對它做了什麼。兩種視角常用不同動詞。",
        "窓が開く。", "窓を開ける。", "前句是窗戶打開；後句是有人把窗戶打開。",
        "把「ドアが閉まる」改成『把門關上』。", "ドアを閉める。", "vmod/"),
    "compound": Concept("詞彙", "拆開複合詞", "word", "先找右邊的核心詞，再看左邊如何限定它。記住整個詞的用法，再用已知成分認新詞。",
        "入学式に出る。", "卒業式に出る。", "兩者都是「式」（典禮）。前面換成入学或卒業，就換了典禮的場合。",
        "在『入學典禮』和『畢業典禮』兩個詞中，共通的成分是什麼？", "式（しき）。入学式（にゅうがくしき）、卒業式（そつぎょうしき）。", "vmod/"),
    "register": Concept("詞彙", "對誰說，怎麼選詞", "word", "先看雙方關係，再聽詞尾。相近的意思可以有不同的親疏與禮貌程度。",
        "遅れてごめん。", "遅れてすみません。", "兩句都為遲到致歉。第一句適合親近的人；第二句較客氣。「すみません」在其他情境也能用來叫住人。",
        "要叫住一位陌生人問路，開頭可以怎麼說？", "すみません、駅はどこですか。", "dmod/lp/"),
    "feelings": Concept("詞彙", "感受、心情與真心話", "word", "先找說話者指的是持續的心意、當下的心情，還是平常沒說出口的想法。",
        "気持ちを伝える。", "本音を言う。", "前句重在傳達心意，後句重在說出真心話。形容當下的心情也常用「気分」。",
        "「平常沒說出口的真話」用「気分」還是「本音」更合適？", "本音。例如「やっと本音を言えた」。", "vmod/"),
    "trend": Concept("語法", "變化往哪裡發展", "form", "把表示變化的動詞放在「一方だ」前面，表達同一趨勢持續發展。",
        "不安が募った。", "不安は募る一方だ。", "第一句回顧不安加深了；第二句說不安仍在不斷加深。",
        "用「増える」說工作量一直在增加。", "仕事は増える一方だ。", "gmod/"),
    "idiom": Concept("詞彙", "語塊與固定表達", "word", "先替整個語塊找意思，再觀察結尾動詞如何變化。",
        "息を殺して待つ。", "気を遣って話す。", "息を殺す是屏住呼吸。気を遣う是顧慮別人；兩者都適合連成一組讀。",
        "「息を殺して待つ」裡，等待時做了什麼？", "屏住呼吸。這裡的殺す和息を一起構成慣用說法。", "vmod/"),
}

VERB_PAIRS = (("開く", "開ける"), ("閉まる", "閉める"), ("壊れる", "壊す"), ("落ちる", "落とす"))
PAIRS = {word: (counterpart, particle) for vi, vt in VERB_PAIRS
         for word, counterpart, particle in ((vi, vt, "が"), (vt, vi, "を"))}
COMPOUNDS = {"入学式": "入学（入學）＋式（典禮）", "卒業式": "卒業（畢業）＋式（典禮）",
             "図書館": "図書（圖書）＋館（場所）", "映画館": "映画（電影）＋館（場所）"}
POTENTIAL_STEMS = dict(zip("うつるむぶぬくぐす", "えてれめべねけげせ"))

FORM_TOPICS = {
    "te": "connect", "teiru": "state", "teru": "state", "teta": "state", "tenai": "state",
    "tenda": "stance", "tetanda": "stance", "tero": "stance", "chau": "completion",
    "chatta": "completion", "toku": "prepare", "toite": "prepare", "tara": "condition",
    "nakya": "condition", "nai": "negative", "chadame": "stance",
}


@dataclass(frozen=True)
class Focus:
    key: str
    evidence: str
    vocabulary: str
    lexical_key: str
    walk: Walk | None = None
    lexical_walk: Walk | None = None


def token_group(token):
    group = verb_group(token.lemma, token.verb_type)
    if group or not token.lookup:
        return group
    return next((g for e in token.lookup.all_entries if (g := verb_group(token.lemma, e.pos))), "")


def focus_for(token: Token, *, phrase=False, particle=None, tokens=(), sentence="", translation="") -> Focus:
    lemma, surface = token.lemma, token.surface
    lexical = "word"
    if lemma in COMPOUNDS:
        lexical = "compound"
    elif surface in ("ごめん", "ごめんなさい", "すみません", "すまない"):
        lexical = "register"
    elif phrase:
        lexical = "idiom"
    elif lemma in PAIRS and not (lemma == "開く" and token.reading == "ひらく"):
        lexical = "pair"
    elif lemma in ("気持ち", "気分", "本音"):
        lexical = "feelings"
    vocab = surface if phrase else lemma
    if not phrase:
        for i, t in enumerate(tokens[:-2]):
            marker, verb = tokens[i+1:i+3]
            if token.start < t.start or token.end > verb.end:
                continue
            if (t.end == marker.start and marker.end == verb.start and t.is_content_word and
                    marker.surface in ("を", "が", "に", "で") and token_group(verb)):
                vocab = t.surface + marker.surface + verb.surface
                if lexical == "pair" and token.start == verb.start:
                    other, expected = PAIRS[lemma]
                    if marker.surface == expected:
                        counterpart = "を" if expected == "が" else "が"
                        vocab = f"{t.surface}{expected}{lemma}　↔　{t.surface}{counterpart}{other}"
                break
    if lexical == "compound":
        vocab += "　＝　" + COMPOUNDS[lemma]
    if lexical == "pair" and "↔" not in vocab:
        vocab += f"　↔　{PAIRS[lemma][0]}（自他對照）"
    lexical_walk = None
    if lexical == "pair":
        other, case_marker = PAIRS[lemma]
        left, _, right = vocab.partition("　↔　")
        perspective = ("從事物本身的變化，改看有人對它做了什麼。" if case_marker == "が"
                       else "從有人對事物做了什麼，改看事物本身的變化。")
        lexical_walk = Walk(surface, (
            ("找出原形與搭配", f"這個用法的原形是「{lemma}」，常用「{case_marker}」標出發生變化的事物。", left),
            ("改變描述視角", perspective + f"換用另一個動詞「{other}」。", right or other),
        ))
    elif lexical == "compound":
        lexical_walk = Walk(lemma, (("找出兩個成分", "先看右側核心，再看左側限定它的種類。", COMPOUNDS[lemma]),
                                   ("合回一個詞", "用右側的類別理解整個詞。", lemma)))
    elif vocab != lemma and not phrase:
        lexical_walk = Walk(lemma, (("找回台詞裡的形式", "把詞典裡的詞放回它真正出現的位置。", surface),
                                   ("連同相鄰詞一起讀", "助詞把前面的詞接到後面的動作。", vocab)))
    if tokens and token.end > token.start:
        tail = "".join(t.surface for t in tokens if t.start >= token.end)
        if token.verb_type and tail.startswith("一方だ"):
            return Focus("trend", surface + "一方だ", vocab, lexical, Walk(surface, (("確認變化動詞", "用原形來說變化的方向。", lemma), ("指出趨勢持續", "原形加上一方だ，表示這個變化還在繼續。", surface + "一方だ"))), lexical_walk)
    word_evidence = f"{surface} → {lemma}" if surface != lemma else surface
    if phrase or lexical != "word" or not token.verb_type:
        gloss, _ = explain_lookup_in_sentence(token, sentence, translation)
        if gloss:
            word_evidence += "　" + gloss
            if lexical_walk is None:
                lexical_walk = Walk(surface, (
                    ("對照詞典的查詢單位", "先辨認整個語塊。" if phrase else "把台詞中的寫法對回詞頭。", lemma),
                    ("核對這個意思放進原句是否通順", "下面是本機根據詞條與語境給出的候選義。", gloss),
                ))
    if phrase:
        return Focus(lexical, word_evidence, vocab, lexical, lexical_walk, lexical_walk)
    # 五段可能形（如 言える）另接否定過去。須吻合完整詞形，以免誤判普通一段動詞。
    group = token_group(token)
    if lemma == "言える" and surface == "言えなかった":
        lemma, group = "言う", "v5"
    if group == "v5" and lemma[-1:] in POTENTIAL_STEMS:
        suffix = POTENTIAL_STEMS[lemma[-1]]
        possible = lemma[:-1] + suffix
        if surface == possible + "なかった":
            walk = Walk(lemma, (("先得到能做的形式", f"五段詞尾「{lemma[-1]}」換成「{suffix}る」。", possible + "る"),
                                ("否定能做這件事", "將る換成ない，表示不能做到。", possible + "ない"),
                                ("回顧當時的情況", "ない換成なかった。現在才同時具備『沒能』和『當時』兩層意思。", surface)))
            return Focus("ability", f"{lemma} → {possible}る（能）→ {possible}ない（不能）→ {surface}（當時沒能）", vocab, lexical, walk, lexical_walk)
    if group:
        negative = build_lesson(lemma, group, "nai")
        if negative and surface == negative.surface[:-1] + "かった":
            walk = Walk(lemma, (("先否定動作", negative.rule, negative.surface),
                                ("再放到過去", "保留否定，把ない換成なかった，交代當時沒有做。", surface)))
            return Focus("negative", f"{lemma} → {negative.surface}（否定）→ {surface}（過去）", vocab, lexical, walk, lexical_walk)
    lesson = lesson_for_token(token)
    if lesson:
        intermediate = lesson.expanded
        if lesson.form == "tara":
            intermediate = surface[:-1]
        parts = [lemma, *([intermediate] if intermediate else []), surface]
        walk = conjugation_walk(lesson)
        if lesson.form == "tara" and sentence[token.start:token.end] == surface:
            tail = re.split(r"[。！？!?…」』.\n]", sentence[token.end:], maxsplit=1)[0].strip("、，, ")
            if tail:
                final = ("找到條件成立後的後項", "現在回到原句，讀出條件後面實際接著的話。", tail)
            else:
                final = ("確認後項有沒有說出來", "這段字幕停在條件。結果要等接下來的對白才能確定。", "後項尚未說出")
            walk = Walk(walk.start, walk.steps + (final,))
        return Focus(FORM_TOPICS.get(lesson.form, "form"), " → ".join(parts) + "　" + FORMS[lesson.form][2],
                     vocab, lexical, walk, lexical_walk)
    if particle:
        key = "stance" if surface in ("よ", "ね", "ぞ", "ぜ", "か") else "roles"
        return Focus(key, f"{surface}　{particle.role}　{particle.meaning}", vocab, lexical,
                     Walk(surface, (("看助詞連接了什麼", particle.meaning, vocab if vocab != surface else particle.role),)), lexical_walk)
    return Focus(lexical, word_evidence, vocab, lexical, lexical_walk, lexical_walk)


def lineage(key):
    result = [key]
    while CONCEPTS[result[-1]].parent:
        result.append(CONCEPTS[result[-1]].parent)
    return list(reversed(result))
