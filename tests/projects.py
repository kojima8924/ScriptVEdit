# -*- coding: utf-8 -*-
"""テストプロジェクト定義の単一の正（スナップショットと実レンダの共通ソース）

`tests/test_snapshot.py`（dry_run のコマンド比較）と `tests/test_real_render.py`
（実 FFmpeg レンダ）は、どちらもこのモジュールの `PROJECTS` からプロジェクトを
組み立てる。以前は同じ構成が両方に書かれており、片方だけ直しても気づけなかった
（監査項目11）。**プロジェクト構成の変更はここだけを直すこと。**

各エントリは `ProjectSpec`:

- `configure`  … `Project.configure()` へ渡す引数（既定 1280x720/30fps/黒背景）
- `layers`     … `tests/layers/` のファイル名（`priority` は並び順）
- `setup`      … configure 直後・layer 登録前に走るフック（marker 等）
- `prepare`    … 構築〜レンダを包む context manager（TTS モック・ダミーキャッシュ）
- `output`     … 出力ファイル名（既定 `<name>.mp4`。拡張子でコンテナが決まる）
- `render_kwargs` … `render()` の追加引数（start/end・draft・alpha など）
- `snapshot`   … False ならスナップショット対象外（実レンダ専用）
- `needs`      … 実レンダに必要な外部環境（"web" / "morph" / "font"）
- `assets`     … gitignore 対象の大容量素材（無ければ skip）

使い方::

    from projects import PROJECTS
    spec = PROJECTS["test01"]
    with spec.build() as p:
        cmd = p.render(spec.output, dry_run=True, **spec.render_kwargs)
"""
import contextlib
import json
import os

from scriptvedit import Project, asset, load_plugins

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
LAYERS_DIR = os.path.join(TESTS_DIR, "layers")
PLUGINS_DIR = os.path.join(ROOT, "plugins")
OUTPUT_DIR = os.path.join(TESTS_DIR, "output")

# 既定の画面設定（大多数のテストはこれを使う）
_DEFAULT_CONFIGURE = {
    "width": 1280, "height": 720, "fps": 30, "background_color": "black",
}


def L(name):
    """レイヤーファイルを絶対パスで解決（cwd 非依存）"""
    return os.path.join(LAYERS_DIR, name)


