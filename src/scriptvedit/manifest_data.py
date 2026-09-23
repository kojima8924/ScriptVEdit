# -*- coding: utf-8 -*-
"""describe（ケイパビリティ・マニフェスト）の手書き補助テーブル。

ここは**純データ**だけを置く。導出ロジック（inspect による自動導出・エントリ
組み立て・kind/name の絞り込み・Markdown 整形）は manifest.py にある。

scriptvedit 内の import は state.py（パッケージ内 import ゼロの葉）から語彙を
1つ借りるだけに留めること。ここに依存を足すと「データとロジックの分離」が
崩れ、循環 import の芽にもなる。

dict のキー順序は describe の出力順に直結する。**定義順を変えないこと。**
"""

from scriptvedit.state import _AUDIO_VIZ_KINDS

MANIFEST_VERSION = "1.1"

# 色として扱うパラメータ名（型の自動判定に使う）
_MANIFEST_COLOR_PARAMS = {
    "color", "fill", "bg", "border_color", "box_color", "background_color",
    "bg_color", "shadow_color", "outline_color",
}

# カテゴリ（日本語）: カテゴリ名 -> そのカテゴリに属する公開名
_MANIFEST_CATEGORY_MEMBERS = {
    "変形": ["resize", "rotate", "crop", "pad", "blur", "eq", "grid"],
    "視覚効果": [
        "fade", "wipe", "zoom", "color_shift", "shake", "chroma_key", "vignette",
        "pixelize", "glow", "lut", "glitch", "perspective_warp", "lens",
        "ken_burns", "drop_shadow", "outline",
    ],
    "変形効果": ["scale", "move", "rotate_to", "move_along", "path_bezier",
                 "throw", "inertia", "look_at"],
    "合成": ["mask", "mask_wipe", "opacity", "blend_mode", "rounded", "pip",
             "blur_background_fill", "progress_bar"],
    "時間操作": ["speed", "reverse", "freeze_frame", "trim", "delete",
                 "atrim", "atempo", "adelete"],
    "生成効果": ["morph_to", "explode_to", "assemble_from"],
    "テキスト・字幕": ["text", "typewriter", "counter", "subtitles", "karaoke",
                       "subtitle", "subtitle_box", "bubble", "diagram"],
    "数式": ["formula", "formula_lines"],
    "オーディオ": ["avolume", "duck_under", "loop", "audio_sequence",
                   "sfx", "audio_viz", "voice", "narrate", "normalize_audio"],
    "シーケンス生成": ["slideshow", "transition", "video_sequence", "slide"],
    "同期・タイムライン": ["anchor", "pause", "scene", "beat_sync", "marker"],
    "グループ": ["group", "tile"],
    "図形ビルダー": ["circle", "rect", "arrow", "label", "spotlight"],
    "ノイズ": ["perlin"],
}
_MANIFEST_CATEGORIES = {}
for _cat, _members in _MANIFEST_CATEGORY_MEMBERS.items():
    for _m in _members:
        _MANIFEST_CATEGORIES[_m] = _cat
del _cat, _members, _m

# docstring を持たない公開関数の要約（自動導出できない分のみ宣言）
_MANIFEST_SUMMARIES = {
    "Project": "プロジェクト（レイヤーを束ねて1本の動画にレンダリングする最上位オブジェクト）",
    "Object": "素材オブジェクト（画像/動画/音声/HTML)。<= 演算子で Transform/Effect を適用する",
    "Transform": "静的変形（時間非依存。素材そのものを変形する）",
    "Effect": "時間依存エフェクト（u=0..1 の進行度で変化する）",
    "resize": "リサイズTransform。sx/sy は倍率（1.0=等倍）",
    "scale": "拡大縮小Effect（時間変化可）。zoom のベース",
    "fade": "フェードEffect。alpha=不透明度 0〜1（Expr/lambda 可）",
    "move": "移動Effect。x/y は 0〜1 の相対座標（Expr/lambda 可）。from_/to_ 指定で自動 lerp",
    "lerp": "線形補間 a + (b - a) * t",
    "clip": "値を lo〜hi に制限する",
    "clamp": "clip の別名",
    "step": "x >= edge で 1、それ以外 0",
    "smoothstep": "edge0〜edge1 の間を滑らかに 0→1 補間",
    "mod": "剰余 a mod b",
    "frac": "小数部を返す",
    "cbrt": "立方根",
    "deg2rad": "度→ラジアン変換",
    "rad2deg": "ラジアン→度変換",
    # ffmpeg 式へそのまま落ちる数学関数（expr.py。docstring を持たない）。
    # 角度の単位はすべてラジアン（度で書きたいときは deg2rad を挟む）
    "sin": "正弦 sin(x)（x はラジアン）",
    "cos": "余弦 cos(x)（x はラジアン）",
    "tan": "正接 tan(x)（x はラジアン）",
    "asin": "逆正弦 asin(x)（戻り値はラジアン）",
    "acos": "逆余弦 acos(x)（戻り値はラジアン）",
    "atan": "逆正接 atan(x)（戻り値はラジアン）",
    "atan2": "2引数の逆正接 atan2(y, x)（戻り値はラジアン）",
    "sinh": "双曲線正弦 sinh(x)",
    "cosh": "双曲線余弦 cosh(x)",
    "tanh": "双曲線正接 tanh(x)",
    "exp": "指数関数 e^x",
    "log": "自然対数 ln(x)",
    "log10": "常用対数 log10(x)",
    "sqrt": "平方根 √x",
    "pow": "べき乗 a^b",
    "abs": "絶対値 |x|",
    "floor": "床関数（x 以下の最大の整数）",
    "ceil": "天井関数（x 以上の最小の整数）",
    "trunc": "0 方向への切り捨て（負数は floor と異なる）",
    "round": "四捨五入（ffmpeg 式では floor(x+0.5) 相当）",
    "min": "2値の小さい方",
    "max": "2値の大きい方",
    # docstring を持たない Project メソッド（describe が summary 空になっていた）
    "Project.configure": "出力設定（画面サイズ・fps・背景色・プリセット等）をまとめて指定する。",
    "Project.render": "タイムラインを1本の動画へ書き出す（dry_run=True なら ffmpeg コマンドだけ返す）。",
    # クラスエントリの methods 一覧はメソッド名だけで引かれる
    "configure": "出力設定（画面サイズ・fps・背景色・プリセット等）をまとめて指定する。",
    "render": "タイムラインを1本の動画へ書き出す（dry_run=True なら ffmpeg コマンドだけ返す）。",
    "Group": "複数Objectをまとめて同一Transform/Effectを一括適用するプロキシ。",
}

# 要約（1行目）だけでは足りない補足。docstring が無い／短い名前にだけ宣言する
# （docstring がある場合は自動で details に載るのでここへは書かない）。
_MANIFEST_DETAILS = {
    "Project.configure": (
        "未知のキーは difflib の「もしかして」つきで ValueError。\n"
        "preset を指定すると width/height/fps がまとめて設定される"
        "（個別指定が優先）。"),
    "Project.render": (
        "dry_run=True の戻り値は必ず {'main': [...], 'cache': {...}} の dict。\n"
        "start/end で時間範囲を切り出し、draft=True で低品質高速プレビュー、\n"
        "alpha=True で透過 webm（from_project のサブレンダに使う）、\n"
        "strict=True で audit() の warning を RuntimeError にする。"),
}

