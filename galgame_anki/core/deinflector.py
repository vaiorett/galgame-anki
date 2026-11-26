"""Yomitan 風格的日語活用形還原（Deinflection）引擎。

支援一段動詞、五段動詞、サ變、カ變、い形容詞的遞迴連綴活用還原，
例如：
- 「踏ん張れなかった」 -> 「踏ん張る」（可能形 -> 否定形 -> 過去形）
- 「かぶれすぎた」     -> 「かぶれる」（〜すぎる -> 過去形）
- 「振舞います」       -> 「振舞う」（丁寧形）
- 「歩いた」           -> 「歩く」（過去形）
- 「知りたい」         -> 「知る」（〜たい願望形）
"""

from __future__ import annotations

from dataclasses import dataclass

# 詞性規則位元旗標
RULE_NONE = 0
RULE_V1 = 1 << 0        # 一段動詞 (る結尾)
RULE_V5 = 1 << 1        # 五段動詞
RULE_VS = 1 << 2        # サ變動詞 (する)
RULE_VK = 1 << 3        # カ變動詞 (くる / 来る)
RULE_ADJ_I = 1 << 4     # い形容詞
RULE_IRU = 1 << 5       # 補助一段 (〜ている / 〜すぎる 等中間態)
RULE_ANY_VERB = RULE_V1 | RULE_V5 | RULE_VS | RULE_VK | RULE_IRU
RULE_ALL = RULE_ANY_VERB | RULE_ADJ_I


@dataclass(frozen=True)
class DeinflectRule:
    kana_in: str
    kana_out: str
    rules_in: int
    rules_out: int
    reason: str


@dataclass(frozen=True)
class DeinflectCandidate:
    term: str
    rules: int
    reasons: tuple[str, ...] = ()