def out(name):
    """実レンダの出力先（tests/output/）"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return os.path.join(OUTPUT_DIR, name)


class ProjectSpec:
    """1つのテストプロジェクトの構成（レンダ呼び出しは含まない）"""

    def __init__(self, name, layers=(), *, configure=None, setup=None,
                 prepare=None, output=None, render_kwargs=None, doc="",
                 snapshot=True, needs=(), assets=(), plugins=False,
                 requires=(), factory=None):
        self.name = name
        self.layers = tuple(layers)
        self.configure = dict(configure) if configure else dict(_DEFAULT_CONFIGURE)
        self.setup = setup
        self.prepare = prepare
        self.output = output or f"{name}.mp4"
        self.render_kwargs = dict(render_kwargs or {})
        self.doc = doc
        self.snapshot = snapshot
        self.needs = tuple(needs)
        self.assets = tuple(assets)
        self.plugins = plugins
        # 実レンダの前提となる別テスト（test15 は test14 のレイヤーキャッシュを使う）
        self.requires = tuple(requires)
        self._factory = factory

    @contextlib.contextmanager
    def build(self, mode="dry_run"):
        """Project を組み立てて yield する（後始末は context manager が持つ）

        mode: "dry_run" か "real"。ダミーキャッシュを使うテストだけが区別する。
        """
        if self._factory is not None:
            with self._factory(mode) as project:
                yield project
            return
        with (self.prepare() if self.prepare is not None
              else contextlib.nullcontext()):
            if self.plugins:
                load_plugins(PLUGINS_DIR)
            p = Project()
            p.configure(**self.configure)
            if self.setup is not None:
                self.setup(p)
            for i, entry in enumerate(self.layers):
                filename, kwargs = entry if isinstance(entry, tuple) else (entry, {})
                p.layer(L(filename), priority=i, **kwargs)
            yield p

    def __repr__(self):
        return f"<ProjectSpec {self.name}>"


# --- 特別な前処理が要るテスト ---

@contextlib.contextmanager
def _mock_tts():
    """VOICEVOX 不要で narrate を通すため tts.tts / tts_duration を差し替える"""
    from scriptvedit import tts as svtts
    orig_tts, orig_dur = svtts.tts, svtts.tts_duration
    fake_wav = asset("audio/bgm_loop.mp3")

    def _fake_tts(text, *, speaker=1, speed=1.0, pitch=0.0, **kw):
        return fake_wav

    def _fake_dur(path):
        return 2.5

    svtts.tts = _fake_tts
    svtts.tts_duration = _fake_dur
    try:
        yield
    finally:
        svtts.tts = orig_tts
        svtts.tts_duration = orig_dur


@contextlib.contextmanager
def _test15_factory(mode):
    """cache='use': dry_run はダミーキャッシュ、実レンダは test14 の実キャッシュ

    dry_run ではキャッシュ内容を読まないのでダミー（1バイト webm + anchors.json）
    で足りる。実レンダは中身を再生する必要があるため、test14（cache='make'）を
    先にレンダしておくことが前提（`requires=("test14",)`）。
    """
    def _make(cache):
        p = Project()
        p.configure(width=1280, height=720, fps=30, background_color="darkblue")
        p.layer(L("test14_maku.py"), priority=0, **cache)
        p.layer(L("test14_oni.py"), priority=1)
        return p

    if mode != "dry_run":
        yield _make({"cache": "use"})
        return

    from scriptvedit.cache import _layer_cache_paths
    # キャッシュ鍵は解決済み総尺を含むため、まず同構成のプロジェクトを
    # dry_run して総尺を解決してからキャッシュパスを計算する
    probe = _make({})
    probe.render("test15_probe.mp4", dry_run=True)
    dummy_webm, dummy_json = _layer_cache_paths(L("test14_maku.py"), probe)
    os.makedirs(os.path.dirname(dummy_webm), exist_ok=True)
    with open(dummy_webm, "wb") as f:
        f.write(b"\x00")          # dry_run なので中身は読まれない
    with open(dummy_json, "w", encoding="utf-8") as f:
        json.dump({"duration": 3.0, "anchors": {"curtain_done": 3.0}}, f)
    try:
        yield _make({"cache": "use"})
    finally:
        for path in (dummy_webm, dummy_json):
            if os.path.exists(path):
                os.unlink(path)
        parent = os.path.dirname(dummy_webm)
        if os.path.exists(parent) and not os.listdir(parent):
            os.rmdir(parent)


def _cfg(**kw):
    """既定の configure を部分的に上書きする"""
    c = dict(_DEFAULT_CONFIGURE)
    c.update(kw)
    return c


_SPECS = [
    ProjectSpec("test01", ["test01_bg.py", "test01_oni.py"],
                configure=_cfg(background_color="darkred"),
                doc="画像2枚の基本合成（背景+前景）"),
    ProjectSpec("test02", ["test02_maku.py", "test02_cafe.py"],
                doc="幕素材+写真の基本合成"),
    ProjectSpec("test03", ["test03_bg.py", "test03_oni.py", "test03_virus.py"],
                doc="3レイヤーの重ね合わせ"),
    ProjectSpec("test04", ["test04_maku.py", "test04_cache_layer.py"],
                configure=_cfg(background_color="white"),
                doc="レイヤーキャッシュ対象を含む合成"),
    ProjectSpec("test05", ["test05_bg.py", "test05_pop.py"],
                configure=_cfg(background_color="green"),
                doc="ポップアップ演出"),
    ProjectSpec("test06", ["test06_oni.py"],
                configure=_cfg(background_color="olive"),
                doc="単一レイヤー"),
    ProjectSpec("test07", ["test07_oni.py", "test07_cafe.py", "test07_virus.py"],
                configure=_cfg(background_color="navy"),
                doc="3レイヤー+個別エフェクト"),
    ProjectSpec("test08", ["test08_bg.py", "test08_pop.py"],
                configure=_cfg(background_color="darkgreen"),
                doc="背景+ポップ"),
    ProjectSpec("test09", ["test09_oni.py", "test09_cafe.py", "test09_virus.py",
                           "test09_pop.py"],
                configure=_cfg(background_color="gray"),
                doc="4レイヤーの合成"),
    ProjectSpec("test10", ["test10_maku.py", "test10_cache_layer.py"],
                configure=_cfg(background_color="purple"),
                doc="レイヤーキャッシュ+幕"),
    ProjectSpec("test11", ["test11_maku.py", "test11_oni.py"],
                configure=_cfg(background_color="darkblue"),
                doc="anchor/pause.until による順次配置（start>0 の tpad 経路）"),
    ProjectSpec("test12", ["test12_sin_fade.py", "test12_lambda_scale.py",
                           "test12_lambda_move.py"],
                configure=_cfg(background_color="darkslategray"),
                doc="Expr(sin)/lambda によるアニメーション"),
    ProjectSpec("test13", ["test13_percent.py"],
                doc="パーセント指定の配置"),
    ProjectSpec("test14", [("test14_maku.py", {"cache": "make"}),
                           "test14_oni.py"],
                configure=_cfg(background_color="darkblue"),
                doc="cache='make': レイヤーキャッシュの生成"),
    ProjectSpec("test15", factory=_test15_factory, requires=("test14",),
                doc="cache='use': レイヤーキャッシュからの読み込み"),
    ProjectSpec("test16", ["test16_bgm.py", "test16_oni.py"],
                doc="音声ミックス: BGM(mp3) + 画像+SE",
                assets=("audio/効果音.mp3",)),
    ProjectSpec("test17", ["test17_video_split.py", "test17_audio.py"],
                configure=_cfg(background_color="darkgreen"),
                doc="AV split: 音声なし動画 + 音声のみ"),
    ProjectSpec("test18", ["test18_length.py"],
                configure=_cfg(background_color="gray"),
                doc="length(): ffprobe で取得した長さを使用"),
    ProjectSpec("test19", ["test19_web.py"], needs=("web",),
                doc="web クリップ: HTML→透明webm→合成"),
    ProjectSpec("test20", ["test20_bg.py", "test20_subtitles.py",
                           "test20_bubble.py"], needs=("web",),
                doc="字幕/吹き出し: subtitle+bubble+背景合成"),
    ProjectSpec("test21", ["test21_diagram.py", "test21_overlay.py"],
                configure=_cfg(background_color="darkslategray"),
                needs=("web",),
                doc="図解: diagram SVG図形+from/toアニメ+画像合成"),
    ProjectSpec("test22", ["test22_checkpoint.py"],
                doc="チェックポイントキャッシュ"),
    ProjectSpec("test23", ["test23_move_preserve.py"],
                doc="move保存: resize(force) + move + scale で move が消えない"),
    ProjectSpec("test24", ["test24_video_checkpoint.py"],
                doc="video checkpoint: 動画の transform-only → .webm 拡張子"),
    ProjectSpec("test25", ["test25_video_no_time.py"],
                doc="time()省略 → auto duration（trim(3)反映で duration=3）"),
    ProjectSpec("test26", ["test26_morph.py"], needs=("morph",),
                doc="morph_to: 画像→画像のモーフィング"),
    ProjectSpec("test27", ["test27_rotate.py"],
                doc="rotate Transform: 画像を30度回転（静的）"),
    ProjectSpec("test28", ["test28_rotate_to.py"],
                doc="rotate_to Effect: 0→180度回転アニメ + move保持"),
    ProjectSpec("test29", ["test29_web_bakeable.py"], needs=("web",),
                doc="web + bakeable(scale/fade): web変換後の checkpoint"),
    ProjectSpec("test30", ["test30_sin_scale.py"],
                doc="sin scale 中間最大 pad: 格子サンプリングで正しい pad サイズ"),
    ProjectSpec("test31", ["test31_crop.py"], doc="crop Transform"),
    ProjectSpec("test32", ["test32_pad.py"], doc="pad Transform"),
    ProjectSpec("test33", ["test33_blur.py"], doc="blur Transform"),
    ProjectSpec("test34", ["test34_eq.py"], doc="eq Transform"),
    ProjectSpec("test35", ["test35_wipe.py"], doc="wipe Effect"),
    ProjectSpec("test36", ["test36_zoom.py"], doc="zoom Effect"),
    ProjectSpec("test37", ["test37_color_shift.py"], doc="color_shift Effect"),
    ProjectSpec("test38", ["test38_subtitle_box.py"], needs=("web",),
                doc="subtitle_box Web テンプレート"),
    ProjectSpec("test39", ["test39_chroma_key.py"], doc="chroma_key Effect"),
    ProjectSpec("test40", ["test40_vignette.py"],
                doc="vignette Effect（strength=Expr → eval=frame）"),
    ProjectSpec("test41", ["test41_pixelize.py"], doc="pixelize Effect"),
    ProjectSpec("test42", ["test42_glow.py"],
                doc="glow Effect（split→gblur→blend=screen）"),
    ProjectSpec("test43", ["test43_lut.py"],
                doc="lut Effect（lut3d + LUTファイル）"),
    ProjectSpec("test44", ["test44_glitch.py"],
                doc="glitch Effect（rgbashift+noise、間欠enable）"),
    ProjectSpec("test45", ["test45_perspective.py"],
                doc="perspective_warp Effect"),
    ProjectSpec("test46", ["test46_lens.py"],
                doc="lens Effect（lenscorrection）"),
    ProjectSpec("test47", ["test47_ken_burns.py"],
                doc="ken_burns Effect（動的scale+crop）"),
    ProjectSpec("test48", ["test48_drop_shadow.py"],
                doc="drop_shadow Effect（split→色付け+gblur→overlay）"),
    ProjectSpec("test49", ["test49_outline.py"],
                doc="outline Effect（dilationベースの縁取り）"),
    ProjectSpec("test50", ["test50_slideshow.py"],
                doc="slideshow（xfade連結の合成Object）"),
    ProjectSpec("test51", ["test51_transition.py"],
                doc="transition（2Objectのxfade合成）"),
    ProjectSpec("test52", ["test52_text.py"], needs=("font",),
                doc="text Effect: drawtextエスケープ + x/y/size/alphaアニメ + box"),
    ProjectSpec("test53", ["test53_typewriter.py"], needs=("font",),
                doc="typewriter: 1文字ずつdrawtext + enable"),
    ProjectSpec("test54", ["test54_counter.py"], needs=("font",),
                doc="counter: %{eif}数値カウントアップ + format"),
    ProjectSpec("test55", ["test55_subtitles.py"],
                doc="subtitles: SRT字幕 + force_style"),
    ProjectSpec("test56", ["test56_audio_viz.py"],
                doc="audio_viz: showwaves可視化（キャッシュ生成物）"),
    ProjectSpec("test57", ["test57_audio_bake.py"],
                setup=lambda p: p.normalize_audio(-14),
                doc="audio_sequence + sfx（キャッシュ生成物） + normalize_audio",
                assets=("audio/効果音.mp3",)),
    ProjectSpec("test58", ["test58_audio_fx.py"],
                doc="loop(aloop) + duck_under(sidechaincompress)",
                assets=("audio/効果音.mp3",)),
    ProjectSpec("test59", ["test59_paths.py"],
                doc="move_along / path_bezier / throw / look_at のパスアニメ"),
    ProjectSpec("test60", ["test60_explode.py"], needs=("morph",),
                doc="explode_to: 粒子飛散（morph同機構でmkvキャッシュ生成物）"),
    ProjectSpec("test61", ["test61_assemble.py"], needs=("morph",),
                doc="assemble_from: 粒子集合（source消費+mkvキャッシュ生成物）"),
    ProjectSpec("test62", ["test62_group_grid.py"],
                doc="group（一括適用）+ grid/tile（グリッド複製）"),
    ProjectSpec("test63", ["test63_scene.py"],
                doc="scene: シーンの順次配置（シーン相対時刻）"),
    ProjectSpec("test64", ["test64_perlin.py"],
                doc="perlin ノイズによる手ブレ風 move"),
    ProjectSpec("test65", ["test65_chapters.py"],
                setup=lambda p: (p.marker(0, "イントロ"), p.marker(2.0, "本編"),
                                 p.marker(4.5, "まとめ")),
                doc="marker + チャプター（FFMETADATA埋め込み）"),
    ProjectSpec("test66", ["test66_window.py"],
                render_kwargs={"start": 1.5, "end": 4.0},
                doc="部分レンダ（時間窓 start=1.5, end=4.0）"),
    ProjectSpec("test67", ["test67_layer.py"], output="test67.gif",
                configure=_cfg(width=640, height=360, fps=15),
                doc="GIF出力（palettegen/paletteuse を1グラフで実行、音声なし）"),
    ProjectSpec("test68", ["test67_layer.py"], render_kwargs={"draft": True},
                doc="ドラフトレンダ（解像度半分・ultrafast・crf28）"),
    ProjectSpec("test69", ["test67_layer.py"],
                configure={"preset": "square", "background_color": "black"},
                doc="プリセット square（1080x1080）"),
    ProjectSpec("test70", ["test67_layer.py"], output="test70.webm",
                configure=_cfg(width=640, height=360),
                render_kwargs={"alpha": True},
                doc="透過webm出力（libvpx-vp9 yuva420p + 透明背景）"),
    ProjectSpec("test71", ["test67_layer.py"], output="test71.png",
                configure=_cfg(width=640, height=360, fps=15),
                doc="連番PNG出力（out_%05d.png / png rgba）"),
    ProjectSpec("test72", ["test67_layer.py"], output="test72.webp",
                configure=_cfg(width=640, height=360, fps=15),
                doc="アニメーションWebP出力"),
    ProjectSpec("test73", ["test67_layer.py"],
                configure={"preset": "shorts", "fps": 24,
                           "background_color": "navy"},
                doc="プリセット + 個別上書き（shorts の後に fps=24）"),
    ProjectSpec("test74", ["test74_nested.py"],
                doc="from_project: ネストコンポジション（サブProject→透過webm）"),
    ProjectSpec("test75", ["test75_mask.py"],
                doc="mask: 画像輝度をアルファとして乗算（movie= + alphamerge）"),
    ProjectSpec("test76", ["test76_mask_wipe.py"],
                doc="mask_wipe: マスク輝度しきい値ワイプ（movie= のタイムベース正規化）"),
    ProjectSpec("test77", ["test77_opacity.py"],
                doc="opacity: 定数(colorchannelmixer) + Expr(geq live)"),
    ProjectSpec("test78", ["test78_blend_mode.py"],
                doc="blend_mode: screen/multiply（blend+maskedmerge合成経路）"),
    ProjectSpec("test79", ["test79_pip_rounded.py"],
                assets=("video/flowerbg_noaudio.mp4",),
                doc="pip プリセット + rounded 角丸"),
    ProjectSpec("test80", ["test80_blur_bg_fill.py"],
                configure=_cfg(width=720, height=1280),
                doc="blur_background_fill: ぼかし背景敷き（キャンバス全面化）"),
    ProjectSpec("test81", ["test81_progress_bar.py"],
                doc="progress_bar: 動画全体の進行バー（geq + T/総尺）"),
    ProjectSpec("test82", ["test82_speed.py"],
                assets=("video/guitar_noaudio.mp4",),
                doc="speed: 再生速度（setpts + length()反映 + atempo自動追従）"),
    ProjectSpec("test83", ["test83_reverse_freeze.py"],
                assets=("video/guitar_noaudio.mp4",),
                doc="reverse + freeze_frame: 逆再生と一時停止（時間系liveサブグラフ）"),
    ProjectSpec("test84", ["test84_video_sequence.py"],
                assets=("video/flowerbg_noaudio.mp4",),
                doc="video_sequence: 動画クリップのxfade連結（キャッシュ生成物）"),
    ProjectSpec("test85", ["test85_narrate.py"], prepare=_mock_tts,
                needs=("font",),
                doc="narrate: TTS(モック)+字幕の同時生成・タイムライン同期"),
    ProjectSpec("test86", ["test86_karaoke.py"],
                doc="karaoke: ASS \\kタグ字幕をsubtitlesフィルタで合成"),
    ProjectSpec("test87", ["test87_slide.py"], needs=("web",),
                doc="slide: HTMLスライドのページ切替規約"),
    ProjectSpec("test88", ["test88_plugin_neon.py"], plugins=True,
                doc="プラグイン: neon_glow(split+gblur+blend複合) + scanline"),
    ProjectSpec("test89", ["test89_plugin_frame.py"], plugins=True,
                doc="プラグイン: photo_frame の pad 拡張が overlay 中央配置に反映"),
    ProjectSpec("test90", ["test90_plugin_autoload.py"],
                doc="プラグイン: tests/layers/plugins/ の自動読込（bakeable/live）"),
    ProjectSpec("test91", ["test91_formula.py"], needs=("web",),
                doc="formula: KaTeX数式の透過PNG化（fade/moveがそのまま効く）"),
    ProjectSpec("test94", ["test94_anchors.py"],
                doc="anchor 6値: 同じ x/y でも基準点ごとに overlay 座標が変わる"),
    # --- 以下は実レンダ専用（dry_run では踏めない経路） ---
    ProjectSpec("test92", ["test92_formula_scale.py"], snapshot=False,
                configure=_cfg(width=640, height=360, fps=15), needs=("web",),
                doc="formula + scale/rotate: pad(SEGVバリア)+copy を実レンダで踏む"),
    ProjectSpec("test93", ["test93_formula_thumb.py"], snapshot=False,
                configure=_cfg(width=640, height=360, fps=15), needs=("web",),
                doc="formula + live ops のみ（ベイク無しの数式PNG生成）"),
    ProjectSpec("test95", ["test95_long_filter.py"], snapshot=False,
                configure=_cfg(width=320, height=180, fps=10), needs=("font",),
                doc="長大フィルタの一時ファイル外部化（-/filter_complex）を実レンダで踏む"),
]

PROJECTS = {spec.name: spec for spec in _SPECS}

# スナップショット対象（dry_run で比較する）名前の一覧
SNAPSHOT_NAMES = [s.name for s in _SPECS if s.snapshot]

# 実レンダの選抜リスト: dry_run では原理的に踏めない経路だけを常時対象にする
# （フル実行は tests/test_real_render.py の --realrender-all）。
# 各項目の根拠は CLAUDE.md §4 の FFmpeg 地雷に対応する:
#   test92 … formula の寸法が dry_run では不明 → scale の pad/copy(SEGVバリア)
#   test76 … mask_wipe の movie= 入力のタイムベース正規化（framesync 実挙動）
#   test77 … start>0 の入力に入る tpad + trim の順序（実フレームでしか出ない）
#   test95 … 4000字超フィルタの一時ファイル外部化（実行時にしか起きない）
REAL_RENDER_SELECTION = ["test76", "test77", "test92", "test95"]