# __all__ にある非callableの公開定数（describe の callable 判定から漏れていた）
_MANIFEST_CONSTANTS = {
    "pause": {
        "category": "タイムライン",
        "summary": "無音・無映像の時間を挿入するファクトリ（タイムラインを進める）。",
        "signature": "pause.time(seconds) / pause.until(name, offset=0.0)",
        "details": "pause.time(3) で3秒空ける。pause.until('mark.end') は"
                   "アンカー時刻まで空ける。a >> pause.time(1) >> b のように"
                   "直後連結の間にも挟める。",
        "example": "pause.time(2)\npause.until('intro.end')",
    },
    "PI": {
        "category": "定数",
        "summary": "円周率 π（Expr の式でそのまま使える float 定数）。",
        "signature": "PI",
        "details": "Python の math.pi と同値。Expr の中で math.sin 等を使わず、"
                   "scriptvedit の sin/cos と PI を組み合わせること。",
    },
    "E": {
        "category": "定数",
        "summary": "自然対数の底 e（Expr の式でそのまま使える float 定数）。",
        "signature": "E",
    },
    "P": {
        "category": "定数",
        "summary": "パーセント記法のヘルパ。`50 % P` で 0.5 を表す。",
        "signature": "P",
        "details": "x=50 % P は x=0.5 と同じ（演算子は剰余の % を流用している。"
                   "`50 * P` ではない）。画面比率を「％で書きたい」ときの糖衣。",
        "example": "obj <= move(x=50 % P, y=80 % P)",
    },
}

# イージング名の日本語化パーツ（30種の要約を自動生成するため）
_MANIFEST_EASE_CURVES = {
    "quad": "2次", "cubic": "3次", "quart": "4次", "quint": "5次",
    "sine": "サイン", "expo": "指数", "circ": "円", "back": "バック（行き過ぎて戻る）",
    "elastic": "弾性（ばね振動）", "bounce": "バウンド（跳ね返り）",
}
_MANIFEST_EASE_DIRS = {"in": "イーズイン（加速）", "out": "イーズアウト（減速）",
                       "in_out": "イーズインアウト（加速→減速）"}

