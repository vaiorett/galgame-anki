"""可離線搜尋的日語要點與讀法。句型、助詞和活用沿用各自的資料來源。"""
from dataclasses import dataclass
from pathlib import Path
import json
import re
import unicodedata

from galgame_anki.core.study_map import CONCEPTS
from galgame_anki.core.verb_lesson import FORMS, build_lesson


@dataclass(frozen=True)
class Topic:
    key: str
    group: str
    title: str
    rule: str
    rows: tuple  # 日文、讀音或接續、中文說明
    before: str = ""
    tags: str = ""


ONES = "零一二三四五六七八九"
READINGS = "れい いち に さん よん ご ろく なな はち きゅう".split()


def numeral(n):
    if n < 10:
        return ONES[n]
    tens, units = divmod(n, 10)
    return (ONES[tens] if tens > 1 else "") + "十" + (ONES[units] if units else "")


def number_reading(n):
    if n < 10:
        return READINGS[n]
    tens, units = divmod(n, 10)
    return (READINGS[tens] if tens > 1 else "") + "じゅう" + (READINGS[units] if units else "")


def numbered_rows(suffix, readings, chinese):
    return tuple((numeral(i) + suffix, reading, chinese.format(n=i)) for i, reading in enumerate(readings, 1))


def quantity_topics():
    group = "數字與數量"
    yield Topic("numbers", group, "數字、十進位與萬進位", "先找位值，再讀各位上的數。十、百、千前面的一通常省略；到了萬位要讀一萬。",
                tuple((numeral(n), number_reading(n), str(n)) for n in range(21)) + (
                    ("零／ゼロ", "れい／ゼロ", "0"), ("四／七／九", "し／しち／く", "和特定單位連讀時會用到"),
                    ("三百／六百／八百", "さんびゃく／ろっぴゃく／はっぴゃく", "300／600／800"),
                    ("三千／八千", "さんぜん／はっせん", "3000／8000"),
                    ("一万／一億／一兆", "いちまん／いちおく／いっちょう", "一萬／一億／一兆"),
                    ("二万三千", "にまんさんぜん", "23,000")), tags="數詞 數量 基數 百千萬億")
    yield Topic("counters", group, "數量詞怎麼選", "先找被數的東西，再選單位，最後把數字和單位連起來讀。數量可放在動詞前，也可用「數量＋の」修飾名詞。",
                (("切手を三枚買う。", "きってを さんまい かう", "買三張郵票。枚用來數薄片。"),
                 ("三枚の切手を買う。", "さんまいの きってを かう", "把「三張」直接放在郵票前。"),
                 ("何枚ですか。", "なんまいですか", "問幾張時保留同一個單位。")), "numbers", "量詞 助數詞 助数詞 數量 數量詞 幾個")
    counters = (
        ("つ", "一般物件", "個", "ひとつ ふたつ みっつ よっつ いつつ むっつ ななつ やっつ ここのつ とお", "いくつ", "不知道專用單位時常用和語數法。一至九寫作數字加つ，十寫「十」。"),
        ("人", "人數", "個人", "ひとり ふたり さんにん よにん ごにん ろくにん ななにん はちにん きゅうにん じゅうにん", "なんにん", "一人、二人各有讀法。從三人起接にん；四人讀よにん。七人也可讀しちにん。"),
        ("個", "小物件", "個物件", "いっこ にこ さんこ よんこ ごこ ろっこ ななこ はっこ きゅうこ じゅっこ", "なんこ", "用於可逐個計數的小物件，例如蘋果。留意一、六、八、十的促音。"),
        ("本", "細長物", "支", "いっぽん にほん さんぼん よんほん ごほん ろっぽん ななほん はっぽん きゅうほん じゅっぽん", "なんぼん", "雨傘、鉛筆、瓶子常用本。比較一本、二本、三本的ぽん、ほん、ぼん。"),
        ("枚", "薄片", "張", "", "なんまい", "紙、票、照片等薄片用枚。"),
        ("冊", "書刊", "本書", "いっさつ にさつ さんさつ よんさつ ごさつ ろくさつ ななさつ はっさつ きゅうさつ じゅっさつ", "なんさつ", "裝訂成冊的書本、筆記本用冊。"),
        ("台", "機器與車輛", "台", "", "なんだい", "數車輛、電腦等機器時用台。"),
        ("匹", "小動物", "隻", "いっぴき にひき さんびき よんひき ごひき ろっぴき ななひき はっぴき きゅうひき じゅっぴき", "なんびき", "貓、狗、小魚等常用匹。大型動物常用頭，鳥常用羽。"),
        ("杯", "杯碗的份量", "杯", "いっぱい にはい さんばい よんはい ごはい ろっぱい ななはい はっぱい きゅうはい じゅっぱい", "なんばい", "計算飲料或盛好的飯時，以容器為單位。"),
        ("回", "次數", "次", "いっかい にかい さんかい よんかい ごかい ろっかい ななかい はっかい きゅうかい じゅっかい", "なんかい", "數動作發生幾次。加目可以表示第幾次。"),
        ("階", "樓層", "樓", "いっかい にかい さんがい よんかい ごかい ろっかい ななかい はっかい きゅうかい じゅっかい", "なんがい", "問樓層用何階。三階常讀さんがい，注意與三回的差別。"),
        ("歳", "年齡", "歲", "いっさい にさい さんさい よんさい ごさい ろくさい ななさい はっさい きゅうさい じゅっさい", "なんさい", "數年齡用歳，也常寫才。二十歲有專用讀法。"),
    )
    for suffix, kind, chinese, series, question, rule in counters:
        base = series.split() if series else [number_reading(i) + {"枚":"まい", "台":"だい"}[suffix] for i in range(1, 11)]
        rows = list(numbered_rows(suffix, base, "{n}" + chinese))
        if suffix == "つ":
            rows[-1] = ("十", "とお", "10個")
        if suffix == "歳":
            rows.append(("二十歳", "はたち", "20歲"))
        rows.append(("いくつ" if suffix == "つ" else "何" + suffix, question, "幾" + chinese))
        yield Topic("counter-" + suffix, group, kind + "（" + suffix + "）", rule, tuple(rows), "counters", "量詞 助數詞")


