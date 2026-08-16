# -*- coding: utf-8 -*-
"""issue #17: manifest・ドキュメントと実装の整合テスト

machine-readable マニフェスト（describe）が無効な DSL を案内していないこと、
CLAUDE.md の件数記載が describe の実測とずれていないことを検証する。
"""
import json
import os
import re
import subprocess
import sys

import pytest

from scriptvedit import Project, describe

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _project_layer_cache_values():
    """実装側（project.py の検証タプル）から layer cache の許可値を抽出する。

    許可値の正は Project.layer の検証コードそのもの。名前付き定数が無いため
    ソースをパースして取り出す（二重管理を防ぐガード）。
    """
    src_path = os.path.join(_ROOT, "src", "scriptvedit", "project.py")
    with open(src_path, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r"if\s+cache\s+not\s+in\s+\(([^)]*)\)", src)
    assert m, "project.py に cache の検証タプルが見つからない"
    return sorted(v.strip().strip("'\"") for v in m.group(1).split(",") if v.strip())


def test_manifest_layer_cache_enum_matches_implementation():
    """manifest の layer_cache enum == 実装（Project.layer 検証タプル）の許可値"""
    manifest_enum = sorted(describe()["enums"]["layer_cache"])
    impl_enum = _project_layer_cache_values()
    assert manifest_enum == impl_enum, (
        f"manifest={manifest_enum} と実装={impl_enum} がずれている")


def test_layer_cache_enum_values_accepted_by_implementation():
    """manifest の enum 値は実際に Project.layer が受理し、enum 外は拒否する"""
    for val in describe()["enums"]["layer_cache"]:
        p = Project()
        p.layer("dummy_layer.py", cache=val)  # 受理される（検証は即時）
    with pytest.raises(ValueError):
        Project().layer("dummy_layer.py", cache="on")


def test_manifest_has_no_cache_on():
    """manifest 全体に存在しない cache='on' の案内が現れない"""
    text = json.dumps(describe(), ensure_ascii=False)
    assert "cache='on'" not in text
    assert 'cache="on"' not in text
    assert "cache=\\\"on\\\"" not in text


def test_manifest_until_examples_use_anchor_suffix():
    """usage.dsl / examples がサフィックス無しの .until('intro') を案内しない。

    time(name='intro') が生成するアンカーは intro.start / intro.end のみ。
    """
    text = json.dumps(describe(), ensure_ascii=False)
    for m in re.finditer(r"\.until\(\s*['\"]([^'\"]+)['\"]", text):
        name = m.group(1)
        assert name.endswith(".start") or name.endswith(".end"), (
            f"manifest がサフィックス無しの until({name!r}) を案内している"
            "（intro.start / intro.end を使うこと）")


def test_manifest_show_not_described_as_sequential():
    """show() を「順次配置」と説明しない（実装は _advance=False の並行表示）

    以前は describe 全文から「順次配置」を禁じていたが、Group.time() は
    実際に順次配置なのでその説明まで書けなくなっていた（Group 収載で顕在化）。
    show を説明している箇所だけを対象にする。
    """
    m = describe()
    texts = []
    for section in ("object_methods", "objects", "effects", "factories",
                    "project_methods", "meta"):
        for e in m.get(section, []):
            for m_ in e.get("methods", []):
                if m_["name"] == "show":
                    texts.append(json.dumps(m_, ensure_ascii=False))
            if e["name"].split(".")[-1] in ("show", "show_until"):
                texts.append(json.dumps(e, ensure_ascii=False))
    assert texts, "show を説明するエントリが describe に見つからない"
    for t in texts:
        assert "順次配置" not in t, f"show を順次配置と説明している: {t}"


def test_claude_md_counts_match_describe():
    """CLAUDE.md に記載の effects/audio_effects/factories 等の件数が describe と一致。

    他テストがプロセス内でプラグインを登録すると describe() の件数が変わるため、
    素の環境の件数をサブプロセスの `python -m scriptvedit describe` で取得する。
    """
    out = subprocess.run(
        [sys.executable, "-m", "scriptvedit", "describe"],
        capture_output=True, text=True, encoding="utf-8", cwd=_ROOT, check=True)
    d = json.loads(out.stdout)
    claude_md = os.path.join(_ROOT, "CLAUDE.md")
    with open(claude_md, encoding="utf-8") as f:
        text = f.read()
    checked = 0
    for key in ("effects", "transforms", "audio_effects", "factories",
                "objects", "object_methods", "project_methods", "expr",
                "plugins"):
        m = re.search(r"`%s`\((\d+)\)" % re.escape(key), text)
        if m is None:
            continue  # 記載が無ければ件数ずれも起きない
        checked += 1
        assert int(m.group(1)) == len(d[key]), (
            f"CLAUDE.md の `{key}`({m.group(1)}) が describe の実測"
            f"（{len(d[key])}）とずれている。CLAUDE.md を更新すること")
    # 監査対象の3種（effects/audio_effects/factories）は必ず記載・検証されること
    for key in ("effects", "audio_effects", "factories"):
        assert re.search(r"`%s`\(\d+\)" % re.escape(key), text), (
            f"CLAUDE.md に `{key}`(N) の記載が見つからない")
    assert checked >= 3