# パラメータのメタ情報の上書き（型/説明/範囲/choices）。
# 自動導出（既定値の型・_resolve_param の有無）で足りない箇所だけを宣言する。
_MANIFEST_PARAM_META = {
    # **kwargs のためシグネチャから導出できないもの
    ("resize", "sx"): {"type": "number", "default": 1, "desc": "横倍率（1.0=等倍）"},
    ("resize", "sy"): {"type": "number", "default": 1, "desc": "縦倍率（1.0=等倍）"},
    ("move", "x"): {"type": "expr", "default": 0.5,
                    "desc": "X座標 0〜1 の相対位置（Expr/lambda 可）"},
    ("move", "y"): {"type": "expr", "default": 0.5,
                    "desc": "Y座標 0〜1 の相対位置（Expr/lambda 可）"},
    ("move", "from_x"): {"type": "number", "default": None, "desc": "開始X（to_x と併用で自動 lerp）"},
    ("move", "from_y"): {"type": "number", "default": None, "desc": "開始Y（to_y と併用で自動 lerp）"},
    ("move", "to_x"): {"type": "number", "default": None, "desc": "終了X"},
    ("move", "to_y"): {"type": "number", "default": None, "desc": "終了Y"},
    # choices は実装（filters/video.py の _ANCHOR_OFFSETS）が実際に区別できる
    # 値だけを載せる。語彙は state.py の _PLACEMENT_ANCHORS が正
    ("move", "anchor"): {"type": "choice", "default": "center",
                         "choices": None,   # → state.py の _PLACEMENT_ANCHORS
                         "desc": "座標の基準点。center=中心 / topleft=左上の角 / "
                                 "left・right・top・bottom=その辺の中点"},
    # trim/atrim は「素材時間の切り出し」。start=2, duration=3 なら素材の 2〜5 秒
    ("trim", "duration"): {"type": "number", "default": None, "min": 0,
                           "desc": "出力する尺（秒。省略時は素材末尾まで）"},
    ("trim", "start"): {"type": "number", "default": 0, "min": 0,
                        "desc": "素材のイン点（秒。キーワード専用）"},
    ("atrim", "duration"): {"type": "number", "default": None, "min": 0,
                            "desc": "出力する尺（秒。省略時は素材末尾まで）"},
    ("atrim", "start"): {"type": "number", "default": 0, "min": 0,
                         "desc": "素材のイン点（秒。キーワード専用）"},
    ("subtitle", "duration"): {"type": "number", "default": 2.5, "min": 0,
                               "desc": "表示尺（秒）。第2位置引数はこれ"},
    ("subtitle", "who"): {"type": "string", "default": None,
                          "desc": "話者ラベル（キーワード専用）"},
    ("bubble", "tail"): {"type": "any", "default": None,
                         "desc": "尻尾が指す位置 (x, y)（0..1 の画面比率。旧名 anchor）"},
    ("grid", "cols"): {"type": "int", "default": None, "required": True, "desc": "列数"},
    ("grid", "rows"): {"type": "int", "default": None, "required": True, "desc": "行数"},
    ("grid", "gap"): {"type": "int", "default": 0, "desc": "セル間の余白px"},
    # 数式（KaTeX同梱・透過PNG化）
    ("formula", "latex"): {"type": "string", "required": True,
                           "desc": "LaTeX 数式（r'...' 推奨。KaTeX のサポート範囲）"},
    ("formula", "size"): {"type": "number", "default": 48, "min": 1, "max": 2000,
                          "desc": "基準フォントサイズpx"},
    ("formula", "color"): {"type": "color", "default": "white",
                           "desc": "文字色（CSSカラー）"},
    ("formula", "display"): {"type": "bool", "default": True,
                             "desc": "True=別行立て(displayMode) / False=インライン"},
    ("formula", "duration"): {"type": "number", "default": None, "min": 0.01,
                              "desc": "表示秒数（省略時は .time(秒) で指定）"},
    ("formula", "padding"): {"type": "number", "default": 4, "min": 0, "max": 500,
                             "desc": "数式まわりの余白px（切り出しbboxに含まれる）"},
    ("formula", "align"): {"type": "choice", "default": "left",
                           "choices": ["left", "center", "right"],
                           "desc": "複数行時の揃え"},
    ("formula_lines", "latex_lines"): {"type": "list", "required": True,
                                       "desc": "LaTeX 数式のリスト（縦に並べる）"},
    ("formula_lines", "gap"): {"type": "number", "default": 12, "min": 0, "max": 2000,
                               "desc": "行間px"},
    ("formula_lines", "align"): {"type": "choice", "default": "left",
                                 "choices": ["left", "center", "right"],
                                 "desc": "行の揃え"},
    ("formula_lines", "duration"): {"type": "number", "default": None, "min": 0.01,
                                    "desc": "表示秒数（省略時は .time(秒) で指定）"},
    # 既定値が None のため型を推定できない引数
    ("speed", "factor"): {"type": "number", "min": 0.1, "desc": "再生速度倍率（2.0で2倍速）"},
    ("zoom", "from_value"): {"type": "number", "desc": "開始スケール"},
    ("zoom", "to_value"): {"type": "number", "desc": "終了スケール"},
    ("freeze_frame", "at"): {"type": "number", "required": True,
                             "desc": "静止させるフレームの時刻（秒・オブジェクト先頭基準）"},
    ("freeze_frame", "duration"): {"type": "number", "required": True,
                                   "desc": "静止させる長さ（秒。実効尺がこの分伸びる）"},
    ("opacity", "value"): {"type": "expr", "desc": "不透明度 0〜1（Expr/lambda 可）"},
    ("rounded", "radius"): {"type": "int", "min": 0, "max": 4096, "desc": "角の半径px（0で無効）"},
    ("text", "font"): {"type": "string", "desc": "フォントファイルパス（省略時はOS別の日本語フォント候補から自動選択。環境変数 SCRIPTVEDIT_FONT で上書き可）"},
    # 縁取り・影（既定値では drawtext オプションを一切出力しない）
    ("text", "border"): {"type": "int", "default": 0, "min": 0,
                         "desc": "縁取り（アウトライン）の太さpx（0で無効）"},
    ("text", "shadow"): {"type": "any", "default": [0, 0],
                         "desc": "影のオフセット (x, y) px（(0,0)で無効。例 (2, 2)）"},
    ("typewriter", "border"): {"type": "int", "default": 0, "min": 0,
                               "desc": "縁取りの太さpx（0で無効）"},
    ("typewriter", "shadow"): {"type": "any", "default": [0, 0],
                               "desc": "影のオフセット (x, y) px（(0,0)で無効）"},
    ("counter", "border"): {"type": "int", "default": 0, "min": 0,
                            "desc": "縁取りの太さpx（0で無効）"},
    ("counter", "shadow"): {"type": "any", "default": [0, 0],
                            "desc": "影のオフセット (x, y) px（(0,0)で無効）"},
    ("narrate", "border"): {"type": "int", "default": 0, "min": 0,
                            "desc": "字幕の縁取り太さpx（text() と同じ。読みやすさ向上に有効）"},
    ("narrate", "shadow"): {"type": "any", "default": [0, 0],
                            "desc": "字幕の影オフセット (x, y) px（text() と同じ）"},
    ("narrate", "subtitle_text"): {"type": "string", "default": None,
                                     "desc": "読み上げ文とは別の字幕表示文"},
    ("narrate", "subtitle_formatter"): {"type": "any", "default": None,
                                          "desc": "字幕文を受けて文字列を返すcallable"},
    ("narrate", "subtitle_max_chars"): {"type": "int", "default": None,
                                          "min": 1, "desc": "日本語禁則折り返しの1行文字数"},
    ("narrate", "subtitle_max_lines"): {"type": "int", "default": None,
                                          "min": 1, "desc": "字幕の最大行数（超過時はエラー）"},
    ("narrate", "subtitle_safe_area"): {"type": "any", "default": None,
                                          "desc": "字幕を収める画面比率マージン"},
    ("normalize_audio", "true_peak"): {"type": "number", "default": -1.5,
                                          "min": -9, "max": 0,
                                          "desc": "最終lossy音声のtrue peak目標(dBTP)。内部で0.5dB余裕を確保"},
    ("normalize_audio", "lra"): {"type": "number", "default": 11,
                                    "min": 1, "max": 50, "desc": "目標LRA(LU)"},
    ("normalize_audio", "limiter"): {"type": "bool", "default": True,
                                        "desc": "loudnorm後のピークリミッター"},
    ("normalize_audio", "sample_rate"): {"type": "int", "default": 48000,
                                            "min": 8000, "max": 384000,
                                            "desc": "最終音声sample rate。Noneで自動"},
    ("typewriter", "font"): {"type": "string", "desc": "フォントファイルパス（省略時は自動選択）"},
    ("counter", "font"): {"type": "string", "desc": "フォントファイルパス（省略時は自動選択）"},
    # choices（実装の検証コードと同じ集合を参照する）
    ("wipe", "direction"): {"type": "choice", "choices": ["left", "right", "up", "down"]},
    ("blend_mode", "mode"): {"type": "choice", "choices": None},   # None → enums から解決
    ("slideshow", "transition"): {"type": "choice", "choices": None},
    # transition は Object のみ受ける（実装は文字列パスを TypeError で拒否）
    ("transition", "obj_a"): {"type": "object", "required": True,
                              "desc": "前半の Object（Transform/Effect 未適用の素材）"},
    ("transition", "obj_b"): {"type": "object", "required": True,
                              "desc": "後半の Object（Transform/Effect 未適用の素材）"},
    ("transition", "kind"): {"type": "choice", "choices": None},
    ("video_sequence", "transition"): {"type": "choice", "choices": None},
    ("steps", "jump"): {"type": "choice", "choices": ["start", "end"]},
    # --- 単位が曖昧で誤用しても ffmpeg が正常終了するパラメータ ---
    # 「画面比率か px か」「+y の向き」「0..1 か 0..255 か」「秒かフレームか」を
    # 最優先で明記する（throw(vx=200) を px のつもりで書くと被写体が飛ぶ）
    ("throw", "vx"): {"type": "number", "required": True,
                      "desc": "初速の横成分（画面幅に対する比率/正規化時間。px ではない）"},
    ("throw", "vy"): {"type": "number", "required": True,
                      "desc": "初速の縦成分（画面高に対する比率。+y は画面下向き）"},
    ("throw", "gravity"): {"type": "number", "default": 1.0,
                           "desc": "重力加速度（画面高に対する比率。+ で下へ加速）"},
    ("throw", "x0"): {"type": "number", "default": 0.5, "desc": "開始X（画面比率 0〜1）"},
    ("throw", "y0"): {"type": "number", "default": 0.5, "desc": "開始Y（画面比率 0〜1）"},
    ("throw", "anchor"): {"type": "choice", "default": "center", "choices": None,
                          "desc": "座標の基準点（move と同じ語彙）"},
    ("inertia", "vx"): {"type": "number", "required": True,
                        "desc": "初速の横成分（画面幅に対する比率。px ではない）"},
    ("inertia", "vy"): {"type": "number", "required": True,
                        "desc": "初速の縦成分（画面高に対する比率。+y は画面下向き）"},
    ("inertia", "damping"): {"type": "number", "default": 3.0, "min": 0,
                             "desc": "減衰係数（大きいほど早く止まる。無次元）"},
    ("inertia", "x0"): {"type": "number", "default": 0.5, "desc": "開始X（画面比率 0〜1）"},
    ("inertia", "y0"): {"type": "number", "default": 0.5, "desc": "開始Y（画面比率 0〜1）"},
    ("inertia", "anchor"): {"type": "choice", "default": "center", "choices": None,
                            "desc": "座標の基準点（move と同じ語彙）"},
    ("move_along", "points"): {"type": "any", "required": True,
                               "desc": "[(x, y), ...] の座標列（画面比率 0〜1。px ではない）"},
    ("move_along", "easing"): {"type": "any", "default": None,
                               "desc": "u の再マッピング関数（省略時は等速）"},
    ("move_along", "anchor"): {"type": "choice", "default": "center", "choices": None,
                               "desc": "座標の基準点（move と同じ語彙）"},
    ("path_bezier", "anchor"): {"type": "choice", "default": "center", "choices": None,
                                "desc": "座標の基準点（move と同じ語彙）"},
    ("perspective_warp", "x0"): {"type": "number", "required": True,
                                 "desc": "左上隅の移動先X（px。画面比率ではない）"},
    ("perspective_warp", "y0"): {"type": "number", "required": True,
                                 "desc": "左上隅の移動先Y（px。+y は画面下向き）"},
    ("perspective_warp", "x1"): {"type": "number", "required": True,
                                 "desc": "右上隅の移動先X（px）"},
    ("perspective_warp", "y1"): {"type": "number", "required": True,
                                 "desc": "右上隅の移動先Y（px）"},
    ("perspective_warp", "x2"): {"type": "number", "required": True,
                                 "desc": "左下隅の移動先X（px）"},
    ("perspective_warp", "y2"): {"type": "number", "required": True,
                                 "desc": "左下隅の移動先Y（px）"},
    ("perspective_warp", "x3"): {"type": "number", "required": True,
                                 "desc": "右下隅の移動先X（px）"},
    ("perspective_warp", "y3"): {"type": "number", "required": True,
                                 "desc": "右下隅の移動先Y（px）"},
    ("shake", "amplitude"): {"type": "number", "default": 0.02, "min": 0,
                             "desc": "振れ幅（画面サイズに対する比率。px ではない）"},
    ("shake", "frequency"): {"type": "number", "default": 10, "min": 0,
                             "desc": "振動回数（表示尺全体を 1 とした回数。Hz ではない）"},
    ("scale", "value"): {"type": "expr", "default": 1, "min": 0,
                         "desc": "拡大率（1.0=等倍。Expr/lambda で時間変化可）"},
    ("blur", "radius"): {"type": "number", "default": 5, "min": 0,
                         "desc": "ガウスぼかしの sigma（px 相当。大きいほど強い）"},
    ("duck_under", "ratio"): {"type": "number", "default": 8, "min": 1,
                              "desc": "圧縮比（大きいほど深く下がる。無次元）"},
    ("duck_under", "threshold"): {"type": "number", "default": 0.05,
                                  "min": 0, "max": 1,
                                  "desc": "動作を始める入力レベル（0〜1 の振幅。dB ではない）"},
    ("duck_under", "attack"): {"type": "number", "default": 20, "min": 0,
                               "desc": "音量を下げ始める速さ（ミリ秒）"},
    ("duck_under", "release"): {"type": "number", "default": 250, "min": 0,
                                "desc": "音量を戻す速さ（ミリ秒）"},
    # Project.configure(**kwargs) の各キー（_CONFIGURE_KEYS。**kwargs なので
    # シグネチャからは導出できず、宣言しないと params が空になる）
    ("Project.configure", "width"): {
        "type": "int", "default": 1920, "min": 1, "desc": "出力の横px"},
    ("Project.configure", "height"): {
        "type": "int", "default": 1080, "min": 1, "desc": "出力の縦px"},
    ("Project.configure", "fps"): {
        "type": "number", "default": 30, "min": 1, "desc": "フレームレート"},
    ("Project.configure", "duration"): {
        "type": "number", "default": None, "min": 0,
        "desc": "総尺（秒）。省略時はタイムラインから自動決定"},
    ("Project.configure", "background_color"): {
        "type": "ffcolor", "default": "black", "desc": "背景色（ffcolor形式）"},
    ("Project.configure", "preset"): {
        "type": "choice", "default": None, "choices": None,
        "desc": "画面サイズ/fpsの一括指定（個別指定が優先）"},
    ("Project.configure", "encoder"): {
        "type": "choice", "default": None, "choices": None,
        "desc": "映像エンコーダ。利用不可なら警告つきで libx264 へフォールバック"},
    ("Project.configure", "parallel"): {
        "type": "int", "default": None, "min": 1,
        "desc": "キャッシュ生成の並列数（時間分割並列は render(parallel=)）"},
    ("Project.configure", "draft_web_fps"): {
        "type": "number", "default": None, "min": 1,
        "desc": "draft レンダ時の web クリップの fps 上限"},
    # choices は実装（audio.py の audio_viz）と同じ集合を参照する
    ("audio_viz", "kind"): {"type": "choice", "choices": list(_AUDIO_VIZ_KINDS),
                            "desc": "可視化方式。waves=波形 / spectrum=スペクトログラム "
                                    "/ cqt=定Q変換（音階表示）"},
    # 定数しか受け付けない（式にすると FFmpeg 8 で SEGV）
    ("text", "size"): {"type": "int", "desc": "文字サイズpx（定数のみ。式/lambda 不可）"},
    ("typewriter", "size"): {"type": "int", "desc": "文字サイズpx（定数のみ。式/lambda 不可）"},
    ("counter", "size"): {"type": "int", "desc": "文字サイズpx（定数のみ。式/lambda 不可）"},
    ("text", "content"): {"type": "string", "required": True, "desc": "表示テキスト（%/:/' は自動エスケープ）"},
    ("lut", "file"): {"type": "string", "required": True, "desc": ".cube LUT ファイルパス"},
    ("mask", "image_path"): {"type": "string", "required": True,
                             "desc": "マスク画像パス（輝度をアルファに乗算）"},
    ("mask_wipe", "image_path"): {"type": "string", "required": True,
                                  "desc": "グラデーション画像パス（掃引マスク）"},
    ("subtitles", "srt_file"): {"type": "string", "required": True, "desc": ".srt ファイルパス"},
    # 実装（effects/terminal.py）は Object 以外を TypeError で拒否する。
    # 文字列パスは受け付けない（Object(...) で包んでから渡す）
    ("morph_to", "target"): {"type": "object", "required": True,
                             "desc": "モーフ先の画像 Object（パス文字列は不可: "
                                     "Object('target.png') で包む）"},
    ("assemble_from", "source"): {"type": "object", "required": True,
                                  "desc": "集合元の画像 Object（パス文字列は不可: "
                                          "Object('src.png') で包む）"},
    ("narrate", "text_content"): {"type": "string", "required": True, "desc": "読み上げテキスト"},
    ("voice", "text"): {"type": "string", "required": True, "desc": "読み上げテキスト"},
    # TTS バックエンド（None で自動選択: env SCRIPTVEDIT_TTS_BACKEND → VOICEVOX 起動判定 → edge）
    ("voice", "backend"): {"type": "choice", "default": None,
                           "choices": ["voicevox", "edge", "sapi"],
                           "desc": "TTSバックエンド（voicevox=要エンジン起動・オフライン / "
                                   "edge=pip install edge-tts・オンライン必須 / "
                                   "sapi=Windows標準）。None で自動選択"},
    ("narrate", "backend"): {"type": "choice", "default": None,
                             "choices": ["voicevox", "edge", "sapi"],
                             "desc": "TTSバックエンド（voice と同じ。None で自動選択）"},
    ("voice", "speaker"): {"type": "any", "default": None,
                           "desc": "話者（voicevox=数値ID / edge=音声名 例 ja-JP-NanamiNeural / "
                                   "sapi=音声名）。None で各バックエンドの既定"},
    ("narrate", "speaker"): {"type": "any", "default": None,
                             "desc": "話者（voice と同じ。None で各バックエンドの既定）"},
    ("slide", "html_file"): {"type": "string", "required": True, "desc": "HTMLファイルパス"},
    ("beat_sync", "audio_source"): {"type": "string", "required": True, "desc": "音声ファイルパス"},
}