def _build_rules() -> list[DeinflectRule]:
    """建構常用且高準確度的日語活用還原規則表。"""
    rules: list[DeinflectRule] = []

    def add(kana_in: str, kana_out: str, r_in: int, r_out: int, reason: str) -> None:
        rules.append(DeinflectRule(kana_in, kana_out, r_in, r_out, reason))

    # --- 1. 丁寧形 (〜ます體系) ---
    polite_suffixes = [
        ("ませんでした", "ます", "丁寧・過去否定"),
        ("ました", "ます", "丁寧・過去"),
        ("ません", "ます", "丁寧・否定"),
        ("まして", "ます", "丁寧・て形"),
        ("ましょう", "ます", "丁寧・意志"),
    ]
    for s_in, s_out, reason in polite_suffixes:
        add(s_in, s_out, RULE_NONE, RULE_IRU, reason)

    # 〜ます -> 原形
    add("ます", "る", RULE_NONE | RULE_IRU, RULE_V1, "丁寧形")
    for stem, dict_end in [
        ("い", "う"),
        ("き", "く"),
        ("ぎ", "ぐ"),
        ("し", "す"),
        ("ち", "つ"),
        ("に", "ぬ"),
        ("び", "ぶ"),
        ("み", "む"),
        ("り", "る"),
    ]:
        add(f"{stem}ます", dict_end, RULE_NONE | RULE_IRU, RULE_V5, "丁寧形")
    add("します", "する", RULE_NONE | RULE_IRU, RULE_VS, "丁寧形")
    add("きます", "くる", RULE_NONE | RULE_IRU, RULE_VK, "丁寧形")
    add("来ます", "来る", RULE_NONE | RULE_IRU, RULE_VK, "丁寧形")

    # --- 2. 複合表現（〜すぎる / 〜たい / 〜やすい / 〜にくい / 〜そう） ---
    for compound, reason in [
        ("すぎる", "〜すぎる（過於）"),
        ("過ぎる", "〜過ぎる（過於）"),
    ]:
        # 只針對一段活用形作為後綴（如 すぎる -> すぎた）
        add(compound, "る", RULE_V1, RULE_V1, reason)
        for stem, dict_end in [
            ("い", "う"), ("き", "く"), ("ぎ", "ぐ"), ("し", "す"),
            ("ち", "つ"), ("に", "ぬ"), ("び", "ぶ"), ("み", "む"), ("り", "る"),
        ]:
            add(f"{stem}{compound}", dict_end, RULE_V1, RULE_V5, reason)
        add(compound, "い", RULE_V1, RULE_ADJ_I, reason)

    # 〜やすい / 〜にくい（本身按い形容詞變化）
    for adj_suffix, reason in [("やすい", "〜やすい（容易）"), ("にくい", "〜にくい（難以）")]:
        add(adj_suffix, "る", RULE_ADJ_I, RULE_V1, reason)
        for stem, dict_end in [
            ("い", "う"), ("き", "く"), ("ぎ", "ぐ"), ("し", "す"),
            ("ち", "つ"), ("に", "ぬ"), ("び", "ぶ"), ("み", "む"), ("り", "る"),
        ]:
            add(f"{stem}{adj_suffix}", dict_end, RULE_ADJ_I, RULE_V5, reason)
        add(f"し{adj_suffix}", "する", RULE_ADJ_I, RULE_VS, reason)
        add(f"き{adj_suffix}", "くる", RULE_ADJ_I, RULE_VK, reason)
        add(f"来{adj_suffix}", "来る", RULE_ADJ_I, RULE_VK, reason)

    # 〜たい (願望形，本身按い形容詞變化)
    add("たい", "る", RULE_ADJ_I, RULE_V1, "願望形（〜たい）")
    for stem, dict_end in [
        ("い", "う"), ("き", "く"), ("ぎ", "ぐ"), ("し", "す"),
        ("ち", "つ"), ("に", "ぬ"), ("び", "ぶ"), ("み", "む"), ("り", "る"),
    ]:
        add(f"{stem}たい", dict_end, RULE_ADJ_I, RULE_V5, "願望形（〜たい）")
    add("したい", "する", RULE_ADJ_I, RULE_VS, "願望形（〜たい）")
    add("きたい", "くる", RULE_ADJ_I, RULE_VK, "願望形（〜たい）")
    add("来たい", "来る", RULE_ADJ_I, RULE_VK, "願望形（〜たい）")

    # --- 3. 進行體・補助動詞（〜ている / 〜てる / 〜てしまう / 〜ちゃう / 〜ておく / 〜とく） ---
    for aux, te_form, reason in [
        ("ている", "て", "〜ている（進行/狀態）"),
        ("てる", "て", "〜てる（口語進行）"),
        ("でいる", "で", "〜でいる（進行/狀態）"),
        ("でる", "で", "〜でる（口語進行）"),
        ("てしまう", "て", "〜てしまう（完了/遺憾）"),
        ("でしまう", "で", "〜でしまう（完了/遺憾）"),
        ("ておく", "て", "〜ておく（預先）"),
        ("でおく", "で", "〜でおく（預先）"),
        ("てみる", "て", "〜てみる（嘗試）"),
        ("でみる", "で", "〜でみる（嘗試）"),
        ("てくる", "て", "〜てくる"),
        ("でくる", "で", "〜でくる"),
        ("て来る", "て", "〜て来る"),
        ("で来る", "で", "〜で来る"),
        ("ていく", "て", "〜ていく"),
        ("でいく", "で", "〜でいく"),
        ("て行く", "て", "〜て行く"),
        ("で行く", "で", "〜で行く"),
    ]:
        aux_rule = (
            RULE_V5
            if aux.endswith(("う", "く", "行く"))
            else (RULE_VK if aux.endswith(("くる", "来る")) else RULE_V1)
        )
        add(aux, te_form, aux_rule, RULE_IRU, reason)

    # 口語約音・命令・禁止・必須
    add("ちゃう", "て", RULE_V5, RULE_IRU, "〜ちゃう（〜てしまう）")
    add("じゃう", "で", RULE_V5, RULE_IRU, "〜じゃう（〜でしまう）")
    add("とく", "て", RULE_V5, RULE_IRU, "〜とく（〜ておく）")
    add("どく", "で", RULE_V5, RULE_IRU, "〜どく（〜でおく）")
    add("てろ", "て", RULE_NONE, RULE_IRU, "命令（〜ていろ）")
    add("でろ", "で", RULE_NONE, RULE_IRU, "命令（〜でいろ）")
    add("てんだ", "てる", RULE_NONE, RULE_V1, "口語解釋（〜ているのだ）")
    add("でんだ", "でる", RULE_NONE, RULE_V1, "口語解釋（〜でいるのだ）")
    add("てたんだ", "てた", RULE_NONE, RULE_IRU, "過去口語解釋（〜ていたのだ）")
    add("でたんだ", "でた", RULE_NONE, RULE_IRU, "過去口語解釋（〜でいたのだ）")
    add("てるんだ", "てる", RULE_NONE, RULE_V1, "口語解釋（〜ているのだ）")
    add("でるんだ", "でる", RULE_NONE, RULE_V1, "口語解釋（〜でいるのだ）")
    add("ちゃだめ", "て", RULE_NONE, RULE_IRU, "禁止（〜てはだめ）")
    add("じゃだめ", "で", RULE_NONE, RULE_IRU, "禁止（〜ではだめ）")
    add("なきゃ", "ない", RULE_NONE, RULE_ADJ_I, "必須（〜なければ）")
    add("なくちゃ", "ない", RULE_NONE, RULE_ADJ_I, "必須（〜なくては）")
    add("ないで", "ない", RULE_NONE, RULE_ADJ_I, "否定て形（〜ないで）")

    # --- 4. 過去形・て形・たら・たり ---
    # 優先匹配不規則サ變・カ變（避免「したら」誤拆成一段「しる」！）
    add("した", "する", RULE_IRU, RULE_VS, "過去形")
    add("して", "する", RULE_IRU, RULE_VS, "て形")
    add("したら", "する", RULE_IRU, RULE_VS, "假定（〜たら）")
    add("したり", "する", RULE_IRU, RULE_VS, "列舉（〜たり）")
    add("きた", "くる", RULE_IRU, RULE_VK, "過去形")
    add("きて", "くる", RULE_IRU, RULE_VK, "て形")
    add("きたら", "くる", RULE_IRU, RULE_VK, "假定（〜たら）")
    add("きたり", "くる", RULE_IRU, RULE_VK, "列舉（〜たり）")
    add("来た", "来る", RULE_IRU, RULE_VK, "過去形")
    add("来て", "来る", RULE_IRU, RULE_VK, "て形")
    add("来たら", "来る", RULE_IRU, RULE_VK, "假定（〜たら）")
    add("来たり", "来る", RULE_IRU, RULE_VK, "列舉（〜たり）")

    # 一段動詞
    for s_in, reason in [
        ("た", "過去形"), ("て", "て形"), ("たら", "假定（〜たら）"), ("たり", "列舉（〜たり）"),
    ]:
        add(s_in, "る", RULE_IRU, RULE_V1, reason)

    # 五段音便
    godan_ta_te = [
        ("った", "う", "過去形"), ("って", "う", "て形"), ("ったら", "う", "假定"), ("ったり", "う", "列舉（〜たり）"),
        ("った", "つ", "過去形"), ("って", "つ", "て形"), ("ったら", "つ", "假定"), ("ったり", "つ", "列舉（〜たり）"),
        ("った", "る", "過去形"), ("って", "る", "て形"), ("ったら", "る", "假定"), ("ったり", "る", "列舉（〜たり）"),
        ("いた", "く", "過去形"), ("いて", "く", "て形"), ("いたら", "く", "假定"), ("いたり", "く", "列舉（〜たり）"),
        ("いだ", "ぐ", "過去形"), ("いで", "ぐ", "て形"), ("いだら", "ぐ", "假定"), ("いだり", "ぐ", "列舉（〜たり）"),
        ("した", "す", "過去形"), ("して", "す", "て形"), ("したら", "す", "假定"), ("したり", "す", "列舉（〜たり）"),
        ("んだ", "ぬ", "過去形"), ("んで", "ぬ", "て形"), ("んだら", "ぬ", "假定"), ("んだり", "ぬ", "列舉（〜たり）"),
        ("んだ", "ぶ", "過去形"), ("んで", "ぶ", "て形"), ("んだら", "ぶ", "假定"), ("んだり", "ぶ", "列舉（〜たり）"),
        ("んだ", "む", "過去形"), ("んで", "む", "て形"), ("んだら", "む", "假定"), ("んだり", "む", "列舉（〜たり）"),
        ("行った", "行く", "過去形"), ("行って", "行く", "て形"), ("行ったら", "行く", "假定"), ("行ったり", "行く", "列舉（〜たり）"),
        ("いった", "いく", "過去形"), ("いって", "いく", "て形"), ("いったら", "いく", "假定"), ("いったり", "いく", "列舉（〜たり）"),
    ]
    for s_in, s_out, reason in godan_ta_te:
        add(s_in, s_out, RULE_IRU, RULE_V5, reason)

    # --- 5. 否定形 (〜ない 按い形容詞變化) ---
    add("しない", "する", RULE_ADJ_I, RULE_VS, "否定形")
    add("せず", "する", RULE_NONE, RULE_VS, "文語否定（〜ず）")
    add("こない", "くる", RULE_ADJ_I, RULE_VK, "否定形")
    add("来ない", "来る", RULE_ADJ_I, RULE_VK, "否定形")
    add("ない", "る", RULE_ADJ_I, RULE_V1, "否定形")
    add("ず", "る", RULE_NONE, RULE_V1, "文語否定（〜ず）")
    add("ぬ", "る", RULE_NONE, RULE_V1, "文語否定（〜ぬ）")
    for a_stem, dict_end in [
        ("わ", "う"), ("か", "く"), ("が", "ぐ"), ("さ", "す"),
        ("た", "つ"), ("な", "ぬ"), ("ば", "ぶ"), ("ま", "む"), ("ら", "る"),
    ]:
        add(f"{a_stem}ない", dict_end, RULE_ADJ_I, RULE_V5, "否定形")
        add(f"{a_stem}ず", dict_end, RULE_NONE, RULE_V5, "文語否定（〜ず）")
        add(f"{a_stem}ぬ", dict_end, RULE_NONE, RULE_V5, "文語否定（〜ぬ）")
        add(f"{a_stem}ん", dict_end, RULE_NONE, RULE_V5, "口語否定（〜ん）")

    # --- 6. 可能・被動・使役（轉換為一段動詞繼續參與外層變化！） ---
    # 一段可能/被動/尊敬
    add("られる", "る", RULE_V1, RULE_V1, "可能/被動形")
    add("れる", "る", RULE_V1, RULE_V1, "ら抜き可能形")
    add("させる", "る", RULE_V1, RULE_V1, "使役形")

    # 五段可能形（え段 + る）
    for e_stem, dict_end in [
        ("え", "う"), ("け", "く"), ("げ", "ぐ"), ("せ", "す"),
        ("て", "つ"), ("ね", "ぬ"), ("べ", "ぶ"), ("め", "む"), ("れ", "る"),
    ]:
        add(f"{e_stem}る", dict_end, RULE_V1, RULE_V5, "可能形")

    # 五段被動形（あ段 + れる）與使役形（あ段 + せる）
    for a_stem, dict_end in [
        ("わ", "う"), ("か", "く"), ("が", "ぐ"), ("さ", "す"),
        ("た", "つ"), ("な", "ぬ"), ("ば", "ぶ"), ("ま", "む"), ("ら", "る"),
    ]:
        add(f"{a_stem}れる", dict_end, RULE_V1, RULE_V5, "被動形")
        add(f"{a_stem}せる", dict_end, RULE_V1, RULE_V5, "使役形")

    add("される", "する", RULE_V1, RULE_VS, "被動形")
    add("させる", "する", RULE_V1, RULE_VS, "使役形")
    add("こられる", "くる", RULE_V1, RULE_VK, "可能/被動形")
    add("来られる", "来る", RULE_V1, RULE_VK, "可能/被動形")
    add("こさせる", "くる", RULE_V1, RULE_VK, "使役形")
    add("来させる", "来る", RULE_V1, RULE_VK, "使役形")

    # --- 7. 假定形 (〜ば) 與 意志形 (〜よう / お段+う) ---
    add("れば", "る", RULE_NONE, RULE_V1, "假定形（〜ば）")
    add("よう", "る", RULE_NONE, RULE_V1, "意志形")
    for e_stem, o_stem, dict_end in [
        ("え", "お", "う"), ("け", "こ", "く"), ("げ", "ご", "ぐ"),
        ("せ", "そ", "す"), ("て", "と", "つ"), ("ね", "の", "ぬ"),
        ("べ", "ぼ", "ぶ"), ("め", "も", "む"), ("れ", "ろ", "る"),
    ]:
        add(f"{e_stem}ば", dict_end, RULE_NONE, RULE_V5, "假定形（〜ば）")
        add(f"{o_stem}う", dict_end, RULE_NONE, RULE_V5, "意志形")
    add("すれば", "する", RULE_NONE, RULE_VS, "假定形（〜ば）")
    add("しよう", "する", RULE_NONE, RULE_VS, "意志形")
    add("くれば", "くる", RULE_NONE, RULE_VK, "假定形（〜ば）")
    add("来れば", "来る", RULE_NONE, RULE_VK, "假定形（〜ば）")
    add("こよう", "くる", RULE_NONE, RULE_VK, "意志形")
    add("来よう", "来る", RULE_NONE, RULE_VK, "意志形")

    # --- 8. い形容詞活用 ---
    adj_rules = [
        ("かった", "い", "過去形"),
        ("くなかった", "い", "過去否定形"),
        ("くない", "い", "否定形"),
        ("くて", "い", "て形"),
        ("く", "い", "連用形（副詞化）"),
        ("ければ", "い", "假定形"),
        ("さ", "い", "名詞化（〜さ）"),
        ("そう", "い", "樣態（〜そう）"),
    ]
    for s_in, s_out, reason in adj_rules:
        add(s_in, s_out, RULE_NONE, RULE_ADJ_I, reason)

    # --- 9. 連用形（ます形詞幹，如 竈に火を入れ、負け、振舞い） ---
    add("", "る", RULE_NONE, RULE_V1, "連用形")
    for i_stem, dict_end in [
        ("い", "う"), ("き", "く"), ("ぎ", "ぐ"), ("し", "す"),
        ("ち", "つ"), ("に", "ぬ"), ("び", "ぶ"), ("み", "む"), ("り", "る"),
    ]:
        add(i_stem, dict_end, RULE_NONE, RULE_V5, "連用形")

    return rules