# --- 監査項目10-4: describe と実装の整合を固定する（再発防止の本体）---------

# describe のエントリを持つセクション（マニフェスト全体の検査対象）
_ENTRY_SECTIONS = ("effects", "transforms", "audio_effects", "factories",
                   "objects", "object_methods", "project_methods",
                   "expr", "plugins", "meta")

# describe に個別エントリを持たない公開名（理由つきの明示的な例外）。
# 例外リストが太り始めたら「describe＝契約」が崩れている合図なので、
# 件数の上限もテストで縛る。
_DESCRIBE_EXEMPT = {
    # マニフェスト自身のバージョン。describe() の manifest_version として
    # トップレベルに出るため、エントリとしては重複になる
    "MANIFEST_VERSION",
}


def _all_describe_names(manifest):
    """describe に載っている全エントリ名（`Project.render` と `render` の両方）"""
    names = set()
    for section in _ENTRY_SECTIONS:
        for e in manifest.get(section, []):
            names.add(e["name"])
            names.add(e["name"].split(".")[-1])
    return names


def _iter_entries(manifest):
    for section in _ENTRY_SECTIONS:
        for e in manifest.get(section, []):
            yield section, e


def test_all_public_names_are_described():
    """`from scriptvedit import *` で入る全名が describe から発見できる

    CLAUDE.md が「ソースを全部読む必要はない、describe が正」と宣言している
    以上、`__all__` にあるのに describe から辿れない名前は API 契約の破れ。
    pause / PI / E / P が全 example で使われるのに未収載だった（監査項目10）。
    """
    from scriptvedit.state import _pkg_all

    names = _all_describe_names(describe())
    missing = sorted(n for n in _pkg_all()
                     if n not in names and n not in _DESCRIBE_EXEMPT)
    assert not missing, (
        f"__all__ にあるのに describe に載っていない: {missing}。"
        f"manifest.py へ収載するか、理由つきで _DESCRIBE_EXEMPT へ入れること")
    assert len(_DESCRIBE_EXEMPT) <= 3, (
        "describe の例外リストが増えている（describe＝契約 が崩れかけている）")


def test_every_entry_has_a_summary():
    """全エントリの summary が空でない（docstring 無しは補助テーブルで埋める）

    Project.configure / Project.render は docstring が無く summary が "" だった。
    「使い方が describe から分からない API」は未収載と同じ。
    """
    empty = [f"{s}:{e['name']}" for s, e in _iter_entries(describe())
             if not e.get("summary", "").strip()]
    assert not empty, (
        f"summary が空のエントリ: {empty}。docstring を書くか "
        f"manifest.py の _MANIFEST_SUMMARIES へ宣言すること")


def test_summaries_are_not_truncated_docstring_lines():
    """要約が docstring の折り返し途中で切れていない（PEP 257 形式を強制）

    summary は docstring の1行目。1行目が次の行へ続く書き方だと
    「…静止してから」のような尻切れがそのまま describe に載る（監査項目10）。
    2行目以降は details フィールドへ載るので、情報は失われない。
    """
    import inspect

    from scriptvedit.objects import Group, Object
    from scriptvedit.project import Project
    from scriptvedit.state import _pkg_all, _pkg_ns

    targets = []
    ns = _pkg_ns()
    for name in _pkg_all():
        obj = ns.get(name)
        if callable(obj):
            targets.append((name, obj))
    for cls in (Project, Object, Group):
        for mname, m in inspect.getmembers(cls, predicate=inspect.isroutine):
            if not mname.startswith("_"):
                targets.append((f"{cls.__name__}.{mname}", m))

    bad = []
    for name, obj in targets:
        doc = inspect.getdoc(obj)
        if not doc:
            continue
        lines = doc.splitlines()
        if len(lines) > 1 and lines[1].strip():
            bad.append(f"{name}: {lines[0][:40]}…")
    assert not bad, (
        "docstring の1行目が要約として独立していない（2行目を空行にするか、"
        f"1行に収めること）: {bad}")


def test_details_field_carries_the_rest_of_the_docstring():
    """docstring の2行目以降が details として describe に載る

    単位や前提は2行目以降に書かれていることが多く、以前は丸ごと落ちていた。
    """
    m = describe(name="throw")
    entry = m["effects"][0]
    assert "+y" in entry.get("details", ""), (
        "throw の details に座標系の説明（+y の向き）が載っていない")