def calendar_topics():
    group = "日曆與時間"
    months = "いち に さん し ご ろく しち はち く じゅう じゅういち じゅうに".split()
    yield Topic("months", group, "月份：一年十二個月", "說日曆上的月份，用數字加「がつ」。四月、七月、九月分別取し、しち、く。",
                numbered_rows("月", [n+"がつ" for n in months], "{n}月") + (("何月", "なんがつ", "幾月"),), "numbers", "月份 月份表 月曆")
    days = "ついたち ふつか みっか よっか いつか むいか なのか ようか ここのか とおか".split()
    for n in range(11, 32):
        special = {14:"じゅうよっか", 17:"じゅうしちにち", 19:"じゅうくにち", 20:"はつか",
                   24:"にじゅうよっか", 27:"にじゅうしちにち", 29:"にじゅうくにち"}
        days.append(special.get(n, number_reading(n)+"にち"))
    yield Topic("dates", group, "日期：一號到三十一號", "日曆先說月，再說日。月初十天、十四、二十、二十四要整個辨認。問幾號用何日。",
                numbered_rows("日", days, "{n}號") + (("四月一日", "しがつついたち", "4月1日"), ("何日", "なんにち", "幾號／幾天")), "months", "日付 日期 幾號 幾日")
    yield Topic("weekdays", group, "星期與週末", "取星期名稱的第一個漢字，加曜日。日曆上的（月）是星期一。",
                tuple((k+"曜日", r+"ようび", "星期"+c) for k,r,c in zip("月火水木金土日", "げつ か すい もく きん ど にち".split(), "一二三四五六日")) +
                (("何曜日", "なんようび", "星期幾"), ("平日／週末", "へいじつ／しゅうまつ", "平日／週末")), tags="禮拜 星期天 星期日 週一 曜日")
    yield Topic("hours", group, "時刻：幾點與半點", "在鐘面上定位，用數字加時。四時、七時、九時分別讀よじ、しちじ、くじ；24 小時制直接唸十三時至二十四時。",
                numbered_rows("時", [("よ" if i == 4 else r)+"じ" for i,r in enumerate(months,1)], "{n}點") +
                tuple((numeral(h) + "時",
                       {14: "じゅうよじ", 17: "じゅうしちじ", 19: "じゅうくじ", 24: "にじゅうよじ"}.get(h, number_reading(h) + "じ"),
                       f"{h}點") for h in range(13, 25)) +
                (("零時", "れいじ", "0點／午夜"), ("午前／午後", "ごぜん／ごご", "上午／下午"), ("七時半", "しちじはん", "7點半"), ("何時", "なんじ", "幾點")), "numbers", "小時 時鐘 點鐘 24小時制")
    minute = "いっぷん にふん さんぷん よんぷん ごふん ろっぷん ななふん はっぷん きゅうふん じゅっぷん".split()
    def read_minute(n):
        tens, unit = divmod(n,10)
        if not tens:
            return minute[unit-1]
        prefix = number_reading(tens) if tens > 1 else ""
        return prefix + ("じゅう" + minute[unit-1] if unit else minute[-1])
    yield Topic("minutes", group, "分鐘：分和半怎麼讀", "先讀幾時，再讀幾分。「分」讀ふん或ぷん。十位後若還有個位，用個位來決定結尾。",
                numbered_rows("分", [read_minute(i) for i in range(1,61)], "{n}分鐘") + (("何分", "なんぷん", "幾分鐘"),), "hours", "時間 分鐘 幾分")
    elapsed = "いっ に さん よん ご ろっ なな はっ きゅう じゅっ じゅういっ じゅうに".split()
    yield Topic("monthspan", group, "期間：經過幾個月", "回答「多久」時用か月（かげつ），可寫作箇月、カ月或ヶ月。「四月」是四月，「四か月」是四個月。",
                numbered_rows("か月", [n+"かげつ" for n in elapsed], "{n}個月") + (("何か月", "なんかげつ", "幾個月"),), "months", "期間 時長 月數 幾個月")
    yield Topic("durations", group, "時點與時長：幾天、幾週、幾年", "看問題是「什麼時候」或「多久」。同樣寫一日，日期讀ついたち，一整天讀いちにち。",
                (("一日", "いちにち", "1天／一整天"), ("三日間", "みっかかん", "3天"), ("四時間", "よじかん", "4小時"),
                 ("一週間", "いっしゅうかん", "1週／一個星期"), ("一年間", "いちねんかん", "1年"), ("三年目", "さんねんめ", "第3年"),
                 ("二時に会う。", "にじに あう", "兩點見面。"), ("二時間待つ。", "にじかん まつ", "等兩小時。")), "dates", "持續 天數 小時 星期 年份 期間")