# エントリごとの注記（AI が踏みがちな地雷。constraints の該当分をここにも展開する）
_MANIFEST_NOTES = {
    "Project.param": [
        "型は default から推論する（int/float/bool/str）。解釈できない値は"
        "既定値へ黙って戻さず ValueError",
        "どの p.param() にも読まれなかった --param は誤記として ValueError"
        "（SCRIPTVEDIT_PARAM_* 由来は共有されうるので警告）",
        "`--param n=v` と `--param=n=v` は同値。`=` の無い指定は ValueError",
    ],
    "group": [
        "返り値は Group（Object ではない）",
        "time(N) は各メンバーを**順次配置**するのでグループ全体の尺は N 倍になる。"
        "同時に重ねたいときは stack(N) を使う",
    ],
    "text": ["size は定数のみ。lambda/Expr を渡すと FFmpeg 8 で SEGV するため拒否される",
             "x/y/alpha は Expr/lambda 可（アニメーション可能）",
             "border=2 の縁取りや shadow=(2, 2) の影で細い文字の可読性を上げられる"],
    "typewriter": ["size は定数のみ（text と同じ制約）"],
    "counter": ["size は定数のみ（text と同じ制約）"],
    "reverse": ["実効尺は最大30秒（全フレームをメモリに保持するため）。超えると ValueError",
                "live Effect（bakeable ではない）"],
    "speed": ["映像の実効尺が 元尺/factor になる（length()/自動尺に反映）",
              "音声側の尺合わせに atempo/atrim が自動付与される"],
    "freeze_frame": ["live Effect。指定時刻のフレームを duration 秒引き伸ばす（実効尺が伸びる）"],
    "blend_mode": ["キャンバス全面へパドしてから blend する前提（オブジェクト単位の局所合成ではない）",
                   "live Effect（bakeable 不可）"],
    "morph_to": ["bakeable ops の末尾に1つだけ置ける（終端フレーム生成Effect）",
                 "target は Object のみ（パス文字列は TypeError）。画像 media_type 限定",
                 "target に Transform/Effect が付いていると ValueError"
                 "（生成処理は素の source しか読まないため）"],
    "explode_to": ["bakeable ops の末尾に1つだけ置ける（終端フレーム生成Effect）"],
    "assemble_from": ["bakeable ops の末尾に1つだけ置ける（終端フレーム生成Effect）",
                      "source は Object のみ（パス文字列は TypeError）。画像 media_type 限定",
                      "source に Transform/Effect が付いていると ValueError"],
    "rotate": ["時間依存の式（u を含む式）は不可。時間変化する回転は rotate_to() を使う"],
    "scale": ["pad サイズ決定のため、u のみに依存する数値評価可能な式であること"],
    "narrate": ['backend="voicevox"（既定候補）は VOICEVOX（別プロセス）の起動が必要',
                'backend="edge" なら pip install edge-tts で使える（オンライン必須）',
                "backend=None は自動選択（VOICEVOX 起動中なら voicevox、無ければ edge）",
                "subtitle_textで読み上げと表示文を分離でき、subtitle_max_charsは日本語禁則対応",
                "subtitle_safe_areaは領域に収まる字幕矩形の位置を画面内へクランプする"],
    "duck_under": ["sidechainは自動で無音延長され、other終了後もBGMは指定尺まで続く"],
    "audio_sequence": ["返却Objectのdurationは連結後の実尺へ自動設定される",
                       "Narrationを渡すと字幕もcrossfade込みで配置され、数値@へ追従する"],
    "voice": ['backend="voicevox"（既定候補）は VOICEVOX（別プロセス）の起動が必要',
              'backend="edge" なら pip install edge-tts で使える（オンライン必須）',
              "speaker の意味はバックエンドごとに違う（数値ID / 音声名）"],
    "beat_sync": ["scipy が必要（未インストールなら ImportError）"],
    "slide": ["HTML レンダリングに web 経路（Playwright 等）を使う"],
    "lut": [".cube 形式のみ"],
    "subtitles": ["SRT の文字コードは UTF-8"],
}