DEINFLECT_RULES = _build_rules()


def pos_matches_rule(pos_text: str, required_rules: int) -> bool:
    """檢查詞典條目的詞性標籤是否符合活用規則要求。"""
    if required_rules == RULE_NONE or not pos_text:
        return True
    p = pos_text.lower()
    if (required_rules & RULE_V1) and ("v1" in p or "一段" in p or "ichidan" in p):
        return True
    if (required_rules & RULE_V5) and ("v5" in p or "五段" in p or "godan" in p):
        return True
    if (required_rules & RULE_VS) and ("vs" in p or "サ変" in p or "suru" in p):
        return True
    if (required_rules & RULE_VK) and ("vk" in p or "カ変" in p or "kuru" in p):
        return True
    if (required_rules & RULE_ADJ_I) and ("adj-i" in p or "形容詞" in p or "i-adj" in p):
        return True
    # 有些社群 Yomitan 詞典沒有細分 v1/v5 只寫 v 或空字串，保持寬容
    if (required_rules & RULE_ANY_VERB) and (p == "v" or "verb" in p or "動詞" in p):
        return True
    return False


def deinflect(source: str, max_depth: int = 5) -> list[DeinflectCandidate]:
    """對日文片語執行廣度優先遞迴還原，回傳所有合法候選辭書形。"""
    if not source:
        return []

    results: list[DeinflectCandidate] = [DeinflectCandidate(term=source, rules=RULE_NONE, reasons=())]
    seen: set[tuple[str, int]] = {(source, RULE_NONE)}

    idx = 0
    while idx < len(results):
        current = results[idx]
        idx += 1

        if len(current.reasons) >= max_depth:
            continue

        for rule in DEINFLECT_RULES:
            # 若目前是中間還原結果，需符合外層規則相容性
            if current.rules != RULE_NONE and not (current.rules & rule.rules_in):
                continue

            if rule.kana_in:
                if not current.term.endswith(rule.kana_in):
                    continue
                stem = current.term[: -len(rule.kana_in)]
                # 獨立不規則動詞（する / くる / 来る / 行く / いく）本身沒有詞幹前綴，允許 stem 為空！
                if not stem and rule.kana_out not in ("する", "くる", "来る", "行く", "いく"):
                    continue
                next_term = stem + rule.kana_out
            else:
                # 連用形補 る（僅限原始表層形且長度 >= 1，避免無窮遞迴）
                if current.rules != RULE_NONE or len(current.term) < 1:
                    continue
                # 避免把已經是標點/純助詞做連用形補る
                if current.term[-1] in "。、！？「」『』（）・…ーっん":
                    continue
                next_term = current.term + rule.kana_out

            key = (next_term, rule.rules_out)
            if key in seen:
                continue
            seen.add(key)

            next_reasons = current.reasons + (rule.reason,)
            results.append(
                DeinflectCandidate(
                    term=next_term,
                    rules=rule.rules_out,
                    reasons=next_reasons,
                )
            )

    return results