def reference_topics(grammar):
    topics = [*quantity_topics(), *calendar_topics()]
    data = Path(__file__).resolve().parents[1] / "data" / "reference_topics.json"
    for item in json.loads(data.read_text(encoding="utf-8")):
        topics.append(Topic(**{**item, "rows": tuple(map(tuple, item["rows"]))}))
    for key, concept in CONCEPTS.items():
        topics.append(Topic("study-" + key, "逐步拆解", concept.title, concept.rule, (
            (concept.first, "", concept.contrast), (concept.second, "", ""),
            (concept.prompt, "", concept.solution)), "study-" + concept.parent if concept.parent else "", concept.branch))
    for key, (title, form, meaning) in FORMS.items():
        rows = []
        for verb, kind in (("食べる","v1"), ("読む","v5"), ("する","vs"), ("来る","vk")):
            lesson = build_lesson(verb, kind, key)
            if lesson:
                rows.append((lesson.surface, verb, lesson.rule))
        if rows:
            topics.append(Topic("verb-" + key, "動詞活用", form + " · " + title, meaning, tuple(rows), "study-form"))
    for p in grammar.particles.values():
        topics.append(Topic("particle-" + p.surface, "助詞", p.surface + " · " + p.role, p.meaning,
                            ((p.surface, p.formula, p.role),), "study-roles", p.level))
    for p in grammar.patterns:
        topics.append(Topic("pattern-" + p["id"], "句型 · " + p["category"], p["name"], p["meaning"],
                            ((p["example"], p["formula"], p["meaning"]),), tags=p["level"]))
    return topics


# 同一條目可從中文數字、阿拉伯數字、簡繁中文、片假名、羅馬音及常見輸入形式查到。
_DIGITS = "〇一二三四五六七八九"
_EXTRA_S2T = str.maketrans("两兩个岁号数读词间时张几点礼拜週动被语假汉", "二二個歲號數讀詞間時張幾點禮拜周動被語假漢")
_FIELD_TOKEN_SPLIT_RE = re.compile(r"[\s/／()（）·・、，,；;。]+")


def _decimal(match):
    value = match.group().replace("零", "〇")
    if "十" in value:
        if value.count("十") != 1:
            return value
        tens, ones = value.split("十")
        if len(tens) > 1 or len(ones) > 1:
            return value
        return str((_DIGITS.index(tens) if tens else 1) * 10 + (_DIGITS.index(ones) if ones else 0))
    return "".join(str(_DIGITS.index(c)) for c in value)