# エントリごとの最小例
_MANIFEST_EXAMPLES = {
    "formula": ("eq = formula(r'\\sum_{k=1}^{n} k = \\frac{n(n+1)}{2}', size=64, color='white')\n"
                "eq.time(4) <= fade(lambda u: u) & move(x=0.5, y=0.4, anchor='center')"),
    "formula_lines": ("formula_lines([r'a^2 + b^2 = c^2', r'c = \\sqrt{a^2 + b^2}'], "
                      "size=48, gap=16).time(5)"),
    "fade": "img.time(3) <= fade(lambda u: u)          # 3秒かけてフェードイン",
    "scale": "img <= scale(lambda u: lerp(1.0, 1.5, u))",
    "zoom": "img <= zoom(from_value=1.0, to_value=1.4)",
    "move": "img <= move(x=lambda u: lerp(0.2, 0.8, u), y=0.5, anchor='center')",
    "move_along": "img <= move_along([(0.1, 0.5), (0.5, 0.2), (0.9, 0.5)], easing=ease_in_out_cubic)",
    "rotate_to": "img <= rotate_to(from_deg=0, to_deg=360)",
    "resize": "img <= resize(sx=0.3, sy=0.3)",
    "crop": "img <= crop(x=0, y=0, w=640, h=360)",
    "wipe": "img <= wipe(direction='left')",
    "text": "t = text('こんにちは', x=0.5, y=0.2, size=48, color='white')\nt.time(3) <= fade(lambda u: u)",
    "typewriter": "typewriter('タイプ表示', cps=12).time(4)",
    "counter": "counter(0, 100, format='%d%%').time(3)",
    "subtitles": "subtitles('subs.srt', style={'size': 36})",
    "blend_mode": "obj <= blend_mode('screen')",
    "mask": "obj <= mask('mask_circle.png')",
    "opacity": "obj <= opacity(0.5)",
    "speed": "clip_.time(4) <= speed(2.0)   # 2倍速",
    "reverse": "clip_ <= reverse()",
    "morph_to": "img <= morph_to(Object(asset('images/target.png')))",
    "slideshow": "slideshow(['a.png', 'b.png', 'c.png'], each=3.0, transition='fade')",
    "transition": "transition(obj_a, obj_b, kind='wipeleft', duration=1.0)",
    "keyframes": "img <= scale(keyframes((0, 1.0), (0.5, 1.5), (1, 1.0), easing=ease_in_out_sine))",
    "avolume": "bgm <= avolume(0.3)",
    "duck_under": "bgm <= duck_under(voice_obj, ratio=8)",
    "sfx": "sfx('効果音.mp3', at=2.5, volume=0.8)",
    "narrate": ("n = narrate('長い読み上げ原稿', speaker=1, subtitle_text='短い字幕', "
                "subtitle_max_chars=14, subtitle_safe_area=0.05)"),
    "group": "g = group(obj_a, obj_b)\ng <= move(x=lambda u: u)",
    "pip": "video <= pip(x=0.75, y=0.75, scale=0.3, radius=12)",
    "anchor": "obj.time(3, name='intro')\npause.until('intro.end')",
    "scene": "with scene('導入', 5.0):\n    ...",
}