@pytest.mark.parametrize("factory,param,values", [
    ("move", "anchor", None),
    ("wipe", "direction", None),
    ("audio_viz", "kind", None),
    ("slideshow", "transition", None),
])
def test_declared_choices_are_accepted_by_implementation(factory, param, values):
    """choices に載せた値はすべて実装が受理する（公称と実装の乖離を禁じる）

    anchor は「6値公称・1値実装」、audio_viz.kind は存在しない 'bars' を
    公称して実在の 'spectrum' を隠していた（監査項目10）。
    """
    import scriptvedit as sv
    from scriptvedit.media import _validate_xfade_kind

    m = describe(name=factory)
    entry = next(e for _s, e in _iter_entries(m) if e["name"] == factory)
    choices = entry["params"][param].get("choices")
    assert choices, f"{factory}.{param} に choices が無い"
    # 既定値は必ず候補に含まれること（slideshow の 'fade' が漏れていた）
    default = entry["params"][param].get("default")
    if default is not None:
        assert default in choices, (
            f"{factory}.{param} の既定値 {default!r} が choices に無い")
    for v in choices:
        if factory == "move":
            sv.move(x=0.5, y=0.5, anchor=v)
        elif factory == "wipe":
            assert sv.wipe(direction=v).params["direction"] == v
        elif factory == "audio_viz":
            # 実際の生成は重いので、実装が持つ許可集合と突き合わせる
            from scriptvedit.state import _AUDIO_VIZ_KINDS
            assert v in _AUDIO_VIZ_KINDS
        else:
            _validate_xfade_kind("slideshow", v)   # 未知なら ValueError


def test_anchor_choices_produce_distinct_placements():
    """anchor の全候補が互いに異なる overlay 座標式を返す（公称＝実装）"""
    from scriptvedit import Object, asset, move
    from scriptvedit.filters.video import _build_move_exprs

    p = Project()
    p.configure(width=1280, height=720, fps=30)
    exprs = {}
    for a in describe()["enums"]["anchor"]:
        o = Object(asset("images/shape_badge.png"))
        o <= move(x=0.5, y=0.5, anchor=a)
        exprs[a] = _build_move_exprs(o, 0, 3)
    assert len(set(map(str, exprs.values()))) == len(exprs), (
        f"互いに同じ式を返す anchor がある（公称だけ増やしていないか）: {exprs}")


def test_wipe_directions_produce_distinct_filters():
    """wipe の direction 4値が互いに異なるフィルタを生む"""
    from scriptvedit import Object, asset, wipe
    from scriptvedit.filters.video import _build_effect_filters

    p = Project()
    p.configure(width=1280, height=720, fps=30)
    seen = {}
    for d in describe()["enums"]["wipe_direction"]:
        o = Object(asset("images/shape_badge.png"))
        o.time(2) <= wipe(direction=d)
        filters, _pad = _build_effect_filters(o, 0, 2)
        seen[d] = "|".join(filters)
    assert len(set(seen.values())) == len(seen), (
        f"direction が違うのに同じフィルタ: {seen}")


def test_object_typed_params_reject_strings():
    """`type: "object"` 宣言のパラメータは文字列を拒否する

    morph_to(target) / assemble_from(source) は Object 以外を TypeError に
    するのに、describe は `type: "string"` +「画像パス」と案内していた。
    """
    import scriptvedit as sv

    m = describe()
    declared = [(e["name"], pname)
                for _s, e in _iter_entries(m)
                for pname, pmeta in e.get("params", {}).items()
                if pmeta.get("type") == "object"]
    assert declared, "type: 'object' 宣言のパラメータが1つも無い"
    callers = {
        "morph_to": lambda v: sv.morph_to(v),
        "assemble_from": lambda v: sv.assemble_from(v),
        "transition": lambda v: sv.transition(v, v),
    }
    for fname, pname in declared:
        assert fname in callers, (
            f"{fname}.{pname} が type:'object' 宣言なのにテストの呼び出し表に無い")
        with pytest.raises((TypeError, ValueError)):
            callers[fname]("some/path.png")


def test_string_typed_params_accept_strings(tmp_path):
    """`type: "string"` 宣言のパラメータは実際に文字列を受ける

    実装はファイルの存在まで検証するので、実在するパスで確かめる
    （type が string なのに文字列を受けない、という逆側の乖離を防ぐ）。
    """
    import scriptvedit as sv
    from scriptvedit import asset

    cube = tmp_path / "id.cube"
    cube.write_text(
        "LUT_3D_SIZE 2\n" + "\n".join(
            f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)),
        encoding="utf-8")
    assert sv.lut(str(cube)).params["file"] == str(cube)

    mask_png = asset("images/mask_circle.png")
    # 公開引数名は image_path、内部の Effect パラメータ名は image
    assert sv.mask(mask_png).params["image"] == mask_png

    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
    p = Project()
    p.configure(width=320, height=180, fps=10)
    assert sv.subtitles(str(srt)) is not None

def test_param_descriptions_are_not_cut_mid_bracket():
    """パラメータ説明が括弧の途中で切れていない

    docstring からの説明抽出は「。」で区切るため、
    "縁取りの色（ffcolor形式。例 'black'）" が "縁取りの色（ffcolor形式" に
    なっていた（監査項目10）。括弧の対応で切断を検出する。
    """
    pairs = (("（", "）"), ("(", ")"), ("「", "」"))
    bad = []
    for _s, e in _iter_entries(describe()):
        for pname, pmeta in e.get("params", {}).items():
            desc = pmeta.get("desc", "")
            if any(desc.count(a) != desc.count(b) for a, b in pairs):
                bad.append(f"{e['name']}.{pname}: {desc}")
    assert not bad, f"括弧が閉じていない説明: {bad}"