def normalized(value):
    from galgame_anki.core.dictionary import normalize_zh_tw

    value = unicodedata.normalize("NFKC", value or "")
    value = normalize_zh_tw(value)
    value = value.casefold()
    value = re.sub(r"(?<=\d),(?=\d)", "", value)
    value = value.translate(_EXTRA_S2T)
    for variant in ("ヶ月", "箇月", "カ月", "ヵ月"):
        value = value.replace(variant, "か月")
    value = "".join(chr(ord(c)-96) if "ァ" <= c <= "ヶ" else c for c in value)
    # 保護「五十音」不被轉為「50音」
    return re.sub(r"[〇零一二三四五六七八九十]+(?!音)", _decimal, value)


class ReferenceIndex:
    def __init__(self, grammar):
        self.topics = {t.key: t for t in reference_topics(grammar)}
        self.terms = {}
        for t in self.topics.values():
            norm_rows = []
            row_atoms = []
            for r in t.rows:
                nr = tuple(normalized(c) for c in r)
                norm_rows.append(nr)
                atoms: set[str] = set()
                for f in nr:
                    atoms.add(f)
                    for part in _FIELD_TOKEN_SPLIT_RE.split(f):
                        if part:
                            atoms.add(part)
                row_atoms.append(atoms)
            title_norm = normalized(t.title)
            title_atoms = {p for p in _FIELD_TOKEN_SPLIT_RE.split(title_norm) if p}
            self.terms[t.key] = (
                title_norm,
                title_atoms,
                normalized(t.tags + " " + t.group),
                norm_rows,
                row_atoms,
                normalized(t.rule),
            )

    def date_parts(self, query):
        """未指定年份的月日允許閏日，不將月份或日期錯當期間。"""
        match = re.fullmatch(r"(\d{1,2})月(\d{1,2})[日號]", normalized(query).strip())
        if not match:
            return None
        month, day = map(int, match.groups())
        last_days = (31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
        if 1 <= month <= 12 and 1 <= day <= last_days[month - 1]:
            return month, day
        return None

    def find(self, query):
        """傳回條目與該頁最相關的列；精確命中的助詞、詞頭、數字或讀音優先於子字串命中。"""
        date = self.date_parts(query)
        if date:
            month, day = date
            return [("dates", day - 1), ("months", month - 1)]
        terms = normalized(query).split()
        matches = []
        for key, (title, title_atoms, tags, rows, row_atoms, rule) in self.terms.items():
            if not terms:
                matches.append((0, 0, key, None))
                continue
            scores = []
            best_row = None
            best_score = 0
            for i, (fields, atoms) in enumerate(zip(rows, row_atoms)):
                row_score = 0
                for term in terms:
                    if term in fields:
                        row_score += 8
                    elif term in atoms:
                        row_score += 6
                    elif any(term in f for f in fields):
                        row_score += 2
                if row_score > best_score:
                    best_row, best_score = i, row_score

            is_exact_particle = len(terms) == 1 and key == "particle-" + terms[0]
            for term in terms:
                if is_exact_particle or term == title:
                    score = 16
                elif term in title_atoms:
                    score = 13
                elif any(term in fields for fields in rows):
                    score = 11
                elif any(term in atoms for atoms in row_atoms):
                    score = 9
                elif term in title:
                    score = 7
                elif term in tags:
                    score = 5
                elif any(term in field for fields in rows for field in fields):
                    score = 3
                elif term in rule:
                    score = 1
                else:
                    break
                scores.append(score)
            if len(scores) == len(terms):
                # 基礎主題（數字／日曆／假名／助詞／逐步拆解）在同分時優先於大量句型子字串
                group_tiebreak = 1 if key.startswith("pattern-") else 0
                matches.append((-sum(scores), group_tiebreak, key, best_row))
        matches.sort(key=lambda m: (m[0], m[1]))
        return [(key, row) for _, _, key, row in matches]


_INDEX_CACHE: dict[tuple[int, int], ReferenceIndex] = {}


def get_reference_index(grammar) -> ReferenceIndex:
    """快取 ReferenceIndex，避免每次開啟語法與詞彙地圖時重複建構索引。"""
    cache_key = (id(grammar), len(getattr(grammar, "patterns", ())))
    cached = _INDEX_CACHE.get(cache_key)
    if cached is None:
        cached = ReferenceIndex(grammar)
        _INDEX_CACHE[cache_key] = cached
    return cached