# 既知の制約・落とし穴（トップレベル constraints）
_MANIFEST_CONSTRAINTS = [
    {
        # DSL で最も重要な時間規則。project.py の _resolve_anchors は
        # 固定点反復の「レイヤーループの内側」で current_time = 0 に戻すため、
        # 順次カーソルはレイヤーごとに独立している。エラーにはならず黙って
        # 重なるだけなので severity は warning（同梱 explainer 雛形が実際に
        # これで字幕とタイトルを重ねていた）。
        "id": "layer_timeline_independent",
        "topic": "タイムライン",
        "severity": "warning",
        "applies_to": ["Project.layer", "Object.time"],
        "text": "タイムラインの順次カーソルはレイヤーごとに 0 秒へリセットされる"
                "（レイヤー内は順次・レイヤー間は並行）。"
                "a.py で obj.time(5) と書いても、別レイヤー b.py の先頭は"
                "再び 0 秒から始まるので、レイヤーをまたいだ順次配置のつもりで"
                "並べると黙って重なる（エラーにはならない）。"
                "別レイヤーの後ろに置きたいときは、そのレイヤーの先頭で "
                "pause.time(5) を挟む / obj @ 12 で絶対配置する / a >> b で"
                "直後連結する / obj.time(3, name='intro') と "
                "pause.until('intro.end') のアンカーで待ち合わせる、のいずれかを使う。"
                "アンカー名は Project 全体で共有されるので、別レイヤーで打った "
                "name= を pause.until() や @ 'intro.end' から参照できる。"
                "動画の総尺は全レイヤーの最大値から自動算出される。",
    },
    {
        "id": "group_time_is_sequential",
        "topic": "グループ",
        "severity": "warning",
        "applies_to": ["group", "Group"],
        "text": "group(a, b, c).time(3) は各メンバーを順次配置するため、"
                "グループ全体の尺は 3 秒ではなく 9 秒（N 倍）になる。"
                "全メンバーを同時に重ねて 3 秒表示したい場合は "
                "group(a, b, c).stack(3) を使う。",
    },
    {
        "id": "text_size_const",
        "topic": "テキスト",
        "severity": "error",
        "applies_to": ["text", "typewriter", "counter"],
        "text": "text/typewriter/counter の size は定数のみ。fontsize に式を渡すと "
                "FFmpeg 8 で SEGV(0xC0000005) するため、Expr/lambda は構築時に拒否される。"
                "文字サイズを変化させたい場合は scale() Effect で拡大縮小する。",
    },
    {
        "id": "reverse_max_30s",
        "topic": "時間操作",
        "severity": "error",
        "applies_to": ["reverse"],
        "text": "reverse() の実効尺は最大30秒。全フレームをメモリに展開するため、"
                "これを超える尺に適用すると ValueError になる。",
    },
    {
        "id": "alpha_container",
        "topic": "出力",
        "severity": "error",
        "applies_to": ["Project.configure", "Project.render"],
        "text": "alpha=True（透過出力）が使えるのは .webm / .webp / .png の出力のみ。"
                ".mp4 では透過を保持できない。",
    },
    {
        "id": "layer_cache_no_audio",
        "topic": "キャッシュ",
        "severity": "warning",
        "applies_to": ["Project.layer"],
        "text": "レイヤーキャッシュ（p.layer(..., cache='auto'/'use'/'make')）は音声を含まない。"
                "音声を持つオブジェクトのあるレイヤーをキャッシュすると警告が出て音声が失われる。"
                "音声レイヤーは cache='off'（既定）のままにする。",
    },
    {
        "id": "blend_mode_canvas",
        "topic": "合成",
        "severity": "info",
        "applies_to": ["blend_mode"],
        "text": "blend_mode はキャンバス全面に透明パドした入力を blend する前提の live Effect。"
                "オブジェクト単位の局所合成ではなく、bakeable にもできない。",
    },
    {
        "id": "tts_backend",
        "topic": "外部依存",
        "severity": "error",
        "applies_to": ["narrate", "voice"],
        "text": "narrate/voice の TTS はバックエンドを選べる。"
                'backend="voicevox"（既定 127.0.0.1:50021。エンジンの別途起動が必要・'
                'オフライン・キャラボイス）、backend="edge"（pip install edge-tts。'
                '導入が容易だがオンライン必須。speaker は "ja-JP-NanamiNeural" のような音声名）、'
                'backend="sapi"（Windows標準・追加導入不要）。'
                "backend=None は自動選択（環境変数 SCRIPTVEDIT_TTS_BACKEND → "
                "VOICEVOX 起動中なら voicevox → 無ければ edge）。",
    },
    {
        "id": "scipy_required",
        "topic": "外部依存",
        "severity": "error",
        "applies_to": ["beat_sync"],
        "text": "beat_sync は scipy が必要（未インストールなら ImportError）。",
    },
    {
        "id": "one_file_one_layer",
        "topic": "構成",
        "severity": "error",
        "applies_to": ["Project.layer"],
        "text": "1ファイル = 1レイヤー。レイヤー .py の先頭は必ず "
                "`from scriptvedit import *`。レイヤー内で作った Object は exec 中に "
                "Project へ自動登録されるので、p.objects.append() の手動追加はしない"
                "（render 時のレイヤー再実行で消える）。"
                "main.py 等レイヤーファイルの外で Object を作ると同じ理由で"
                "破棄されるため、render() が ValueError で検出して停止する。",
    },
    {
        "id": "terminal_frame_effect_last",
        "topic": "生成効果",
        "severity": "error",
        "applies_to": ["morph_to", "explode_to", "assemble_from"],
        "text": "morph_to / explode_to / assemble_from は終端フレーム生成Effect。"
                "bakeable な ops の末尾に1つだけ置ける（後ろに別の bakeable Effect を続けられない）。",
    },
    {
        "id": "slice_is_destructive",
        "topic": "時間操作",
        "severity": "error",
        "applies_to": ["Object"],
        "text": "obj[a:b]（スライス）/ obj * n（リピート）/ -obj（逆再生）は"
                "Object を破壊的に変更して同じ Object を返す。新しいクリップは"
                "作られないので、`a = src[0:2]` と `b = src[3:5]` は同じ Object を"
                "指し trim が2段重なる。別区間は Object(src) から作り直すこと"
                "（例: a = Object(src)[0:2]; b = Object(src)[3:5]）。"
                "同じ op の二重適用は ValueError で拒否される。",
    },
    {
        "id": "rotate_static_only",
        "topic": "変形",
        "severity": "error",
        "applies_to": ["rotate"],
        "text": "rotate() は静的 Transform。u を含む時間依存の式は渡せない。"
                "時間変化する回転は rotate_to() Effect を使う。",
    },
    {
        "id": "expr_no_python_math",
        "topic": "式",
        "severity": "error",
        "applies_to": ["Expr"],
        "text": "lambda の中で math.sin 等の Python 標準 math を使わない。"
                "scriptvedit が提供する sin/cos/lerp/clip 等（Expr を返す）を使う。"
                "Python の math は Expr を受け取れず TypeError になる。",
    },
    {
        "id": "bakeable_vs_live",
        "topic": "キャッシュ",
        "severity": "info",
        "applies_to": [],
        "text": "Effect には bakeable（中間ファイルへ焼き込みキャッシュ可能）と "
                "live（毎レンダで FFmpeg フィルタとして適用）がある。"
                "各エントリの bakeable フィールドで判別できる。"
                "speed/reverse/freeze_frame/blend_mode/move/shake は live。",
    },
    {
        "id": "fast_quality_hint",
        "topic": "演算子",
        "severity": "info",
        "applies_to": [],
        "text": "`~op` は内容を削除しない品質ヒント。軽い代替処理を持つopだけが"
                "それを使い、持たないopは通常と同一の処理を警告なしで行う。"
                "明示的な音声削除には adelete() を使う。無視されたヒントは"
                "実装済みの p.audit() が quality-hint-ignored（info）として報告する"
                "（render(strict=True) は audit の warning 以上でレンダ前に停止）。",
    },
    {
        "id": "render_timeout",
        "topic": "出力",
        "severity": "info",
        "applies_to": ["Project.render"],
        "text": "render() の timeout は既定値 None（無制限）。制限が必要な場合だけ"
                "秒数を明示する。単一出力は一時パスから原子的に確定し、明示"
                "タイムアウトまたはCtrl+Cによる中断時は書きかけを削除する。",
    },
    {
        "id": "web_preview_optimized",
        "topic": "Web/Canvas",
        "severity": "info",
        "applies_to": ["Project.render", "Project.storyboard", "Project.thumbnail"],
        "text": "draftではWeb screenshotをconfigure(draft_web_fps=8)以下へ落とし、"
                "部分レンダは交差フレームだけ撮影する。Canvas内部は静的audit対象外なので、"
                "storyboard()で確認する。完成動画があればsource=を指定すると高速。",
    },
]

# AI 向けの使い方（describe の出力だけでスクリプトが書けることを目標にする）
_MANIFEST_USAGE = {
    "overview": (
        "scriptvedit は FFmpeg を駆動する Python DSL の動画編集ライブラリ。"
        "main スクリプトが Project を作り、複数のレイヤー .py を読み込んで1本の動画に合成する。"
    ),
    "concepts": [
        "Object: 素材（画像/動画/音声/HTML）。レイヤー .py の中で作ると Project に自動登録される",
        "レイヤー: p.layer() で読み込む .py 1本分の独立したタイムライン。"
        "順次カーソルはレイヤーごとに 0 秒から始まる"
        "（レイヤー内は順次・レイヤー間は並行。総尺は全レイヤーの最大値）",
        "Transform: 静的変形（resize/crop/rotate 等。時間非依存）",
        "Effect: 時間依存エフェクト（fade/move/scale 等。u=0..1 の進行度で変化）",
        "AudioEffect: 音声への効果（avolume/atempo 等）",
        "<= 演算子で Object に Transform/Effect/AudioEffect を適用する"
        "（実行順は記述順ではなく「全Transform→全Effect」のカテゴリ順。"
        "Effect 適用後に Transform を適用しようとすると ValueError。"
        "Effect 適用後の静的変形は compute() で素材化してから行う）",
        "u: エフェクトの進行度 0..1。lambda u: ... または Expr で時間変化を書く",
        "Expr: FFmpeg 式へ展開される式オブジェクト。lambda u: lerp(0, 1, u) は自動で Expr になる",
        "asset('images/bg.jpg'): プロジェクトの assets/ → assets/_imported/ → "
        "共有ライブラリ(環境変数 SCRIPTVEDIT_ASSETS、; 区切り)の順に解決する。"
        "共有ライブラリで見つかった素材は assets/_imported/ へコピーしてそのパスを返す"
        "（プロジェクトが自己完結する。キャッシュ鍵は内容ハッシュなので再レンダは起きない）",
        "here('scene.html'): 実行中のレイヤーファイルと同じディレクトリ（cwd 非依存）",
    ],
    "main_script": (
        "import os\n"
        "from scriptvedit import *\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    os.chdir(os.path.dirname(os.path.abspath(__file__)))\n"
        "    p = Project()\n"
        "    p.configure(width=1280, height=720, fps=30, background_color='black')\n"
        "    p.layer('bg.py', priority=0)      # 数字が小さいほど奥\n"
        "    p.layer('title.py', priority=1)\n"
        "    p.render('output.mp4')\n"
    ),
    "layer_file": (
        "# bg.py — 1ファイル = 1レイヤー。先頭は必ず from scriptvedit import *\n"
        "from scriptvedit import *\n"
        "\n"
        "bg = Object('bg.jpg')            # Project へ自動登録される\n"
        "bg <= resize(sx=1.0, sy=1.0)     # Transform（静的）\n"
        "bg.time(5) <= fade(lambda u: clip(u * 2, 0, 1))   # 5秒表示 + フェードイン\n"
        "bg <= move(x=lambda u: lerp(0.4, 0.6, u), y=0.5, anchor='center')\n"
        "\n"
        "t = text('タイトル', x=0.5, y=0.3, size=64, color='white')\n"
        "t.time(3) <= fade(lambda u: 1 - abs(2 * u - 1))   # フェードイン→アウト\n"
    ),
    "dsl": {
        "apply": "obj <= fade(lambda u: u)            # Transform/Effect/AudioEffect を適用",
        "chain": "obj <= fade(0.5) & scale(1.2)       # Effect 同士は & で連結",
        "transform_chain": "obj <= resize(sx=0.5, sy=0.5) | blur(3)   # Transform 同士は |",
        "duration": "obj.time(3)                       # 表示尺3秒（省略時は素材の尺）",
        "start": "obj.show(3)                          # 時計を進めずに3秒表示（並行表示・非進行）",
        "anchor": "obj.time(3, name='intro'); pause.until('intro.end')   # name= は <name>.start / <name>.end を生成",
        "length": "obj.length()                        # trim/atempo を反映した実効尺",
        "quality": "~fade(1.0)                         # ~op は品質ヒント（内容は削除しない）",
        "policy": "+fade(1.0) / -fade(1.0)             # +op=force, -op=cache off",
        "slice": "clip = Object(src)[2:5]              # 素材時間2〜5秒を切り出し（表示尺=3秒が既定。負値は末尾相対、stepは不可）",
        "place": "clip @ 12                            # タイムライン12秒に絶対配置（非進行）。@ 'intro.end' でアンカー参照可",
        "sequence": "a >> b                             # b を a の終了直後に開始。a >> pause.time(0.5) >> b で間も可（要・先行の尺確定）",
        "repeat": "clip * 3                             # 3回連続再生（表示尺=実効尺×3。映像loop+音声aloop）",
        "reverse": "-clip                               # 逆再生（reverse()の糖衣。音声は反転されない）",
        "strict": "p.render(out, strict=True)           # audit の warning があればレンダ前に停止",
    },
    "expr": {
        "lambda": "lambda u: lerp(0.2, 0.8, u)   # u は 0..1 の進行度",
        "easing": "img <= scale(apply_easing(ease_in_out_cubic, 1.0, 1.5))",
        "keyframes": "img <= fade(keyframes((0, 0), (0.2, 1), (0.8, 1), (1, 0)))",
        "chain_methods": "(lambda u: u) は Expr にすると .smooth() / .invert() / "
                         ".pingpong() / .map(lo, hi) / .clamped() / .oscillate() が使える",
        "caution": "lambda の中で Python の math.sin は使わない。scriptvedit の sin/cos を使う",
    },
    "plugin_template": (
        "# plugins/my_fx.py に置くだけで自動読込され、from scriptvedit import * で使える\n"
        "from scriptvedit import effect_plugin\n"
        "\n"
        "@effect_plugin('my_glow', bakeable=True, category='視覚効果',\n"
        "               params={'radius': {'type': 'number', 'default': 10,\n"
        "                                  'min': 0, 'max': 200, 'desc': 'ぼかし半径'}})\n"
        "def build_my_glow(params, ctx):\n"
        "    '''自作グロー（この1行目が要約としてマニフェストに載る）'''\n"
        "    # ctx: u / u_T / start / dur / fps / width / height / label / obj / project ...\n"
        "    return [f\"gblur=sigma={params['radius']}\"]\n"
        "\n"
        "# → レイヤーで  obj <= my_glow(radius=20)  として使える\n"
    ),
    "workflow": [
        "1. describe() または `python -m scriptvedit describe` で使える機能を確認する",
        "2. constraints（落とし穴）と対象エントリの notes を必ず読む",
        "3. main スクリプト + レイヤー .py を書く（1ファイル=1レイヤー）",
        "4. p.render(out, dry_run=True) でフィルタ構築だけ検証してから本レンダする",
        "5. 足りない Effect は plugins/*.py に @effect_plugin で足す（コア編集不要）",
    ],
    "cli": [
        "python -m scriptvedit new myvideo              # 動画プロジェクトの雛形を生成",
        "python -m scriptvedit new myvideo --template explainer  # 数式・字幕・BGM入り",
        "python -m scriptvedit describe                 # 全マニフェスト（JSON）",
        "python -m scriptvedit describe --format md     # 人間可読 Markdown",
        "python -m scriptvedit describe --kind effect   # 種別で絞る",
        "python -m scriptvedit describe --name fade     # 単一エントリ",
        "python -m scriptvedit cache --stats            # キャッシュ統計",
        "python -m scriptvedit watch main.py            # 変更監視して再レンダ",
    ],
}


# イントロスペクション/プラグイン登録などのメタAPI（素材オブジェクトを生成しない）
_MANIFEST_META_NAMES = {
    "describe", "describe_markdown", "plugin_manifest", "effect_plugin",
    "load_plugin", "load_plugins", "unregister_plugin",
    # 素材・パス解決（cwd 非依存）
    "asset", "assets_dir", "here",
    # キャッシュ操作（素材オブジェクトを作らないメタAPI）
    "cache_clear", "cache_gc", "cache_stats",
}

# 内部にしか存在しない（公開ファクトリを持たない）操作の宣言
# grid は Object.grid() メソッド経由でのみ生成される Transform
_MANIFEST_INTERNAL_OPS = {
    "grid": {
        "kind": "transform",
        "summary": "グリッド配置Transform（Object.grid(cols, rows, gap) で生成）",
        "example": "obj.grid(3, 2, gap=8)",
    },
    "repeat": {
        "kind": "effect",
        "summary": "n回連続再生の映像側Effect（DSL糖衣 `obj * n` で生成。live）",
        "example": "clip = Object('v.mp4')[0:2] * 3   # 2秒区間を3回=6秒",
    },
    "arepeat": {
        "kind": "audio_effect",
        "summary": "n回連続再生の音声側AudioEffect（DSL糖衣 `obj * n` で生成）",
        "example": "clip = Object('v.mp4')[0:2] * 3",
    },
}

# expr セクションのグループ分け（数学関数/条件分岐/イージング/シーケンス）
_MANIFEST_EXPR_GROUPS = {
    "数学関数": ["sin", "cos", "tan", "asin", "acos", "atan", "atan2",
                 "sinh", "cosh", "tanh", "exp", "log", "sqrt", "floor", "ceil",
                 "trunc", "log10", "cbrt", "lerp", "clip", "clamp", "step",
                 "smoothstep", "mod", "frac", "deg2rad", "rad2deg",
                 "abs", "min", "max", "round", "pow", "perlin"],
    "条件分岐・比較": ["if_", "lt", "gt", "lte", "gte", "eq_", "neq", "and_", "or_",
                       "not_", "between", "case", "sign", "random"],
    "イージング": ["linear", "ease_cubic_bezier", "ease_spring", "steps", "apply_easing"],
    "シーケンス・キーフレーム": ["phase", "sequence_param", "repeat", "bounce",
                                 "alternate", "staircase", "keyframes"],
}


# kind -> マニフェストのセクション名
_MANIFEST_KIND_SECTIONS = {
    "effect": "effects",
    "transform": "transforms",
    "audio_effect": "audio_effects",
    "factory": "factories",
    "class": "objects",
    "object_method": "object_methods",
    "project_method": "project_methods",
    "expr": "expr",
    "plugin": "plugins",
    "meta": "meta",
}

_MANIFEST_ENTRY_SECTIONS = ("effects", "transforms", "audio_effects", "factories",
                            "objects", "object_methods", "project_methods",
                            "expr", "plugins", "meta")


# 部分一致を許す最短クエリ長。これより短いと 'P' が 'Project'/'pip' 等を
# 巻き込んで絞り込みにならないため、完全一致だけに制限する。
_MANIFEST_NAME_PARTIAL_MIN = 3


# choices を持つパラメータ名 → enums のキー（md の打ち切り時に参照先を示す）
_MANIFEST_PARAM_ENUM_KEY = {
    "mode": "blend_mode",
    "transition": "xfade_transition",
    "kind": "xfade_transition",
    "anchor": "anchor",
    "preset": "preset",
    "encoder": "encoder",
    "cache_quality": "layer_cache_quality",
    "cache": "layer_cache",
}

# md の表へ載せる choices の最大件数（超えたら参照先を示して打ち切る）
_MANIFEST_MD_CHOICES_MAX = 8
