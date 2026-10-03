# -*- coding: utf-8 -*-
"""describe（ケイパビリティ・マニフェスト）の手書き補助テーブル。

ここは**純データ**だけを置く。導出ロジック（inspect による自動導出・エントリ
組み立て・kind/name の絞り込み・Markdown 整形）は manifest.py にある。

scriptvedit 内の import は state.py（パッケージ内 import ゼロの葉）から語彙を
借りるだけに留めること。ここに依存を足すと「データとロジックの分離」が
崩れ、循環 import の芽にもなる。

dict のキー順序は describe の出力順に直結する。**定義順を変えないこと。**
"""

from scriptvedit.state import _AUDIO_VIZ_KINDS, _FLY_COLOR_PATHS, _FLY_MATCH_MODES, _FLY_STAGGER_BY, _NORMALIZE_AUDIO_MODES

MANIFEST_VERSION = "1.1"

# 色として扱うパラメータ名（型の自動判定に使う）
_MANIFEST_COLOR_PARAMS = {
    "color", "fill", "bg", "border_color", "box_color", "background_color",
    "bg_color", "shadow_color", "outline_color",
}

# カテゴリ（日本語）: カテゴリ名 -> そのカテゴリに属する公開名
_MANIFEST_CATEGORY_MEMBERS = {
    "変形": ["resize", "rotate", "crop", "pad", "blur", "eq", "flip", "grid"],
    "視覚効果": [
        "fade", "wipe", "zoom", "color_shift", "shake", "chroma_key", "vignette",
        "pixelize", "glow", "lut", "glitch", "perspective_warp", "lens",
        "ken_burns", "drop_shadow", "outline", "tint",
    ],
    "変形効果": ["scale", "move", "rotate_to", "move_along", "path_bezier",
                 "throw", "inertia", "look_at"],
    "合成": ["mask", "mask_wipe", "opacity", "blend_mode", "rounded", "pip",
             "blur_background_fill", "progress_bar"],
    "時間操作": ["speed", "reverse", "freeze_frame", "trim", "delete",
                 "atrim", "atempo", "adelete"],
    "生成効果": ["morph_to", "explode_to", "assemble_from", "fly_to"],
    "テキスト・字幕": ["text", "typewriter", "counter", "subtitles", "karaoke",
                       "subtitle", "subtitle_box", "bubble", "diagram",
                       "text_image", "text_transition", "odometer"],
    "数式": ["formula", "formula_lines"],
    "オーディオ": ["avolume", "duck_under", "loop", "audio_sequence",
                   "sfx", "audio_viz", "voice", "narrate", "normalize_audio"],
    "シーケンス生成": ["slideshow", "transition", "video_sequence", "slide",
                       "stills", "frames"],
    "同期・タイムライン": ["anchor", "pause", "scene", "beat_sync", "marker"],
    "グループ": ["group", "tile"],
    "図形ビルダー": ["circle", "rect", "arrow", "label", "spotlight"],
    "ノイズ": ["perlin"],
    "地図": ["globe"],
    "正規表現の照合": ["regex_trace", "regex_count", "regex_view"],
    "ネットワーク図": ["flow_graph", "flow_tree"],
    "データ構造の図": ["slots"],
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

_PARTICLE_PARAM_META = {
    "max_pixels": {"type": "int", "default": 2000, "min": 1,
                   "desc": "粒の数の上限（不透明な画素から無作為に間引く）。"
                           "推奨: 文字は 8000〜12000（既定の 2000 は疎ら）"},
    "speed": {"type": "number", "default": 200.0,
              "desc": "放射方向の初速 px（進行度 0→1 の間に進む距離。粒ごとに "
                      "0.5〜1.5 倍）。推奨 250〜450。toward / from_point を"
                      "指定したときは横ぶれの大きさ"},
    "gravity": {"type": "number", "default": 300.0,
                "desc": "重力 px（+ で下へ。進行度 1 で 0.5×gravity 落ちる）。"
                        "0 で無重力。toward / from_point では道すじのたるみ"},
    "spread": {"type": "number", "default": 1.0, "min": 0,
               "desc": "初速のばらつき（0 で純粋な放射状）。推奨 0.5〜1.0"},
    "swirl": {"type": "number", "default": 0.0,
              "desc": "重心まわりの回転 rad（正で時計回り）。推奨 0（なし）〜1.5"},
    "particle_size": {"type": "int", "default": 2, "min": 1,
                      "desc": "粒（円）の半径 px。推奨 2（1080p）"},
    "seed": {"type": "int", "default": 42, "desc": "乱数の種（同じ値なら同じ絵）"},
    "dissolve": {"type": "number", "default": 0.25, "min": 0, "max": 1,
                 "desc": "元の絵 → 粒へ切り替える進行度の区間（0〜dissolve）。"
                         "推奨 0.08〜0.15（小さいほど早く粒になる）"},
    "expand": {"type": "int", "default": None, "min": 0,
               "desc": "素材の周りに足す透明の余白 px。None（既定）は自動: "
                       "粒が実際に飛ぶ範囲から決める（左右と上下で別。ほぼ消えた粒は"
                       "数えず、画面の外になる分は足さない）。数値を渡すと四方に同じ幅"},
    "fade": {"type": "bool", "default": True,
             "desc": "True は粒が進行度に合わせて薄れて消える。False は薄れず、"
                     "散った位置に残る（散らしたまま止めて見せる）"},
    "delay": {"type": "number", "default": 0, "min": 0,
              "desc": "動き出すまでの秒数。その間は最初のコマを出すだけで、"
                      "粒子のコマは作らない（静止の間を blend で作るより速い）"},
    "duration": {"type": "number", "default": None,
                 "desc": "動く秒数（None は残り全部）。終わった後は最後のコマを "
                         "Object の尺の終わりまで保持する"},
}


# text_transition / odometer が **fmt で受ける文字の書式（text_image と同じ名前・既定値。
# max_width / canvas / background / missing は受けない）
_TEXTMOVE_FMT_META = {
    "size": {"type": "number", "default": 64, "min": 1, "max": 2000,
             "desc": "文字サイズpx（状態ごとの大きさは区間の書式 {'size': …} で変える）"},
    "font": {"type": "string", "default": None,
             "desc": "フォントファイルパス（省略時は text() と同じ既定の探索）。"
                     "等幅にしたいときは mono のフォント"},
    "font_index": {"type": "int", "default": 0, "min": 0, "desc": ".ttc の中の書体番号"},
    "weight": {"type": "any", "default": None,
               "desc": "可変フォントの太さ（wght 軸の値か、名前つきインスタンス）"},
    "color": {"type": "ffcolor", "default": "white", "desc": "文字色（区間ごとに上書きできる）"},
    "markup": {"type": "bool", "default": False,
               "desc": "True で状態の文字列を簡易マークアップ {書式|文字} として読む"},
    "styles": {"type": "any", "default": None, "desc": "名前つき書式の辞書"},
    "line_spacing": {"type": "number", "default": 1.5, "min": 0.1, "max": 20,
                     "desc": "行送り ÷ その行の文字サイズ"},
    "align": {"type": "choice", "default": None, "choices": ["left", "center", "right"],
              "desc": "省略する（共通キャンバスの中の横の揃えは anchor。違う値・None は ValueError）"},
    "border": {"type": "int", "default": 0, "min": 0, "max": 500, "desc": "縁取りの太さpx"},
    "border_color": {"type": "ffcolor", "default": "black", "desc": "縁取りの色"},
    "shadow": {"type": "any", "default": [0, 0], "desc": "影のずらし (x, y) px"},
    "shadow_color": {"type": "ffcolor", "default": "black@0.6", "desc": "影の色"},
    "shadow_blur": {"type": "number", "default": 0, "min": 0, "max": 500,
                    "desc": "影のぼかし半径px"},
    "padding": {"type": "any", "default": None,
                "desc": "共通キャンバスの余白px（数値か (横, 縦)）。省略時は text_image の"
                        "自然な寸法と同じ（縁取り + 影 + size の15%）。None は渡さず省略する"},
}


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
    ("flip", "horizontal"): {"type": "bool", "default": None,
                             "desc": "左右反転（hflip）。省略時は vertical を指定していなければ True、"
                                     "vertical=True だけなら False（上下反転のみ）"},
    ("flip", "vertical"): {"type": "bool", "default": False,
                           "desc": "上下反転（vflip）"},
    ("sfx", "at"): {"type": "any", "default": None, "required": True,
                    "desc": "配置時刻（秒）。数値1つ（at=2.5）か数値のリスト（at=[0.5, 1.5]）"},
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
    # 文字を透過 PNG に焼く（PIL）
    ("text_image", "content"): {
        "type": "any", "required": True,
        "desc": "文字列、または区間のリスト [(\"文字\", {書式}), ...]。改行は \\n。"
                "書式のキーは color / size / font / font_index / weight"},
    ("text_image", "size"): {"type": "number", "default": 64, "min": 1, "max": 2000,
                             "desc": "文字サイズpx（drawtext の fontsize と同じ em の大きさ）"},
    ("text_image", "font"): {"type": "string", "default": None,
                             "desc": "フォントファイルパス（省略時は text() と同じ既定の探索。"
                                     "環境変数 SCRIPTVEDIT_FONT で上書き可）"},
    ("text_image", "font_index"): {"type": "int", "default": 0, "min": 0,
                                   "desc": ".ttc の中の書体番号（0 始まり）"},
    ("text_image", "weight"): {
        "type": "any", "default": None,
        "desc": "可変フォントの太さ。数値は wght 軸の値（例 700）、文字列は名前つき"
                "インスタンス（例 'Bold'）。可変フォントでなければ ValueError"},
    ("text_image", "color"): {"type": "ffcolor", "default": "white",
                              "desc": "文字色（色名 / 色名@alpha / #RRGGBB[AA]）"},
    ("text_image", "markup"): {
        "type": "bool", "default": False,
        "desc": "True で content の文字列を簡易マークアップとして読む。{書式|文字} "
                "（例 '犯人は、{red|正規表現が1本}'）。エスケープは \\{ \\} \\\\ の3つ"},
    ("text_image", "styles"): {
        "type": "any", "default": None,
        "desc": "名前つき書式の辞書（例 {'r': {'color': 'red', 'weight': 900}}）。"
                "区間 (\"文字\", 'r') とマークアップ {r|文字} から名前で使う"},
    ("text_image", "line_spacing"): {"type": "number", "default": 1.5, "min": 0.1, "max": 20,
                                     "desc": "行送り（ベースラインの間隔）÷ その行の文字サイズ"},
    ("text_image", "align"): {"type": "choice", "default": "left",
                              "choices": ["left", "center", "right"],
                              "desc": "行ごとの揃え"},
    ("text_image", "max_width"): {
        "type": "number", "default": None, "min": 1,
        "desc": "自動折り返しの幅px（省略時は折り返さない）。全角はどこでも、欧文は語の"
                "切れ目で折り返す。行頭禁則は句読点と閉じ括弧の類だけ"},
    ("text_image", "border"): {"type": "int", "default": 0, "min": 0, "max": 500,
                               "desc": "縁取りの太さpx（0で無効）"},
    ("text_image", "border_color"): {"type": "ffcolor", "default": "black",
                                     "desc": "縁取りの色"},
    ("text_image", "shadow"): {"type": "any", "default": [0, 0],
                               "desc": "影のずらし (x, y) px（(0,0)で無効）"},
    ("text_image", "shadow_color"): {"type": "ffcolor", "default": "black@0.6",
                                     "desc": "影の色"},
    ("text_image", "shadow_blur"): {"type": "number", "default": 0, "min": 0, "max": 500,
                                    "desc": "影のぼかし半径px（shadow=(0,0) でも光彩として出る）"},
    ("text_image", "background"): {"type": "ffcolor", "default": None,
                                   "desc": "下地の色（キャンバス全面。省略時は透明）"},
    ("text_image", "background_radius"): {"type": "number", "default": 0, "min": 0,
                                          "desc": "下地の角丸の半径px"},
    ("text_image", "padding"): {
        "type": "any", "default": None,
        "desc": "文字のまわりの余白px（数値か (横, 縦)）。省略時は 縁取り + 影 + size の15%"},
    ("text_image", "canvas"): {
        "type": "any", "default": None,
        "desc": "キャンバスを (幅, 高さ) px に固定（文字は縦中央・横は align）。"
                "morph_to の2枚を同じ寸法にするとき用"},
    ("text_image", "missing"): {
        "type": "choice", "default": "error", "choices": ["error", "warn", "ignore"],
        "desc": "フォントに無い字（豆腐）があったときの扱い（error は ValueError）"},
    ("text_image", "duration"): {"type": "number", "default": None, "min": 0.01,
                                 "desc": "表示秒数（省略時は .time(秒) で指定）"},
    # 文字列の組み替え（fx_textmove.py）。書式は **fmt で受ける
    ("text_transition", "states"): {
        "type": "any", "required": True,
        "desc": "2つ以上の状態。各状態は文字列か text_image と同じ区間のリスト"
                "（状態ごとの大きさ・色は区間の書式で変える）。1状態 400 トークン・3行まで"},
    ("text_transition", "duration"): {
        "type": "any", "default": 1.2, "unit": "秒",
        "desc": "遷移ごとの秒（数か、遷移の数のリスト）"},
    ("text_transition", "hold"): {
        "type": "any", "default": 0.0, "unit": "秒",
        "desc": "状態ごとに止まる秒（数か、状態の数のリスト）"},
    ("text_transition", "unit"): {
        "type": "string", "default": "char",
        "desc": "トークンの単位。char / word（空白もトークン）/ code（識別子・数・文字列"
                "リテラル・空白・1字の記号）/ 正規表現の文字列（例 r'\\\\.|.'）"},
    ("text_transition", "match"): {
        "type": "any", "default": "auto",
        "desc": "auto（LCS → 同じ字を近い順に move → 隙間の同じ種類の字を replace）/ "
                "edit（レーベンシュタイン。タイは prefer の順）/ position（anchor 側から位置で）/ "
                "[(i, j), …]（手で対応。番号は obj.figure.tokens）"},
    ("text_transition", "prefer"): {
        "type": "any", "default": ["replace", "insert", "delete"],
        "desc": "match='edit' のタイの決め方（3つを並べ替えた順）"},
    ("text_transition", "move"): {"type": "choice", "default": "slide",
                                  "choices": ["slide", "arc"],
                                  "desc": "位置の変わる残る字の道筋（入れ替わる組はいつも上下に"
                                          "離れて運ばれる U 字の道）"},
    ("text_transition", "replace"): {"type": "choice", "default": "auto",
                                     "choices": ["auto", "roll", "fade", "swap"],
                                     "desc": "入れ替わる字。auto=数字どうしは roll・ほかは fade / "
                                             "roll=字の窓（縦横で切る）の中を縦に流れる"
                                             "（1字 0.25 秒以上）"},
    ("text_transition", "leave"): {"type": "choice", "default": "fade",
                                   "choices": ["fade", "fall", "scatter"],
                                   "desc": "消える字（下の層に置く）"},
    ("text_transition", "enter"): {"type": "choice", "default": "fade",
                                   "choices": ["fade", "drop", "rise"],
                                   "desc": "現れる字（drop=上から / rise=下から）"},
    ("text_transition", "stagger"): {"type": "number", "default": 0.02, "min": 0, "max": 10,
                                     "unit": "秒",
                                     "desc": "字ごとの遅れ（roll は anchor の反対側から、"
                                             "ほかは左から。広がりは duration の 30% まで）"},
    ("text_transition", "easing"): {"type": "any", "default": "ease_in_out_cubic",
                                    "desc": "動きの緩急（名前・イージング関数・Expr）"},
    ("text_transition", "anchor"): {"type": "choice", "default": "left",
                                    "choices": ["left", "right", "center"],
                                    "desc": "共通キャンバスの中の横の揃え（変わらない字は止まったまま）"},
    ("text_transition", "roll_dir"): {"type": "choice", "default": "auto",
                                      "choices": ["auto", "up", "down"],
                                      "desc": "回る向き。auto=増えるなら上・減るなら下"},
    ("text_transition", "motion_blur"): {
        "type": "bool", "default": True,
        "desc": "1コマで字幅の 0.5 倍以上動く区間だけ、中間位置の平均で描く"
                "（3〜10 点。間隔が 3px 以下になるよう速いほど増やす）"},
    ("text_transition", "swing"): {
        "type": "number", "default": 1.0, "min": 0, "max": 10,
        "desc": "入れ替わる組が行から離れる量の上限（行の字の高さ = ascent + descent の倍）。"
                "行の字をちょうど避ける量より浅くはしない（0 でいつもちょうど避ける高さ）"},
    **{("text_transition", k): v for k, v in _TEXTMOVE_FMT_META.items()},
    ("odometer", "from_"): {"type": "int", "required": True, "desc": "最初の値（整数）"},
    ("odometer", "to"): {"type": "int", "required": True, "desc": "最後の値（整数）"},
    ("odometer", "base"): {"type": "int", "default": 10, "min": 2, "max": 36,
                           "desc": "基数（11 以上は A〜Z）"},
    ("odometer", "digits"): {"type": "int", "default": None, "min": 1, "max": 400,
                             "desc": "桁数（0 で埋める）"},
    ("odometer", "signed"): {"type": "any", "default": False,
                             "desc": "False（負は '-'）/ True（正にも '+'）/ "
                                     "'twos'（digits 桁の2の補数。base は2の累乗）"},
    ("odometer", "group"): {"type": "int", "default": None, "min": 1,
                            "desc": "この桁数ごとに sep を挟む"},
    ("odometer", "sep"): {"type": "string", "default": None,
                          "desc": "桁区切りの文字（既定 ','。2進の数え盤なら ' '）"},
    ("odometer", "duration"): {"type": "number", "default": 1.5, "min": 0, "unit": "秒",
                               "desc": "回る秒数"},
    ("odometer", "carry"): {"type": "choice", "default": "ripple",
                            "choices": ["ripple", "together"],
                            "desc": "ripple=変わる桁を右から ripple 秒ずつ遅らせる / "
                                    "together=全部同時"},
    ("odometer", "ripple"): {"type": "number", "default": 0.04, "min": 0, "max": 10,
                             "unit": "秒", "desc": "桁ごとの遅れ（1桁は 0.25 秒以上回す）"},
    ("odometer", "roll_dir"): {"type": "choice", "default": "auto",
                               "choices": ["auto", "up", "down"],
                               "desc": "回る向き。auto=増えるなら上・減るなら下"},
    ("odometer", "hold"): {"type": "any", "default": 0.0, "unit": "秒",
                           "desc": "前後の状態で止まる秒（数か2個のリスト）"},
    ("odometer", "easing"): {"type": "any", "default": "ease_in_out_cubic",
                             "desc": "1桁の回り方の緩急"},
    ("odometer", "anchor"): {"type": "choice", "default": "right",
                             "choices": ["left", "right", "center"],
                             "desc": "横の揃え（桁が増えても右端の桁は止まったまま）"},
    ("odometer", "motion_blur"): {"type": "bool", "default": True,
                                  "desc": "速く回る桁を中間位置の平均で描く（3〜10 点）"},
    **{("odometer", k): v for k, v in _TEXTMOVE_FMT_META.items()},
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
    ("text", "line_spacing"): {"type": "int", "default": 0,
                               "desc": "複数行の行間に足すpx（負値で詰める）"},
    ("text", "text_align"): {"type": "choice", "default": "left",
                             "choices": ["left", "center", "right"],
                             "desc": "複数行の行ごとの揃え（ブロック全体の位置は anchor と x/y）"},
    ("text", "y_align"): {"type": "choice", "default": "text",
                          "choices": ["text", "baseline", "font"],
                          "desc": "y の縦の基準。text=いちばん背の高い字の上端 / "
                                  "baseline=1行目のベースライン / font=フォントの行の上端"},
    ("typewriter", "border"): {"type": "int", "default": 0, "min": 0,
                               "desc": "縁取りの太さpx（0で無効）"},
    ("typewriter", "shadow"): {"type": "any", "default": [0, 0],
                               "desc": "影のオフセット (x, y) px（(0,0)で無効）"},
    ("counter", "format"): {"type": "string", "default": "%d",
                            "desc": "printf 風の書式。%d / %0Nd（ゼロ埋め）/ %,d（桁区切り）/ "
                                    "%.Nf（小数）/ %,.Nf。前後に固定の文字を書ける"
                                    "（文字の % は %%。' は不可）"},
    ("counter", "easing"): {"type": "any", "default": None,
                            "desc": "値の進み方。None=等速 / イージング名（'ease_out_cubic' 等）/ "
                                    "u を受け取る関数・Expr（0→1 の進行度を返す）"},
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
    # Project のメソッドは "Project.<名前>" で引かれる（manifest._manifest_entry）
    ("Project.normalize_audio", "target"): {"type": "number", "default": -14,
                                            "min": -70, "max": 0,
                                            "desc": "目標の統合ラウドネス(LUFS)"},
    ("Project.normalize_audio", "true_peak"): {"type": "number", "default": -1.5,
                                               "min": -9, "max": 0,
                                               "desc": "最終lossy音声のtrue peak目標(dBTP)。内部で0.5dB余裕を確保"},
    ("Project.normalize_audio", "lra"): {"type": "number", "default": 11,
                                         "min": 1, "max": 50,
                                         "desc": "目標LRA(LU)。mode='dynamic' のみ有効"},
    ("Project.normalize_audio", "limiter"): {"type": "bool", "default": True,
                                             "desc": "正規化後のピークリミッター（alimiter。look-ahead 5ms）"},
    ("Project.normalize_audio", "sample_rate"): {"type": "int", "default": 48000,
                                                 "min": 8000, "max": 384000,
                                                 "desc": "最終音声sample rate。Noneで自動"},
    # choices は実装（project.py の normalize_audio の検証）と同じ集合を参照する
    ("Project.normalize_audio", "mode"): {
        "type": "choice", "default": "dynamic",
        "choices": list(_NORMALIZE_AUDIO_MODES),
        "desc": "dynamic=1パスloudnorm（BGMだけの区間が膨らむ）/ "
                "linear=測定パス→一定の増幅+リミッター（区間の音量差を保つ）"},
    ("typewriter", "font"): {"type": "string", "desc": "フォントファイルパス（省略時は自動選択）"},
    ("counter", "font"): {"type": "string", "desc": "フォントファイルパス（省略時は自動選択）"},
    # choices（実装の検証コードと同じ集合を参照する）
    ("wipe", "direction"): {"type": "choice", "choices": ["left", "right", "up", "down"]},
    ("blend_mode", "mode"): {"type": "choice", "choices": None},   # None → enums から解決
    ("slideshow", "transition"): {"type": "choice", "choices": None},
    # 絵の列を1本の動画にする（stillseq.py）
    ("stills", "items"): {
        "type": "any", "required": True,
        "desc": "[(画像パス, 表示秒), …]。total を指定したときは [(画像パス, 開始秒), …]"
                "（昇順・最初は 0）。画像は全部同じ寸法"},
    ("stills", "total"): {"type": "number", "default": None, "min": 0, "unit": "秒",
                          "desc": "総尺。指定すると items の秒を開始秒として読む"
                                  "（最後の絵は total まで）"},
    ("stills", "size"): {"type": "any", "default": None,
                         "desc": "(幅, 高さ) px。省略時は画像の寸法そのまま。"
                                 "指定すると縦横比を保って収め、余白は透明"},
    ("stills", "fps"): {"type": "number", "default": None, "min": 1, "max": 1000,
                        "desc": "省略時は Project の fps"},
    ("frames", "draw"): {
        "type": "any", "required": True,
        "desc": "draw(i) → PIL.Image か、形 (高さ, 幅, 4)・uint8 の RGBA numpy 配列。"
                "i は 0 始まりのコマ番号（時刻は i / fps 秒）"},
    ("frames", "n_frames"): {"type": "int", "default": None, "min": 1,
                             "desc": "コマ数（duration とどちらか一方）"},
    ("frames", "duration"): {"type": "number", "default": None, "min": 0, "unit": "秒",
                             "desc": "秒数（n_frames とどちらか一方。最も近いコマ数へ丸める）"},
    ("frames", "key"): {
        "type": "any", "required": True,
        "desc": "キャッシュ鍵（必須）。文字列か JSON にできる値。draw のコードは鍵に"
                "入らないので、描き方や元データを変えたら key を変える"},
    ("frames", "size"): {"type": "any", "default": None,
                         "desc": "(幅, 高さ) px。省略時は Project の解像度。"
                                 "draw はこの寸法で描く"},
    ("frames", "fps"): {"type": "number", "default": None, "min": 1, "max": 1000,
                        "desc": "省略時は Project の fps"},
    # 点で描いた地球と世界地図（fx_globe.py）
    ("globe", "size"): {"type": "any", "default": 900,
                        "desc": "ortho は正方形の一辺 px。plate は (幅, 高さ)（2:1）か幅。64〜4096"},
    ("globe", "projection"): {"type": "choice", "default": "ortho", "choices": ["ortho", "plate"],
                              "desc": "ortho=正射影の地球（裏側は描かない）/ plate=正距円筒の世界地図"},
    ("globe", "radius"): {"type": "number", "default": None, "min": 8,
                          "desc": "地球の半径 px（ortho だけ）。None は 0.46×size"},
    ("globe", "view"): {"type": "any", "default": None,
                        "desc": "最初の向き (緯度, 経度)（中心・北が上）。plate は経度だけ。None は "
                                "ortho で (20, 0)・plate で 'auto'。'auto'（plate だけ）は弧が地図の端を"
                                "またがず点が端で切れず、端の経線が大陸を切らない中心の経度を選ぶ"
                                "（obj.figure.view）"},
    ("globe", "land"): {"type": "any", "default": True,
                        "desc": "True=同梱の地球（Natural Earth 1:110m の陸。パブリックドメイン）/ "
                                "False=陸なし（15 度の経緯線と縁）/ 正距円筒の白黒 PNG のパス（白が陸。"
                                "asset() でも探す）/ 多角形 [[(経度, 緯度), …], …]。None は ValueError"},
    ("globe", "step"): {"type": "number", "default": None, "min": 0.6, "max": 5, "unit": "度",
                        "desc": "点の間隔。None は画面上の間隔 ≒ 3.9×dot px になる角度（ortho は地球の"
                                "中心で。size=900 で 1.19 度・300 で 3.56 度。小さい地球で点がつぶれない）。"
                                "画面上の間隔が点の直径を下回ると警告"},
    ("globe", "dot"): {"type": "number", "default": 2.2, "min": 2, "max": 20,
                       "desc": "点の半径 px（2 未満は回転で瞬くので ValueError）"},
    ("globe", "land_color"): {"type": "any", "default": "line",
                              "desc": "陸の点の色（図の色の名前・色表記・(r, g, b[, a])）。"
                                      "不透明度 0.55 で描く（拠点の白と弧の赤が浮く）"},
    ("globe", "graticule"): {"type": "any", "default": None, "unit": "度",
                             "desc": "経緯線の間隔。None は陸が無ければ 15・あれば無し。False / 0 で無し"},
    ("globe", "limb"): {"type": "number", "default": 0.35, "min": 0, "max": 1,
                        "desc": "縁の明るさ（点の明るさ = limb + (1 − limb)·√z）"},
    ("globe", "back"): {"type": "bool", "default": False,
                        "desc": "裏側の点を 0.15 の明るさで描く（ortho）"},
    ("globe", "atmosphere"): {"type": "number", "default": 0.25, "min": 0, "max": 1,
                              "desc": "縁の外の淡い光（ortho。0 で無し）。光のぶんキャンバスが広がる"
                                      "（0.25 で縁の外へ半径の約 24%）"},
    ("globe", "colors"): {"type": "any", "default": None,
                          "desc": "図の色の差し替え {名前: 色}（fg / accent / muted / line / dim / panel）"},
    ("globe", "font"): {"type": "string", "default": None, "desc": "札のフォントファイルパス"},
    ("globe", "weight"): {"type": "any", "default": None, "desc": "札の太さ（可変フォントの wght）"},
    ("globe", "label_size"): {"type": "number", "default": 32, "min": 8, "max": 400,
                              "desc": "札の文字サイズ px"},
    ("globe", "seed"): {"type": "int", "default": 0,
                        "desc": "points(appear=('staged', …)) の1段の中のばらつき"},
    # 後戻り型の正規表現の照合（regex_vm.py / fx_regex.py）。choices は
    # regex_vm._MODES / _COUNT_KINDS・fx_regex._VIEWS / _TRIVIAL と同じ
    # （tests/test_fx_regex.py が突き合わせる）
    ("regex_trace", "mode"): {"type": "choice", "default": "search",
                              "choices": ["search", "match", "fullmatch"]},
    ("regex_trace", "max_steps"): {"type": "int", "default": 200_000, "min": 1,
                                   "desc": "記録する手数（命令の数）の上限。超えたら RuntimeError"
                                           "（数だけなら regex_count）"},
    ("regex_count", "make_text"): {
        "type": "any", "required": True,
        "desc": "make_text(n) → 文字列（例 lambda n: 'x' + ' ' * n + 'x'）"},
    ("regex_count", "count"): {"type": "choice", "default": "tests",
                               "choices": ["tests", "matches", "backtracks", "steps",
                                           "attempts"]},
    ("regex_count", "mode"): {"type": "choice", "default": "search",
                              "choices": ["search", "match", "fullmatch"]},
    ("regex_count", "fit_ns"): {"type": "any", "default": None,
                                "desc": "外挿に使う小さい n の列（既定 (8, 12, 16, 24, 32, 48, 64)。"
                                        "式に上限のある量指定子（{1,100} など）があれば既定は"
                                        "上限の先へずらす）"},
    ("regex_view", "trace"): {"type": "any", "required": True,
                              "desc": "regex_trace(式, 文字列) の戻り値（RegexTrace）"},
    ("regex_view", "view"): {"type": "choice", "default": "tape",
                             "choices": ["tape", "rows", "both"]},
    ("regex_view", "beats"): {
        "type": "string", "default": "test",
        "desc": "1拍の単位（'test'・'backtrack'・'attempt'・'step'・'literal:<字>'）"},
    ("regex_view", "pace"): {
        "type": "any", "default": None,
        "desc": "None（0.6 秒×3拍から 0.82 倍ずつ速く）・1拍の秒・framekit.pace の引数の dict"},
    ("regex_view", "at"): {"type": "any", "default": None, "unit": "秒",
                           "desc": "先頭の拍の時刻のリスト（Object の先頭からの秒）"},
    ("regex_view", "duration"): {"type": "number", "default": None, "min": 0, "unit": "秒",
                                 "desc": "図の長さ。pace を省くと拍がちょうどこの長さを埋める速さに"
                                         "する（pace を渡したときは長さだけ）"},
    ("regex_view", "size"): {"type": "any", "default": None,
                             "desc": "(幅, 高さ) px。省略時は中身に合わせる（4096 まで）"},
    ("regex_view", "count_label"): {
        "type": "any", "default": "判定 {n:,} 回",
        "desc": "カウンタの書式（n・man・oku）。(拍と回転の間, 最後) の2つの組にもできる"
                "（例 ('判定 {n:,} 回', '約{oku:.0f}億回（模式）')）"},
    ("regex_view", "cell"): {"type": "int", "default": 64, "min": 24, "max": 256,
                             "desc": "1マスの大きさ px（文字は cell × 0.61）"},
    ("regex_view", "window"): {"type": "any", "default": None,
                               "desc": "(開始, 終了)。この範囲だけをマスで描き、外は「…」に畳む"},
    ("regex_view", "show"): {"type": "any",
                             "desc": "描く要素の組（pattern・cells・spans・cursor・start・arrow・"
                                     "fail・count）"},
    ("regex_view", "count"): {"type": "choice", "default": "tests",
                              "choices": ["tests", "matches", "backtracks", "steps",
                                          "attempts"]},
    ("regex_view", "colors"): {"type": "any", "default": None,
                               "desc": "色の上書き（fg・accent・muted・line・dim・panel）"},
    ("regex_view", "trivial"): {"type": "choice", "default": "mark", "choices": ["mark", "hide"]},
    # 点と線の図（fx_flow.py）。choices は fx_flow の _LAYOUTS / _DIRECTIONS / _SHAPES /
    # _LABEL_POS と同じ（tests/test_fx_flow.py が突き合わせる）
    ("flow_graph", "nodes"): {
        "type": "any", "required": True,
        "desc": "{名前: {'pos': (x, y), 'label': 文字, 'shape': 'dot'|'box', 'layer': 段}} か"
                "名前のリスト。nodes() と edges() を持つグラフも読む（networkx は import しない）"},
    ("flow_graph", "edges"): {
        "type": "any", "default": [],
        "desc": "(a, b) か (a, b, {'delay': 秒, 'curve': 0.2}) のリスト"},
    ("flow_graph", "layout"): {
        "type": "choice", "default": "given", "choices": ["given", "layered", "radial", "rings"],
        "desc": "given=pos の通り / layered=BFS の段（段の中は重心法を2往復）/ "
                "radial=根が中心で角度は葉の数に比例 / rings=段ごとの同心円"},
    ("flow_graph", "direction"): {"type": "choice", "default": "down",
                                  "choices": ["down", "up", "right", "left"],
                                  "desc": "layered の向き（根のある側から）"},
    ("flow_graph", "size"): {"type": "any", "default": None,
                             "desc": "キャンバス (幅, 高さ) px。省略時は Project の解像度"},
    ("flow_graph", "padding"): {"type": "number", "default": 40, "min": 0,
                                "desc": "自動配置の余白 px（文字・box のはみ出しは別に空ける）"},
    ("flow_graph", "node"): {"type": "choice", "default": "dot", "choices": ["dot", "box"],
                             "desc": "既定のノードの形（nodes の 'shape' で個別に変えられる）"},
    ("flow_graph", "node_radius"): {"type": "number", "default": 10, "min": 1, "max": 200,
                                    "desc": "点の半径 px"},
    ("flow_graph", "box"): {"type": "any", "default": [240, 72],
                            "desc": "box の (幅, 高さ) px。文字が収まらなければ広げる"},
    ("flow_graph", "edge_width"): {"type": "number", "default": 3, "min": 0.5, "max": 50,
                                   "desc": "辺の幅 px（line 色）"},
    ("flow_graph", "curve"): {"type": "number", "default": 0.0, "min": -2, "max": 2,
                              "desc": "辺の曲がり（中点から 長さ×curve だけ進行方向の左へ。負で右）。"
                                      "ノードから出る辺・入る辺の組ごとに、隣の辺との角の間 g に"
                                      "応じて tan(g/2)/2 までに自動で弱める（渦に見えない。"
                                      "均等に散った 3〜4 本はそのまま、layered の 3 本の扇は約 0.16。"
                                      "辺ごとの 'curve' は指定どおり）"},
    ("flow_graph", "colors"): {"type": "any", "default": None,
                               "desc": "図の色の差し替え {名前: 色}（fg / accent / muted / line / dim / panel）"},
    ("flow_graph", "font"): {"type": "string", "default": None,
                             "desc": "ノードの文字のフォントファイルパス"},
    ("flow_graph", "weight"): {"type": "any", "default": None,
                               "desc": "文字の太さ（可変フォントの wght か名前つきインスタンス）"},
    ("flow_graph", "label_size"): {"type": "number", "default": 36, "min": 30, "max": 400,
                                   "desc": "文字サイズ px（30 未満は ValueError）"},
    ("flow_graph", "label_pos"): {
        "type": "choice", "default": "auto",
        "choices": ["auto", "below", "above", "right", "left", "center"],
        "desc": "点の文字の位置。auto は layered では葉だけ流れの向き（根は逆の側・途中は横）、"
                "radial / rings は外向き、given は下を第一候補にし、辺・ほかのノードと重なる側を"
                "避ける（box の文字はいつも中）"},
    ("flow_graph", "seed"): {"type": "int", "default": 0,
                             "desc": "点の塊（flow_tree の 500 を超える葉）の散らばり"},
    ("flow_tree", "levels"): {
        "type": "any", "required": True,
        "desc": "段ごとのノードの数（例 [1, 50, 3000]）。名前は 'L段_番号'"},
    ("flow_tree", "t"): {"type": "number", "default": 0, "min": 0, "unit": "秒",
                         "desc": "根から broadcast する秒（None で配らない）"},
    ("flow_tree", "hop"): {"type": "number", "default": 0.4, "min": 0, "unit": "秒",
                           "desc": "1段あたりの秒"},
    ("flow_tree", "layout"): {"type": "choice", "default": "radial",
                              "choices": ["radial", "layered", "rings"]},
    ("flow_tree", "amount"): {
        "type": "number", "default": None, "min": 0,
        "desc": "根の量。分かれるたびに等分し、パケットの直径を √(量) に比例させる（合計は保つ）"},
    ("flow_tree", "size"): {"type": "any", "default": None,
                            "desc": "flow_graph と同じ（**kw で受ける。colors・node_radius・seed なども同様）"},
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
    # *others（可変長）はシグネチャから導出できないので宣言する
    ("duck_under", "others"): {"type": "object", "required": True,
                               "desc": "下げる合図になる音声Object（1つ以上。"
                                       "duck_under(a, b, c) / duck_under([a, b, c]) の"
                                       "どちらでも可。Narration は .audio が使われる）"},
    ("duck_under", "ratio"): {"type": "number", "default": 8, "min": 1,
                              "desc": "圧縮比（大きいほど深く下がる。無次元）"},
    ("duck_under", "threshold"): {"type": "number", "default": 0.05,
                                  "min": 0, "max": 1,
                                  "desc": "動作を始める入力レベル（0〜1 の振幅。dB ではない）"},
    ("duck_under", "attack"): {"type": "number", "default": 20, "min": 0,
                               "desc": "音量を下げ始める速さ（ミリ秒）"},
    ("duck_under", "release"): {"type": "number", "default": 250, "min": 0,
                                "desc": "音量を戻す速さ（ミリ秒）"},
    ("duck_under", "hold"): {"type": "number", "default": 0, "min": 0,
                             "desc": "相手が止んでから戻り始めるまでの保持時間（ミリ秒。"
                                     "0=保持なし）。読点や文の間で BGM が戻るのを防ぐ"},
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
    ("Project.marker", "time"): {
        "type": "any", "required": True,
        "desc": "秒（数値）かアンカー名の文字列（例 'q2.start'）。アンカー名はレンダ時に解決"},
    ("Project.marker", "label"): {
        "type": "string", "required": True, "desc": "チャプター名（YouTube 目次の見出し）"},
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
    ("subtitles", "srt_file"): {"type": "string", "required": True,
                                "desc": "字幕ファイルパス（.srt / .ass / .vtt）"},
    ("subtitles", "style"): {"type": "string", "default": None,
                             "desc": "ASS の force_style 文字列（例 'FontName=Meiryo,FontSize=28'）"},
    ("subtitles", "fontsdir"): {"type": "string", "default": None,
                                "desc": "フォントを名前で探すフォルダ（同梱フォント用。"
                                        "システムのフォントに加えて探す）"},
    ("karaoke", "fontsdir"): {"type": "string", "default": None,
                              "desc": "style['font'] の名前を探すフォルダ（subtitles と同じ）"},
    # 実装（effects/terminal.py）は Object 以外を TypeError で拒否する。
    # 文字列パスは受け付けない（Object(...) で包んでから渡す）
    ("morph_to", "target"): {"type": "object", "required": True,
                             "desc": "モーフ先の画像 Object（パス文字列は不可: "
                                     "Object('target.png') で包む）"},
    ("assemble_from", "source"): {"type": "object", "required": True,
                                  "desc": "集合元の画像 Object（パス文字列は不可: "
                                          "Object('src.png') で包む）"},
    # --- morph_to(**morph_params)。既定値は morph.py の関数シグネチャと同じ
    #     （tests/test_terminal_fx.py が突き合わせる）---
    ("morph_to", "method"): {"type": "choice", "default": "sdf",
                             "choices": ["sdf", "transport"],
                             "desc": "sdf=輪郭（アルファ）の距離場で形を補間（既定。文字・図形向き）/ "
                                     "transport=最適輸送で画素を動かす（内部の部品が動く。重い）。"
                                     "省略して transport 専用のキーを渡すと transport"},
    ("morph_to", "align"): {"type": "bool", "default": True,
                            "desc": "[sdf] 不透明部の重心を合わせてから補間する"},
    ("morph_to", "fit"): {"type": "bool", "default": None,
                          "desc": "[sdf] 不透明部の外接矩形（位置と大きさ）を合わせながら補間する。"
                                  "None（既定）は自動: 幅か高さが 1.15 倍を超えて違う組だけ合わせる"
                                  "（幅の違う文字列で端の文字が途中で欠けるのを防ぐ）"},
    ("morph_to", "edge_softness"): {"type": "number", "default": 1.0, "min": 0,
                                    "desc": "[sdf] 輪郭のぼかし幅 px"},
    ("morph_to", "color_ease"): {"type": "int", "default": 1, "min": 0, "max": 3,
                                 "desc": "[sdf] 色の進行に smoothstep を掛ける回数"
                                         "（大きいほど両端の色を保つ）"},
    ("morph_to", "color_path"): {"type": "choice", "default": "oklch",
                                 "choices": ["oklch", "oklab"],
                                 "desc": "[sdf] 色の通り道。oklch=色相を回す / oklab=直線"},
    ("morph_to", "max_pixels"): {"type": "int", "default": 2000, "min": 1,
                                 "desc": "[transport] 最適輸送のサンプル数（重さは3乗で効く）"},
    ("morph_to", "delay"): _PARTICLE_PARAM_META["delay"],
    ("morph_to", "duration"): _PARTICLE_PARAM_META["duration"],
    # --- explode_to / assemble_from(**particle_params) ---
    **{(fn, key): meta
       for fn in ("explode_to", "assemble_from")
       for key, meta in _PARTICLE_PARAM_META.items()},
    ("explode_to", "toward"): {
        "type": "any", "default": None,
        "desc": "(dx, dy)。放射ではなく、素材の中心からこれだけずれた1点へ粒が集まって消える"
                "（px。右と下が正）。spread は粒の出発のばらつき、swirl は渦"},
    ("assemble_from", "from_point"): {
        "type": "any", "default": None,
        "desc": "(dx, dy)。素材の中心からこれだけずれた1点から粒が出て、絵に集まる"
                "（px。右と下が正）"},
    # --- fly_to。既定値は morph_flight.py の generate_flight_frames と同じ
    #     （tests/test_fly_to.py が突き合わせる）。choices は state.py の集合 ---
    ("fly_to", "target"): {"type": "object", "required": True,
                           "desc": "行き先の絵（加工していない画像 Object。text_image も可。"
                                   "パス文字列は不可）。消費されて Project から外れる"},
    ("fly_to", "offset"): {"type": "any", "default": [0, 0],
                           "desc": "(dx, dy)。A の中心から B の中心までのずれ px（A の絵の px。"
                                   "右と下が正）。B の左上は A の左上から "
                                   "(⌈Wa/2⌉ + ⌊dx − Wb/2⌋, ⌈Ha/2⌉ + ⌊dy − Hb/2⌋)（A の中心を整数の"
                                   "画素に置いたとき、中心を A の中心 + offset に置いた静止画の B と"
                                   "同じ丸め）"},
    ("fly_to", "max_pixels"): {"type": "int", "default": 12000, "min": 1, "max": 100000,
                               "desc": "粒の数の上限。粒の数は min(max_pixels, A と B の"
                                       "多い方の不透明な画素数)。少ない側は複製して揃える"},
    ("fly_to", "match"): {"type": "choice", "default": "ot",
                          "choices": list(_FLY_MATCH_MODES),
                          "desc": "粒の対応。ot=スライスした最適輸送（移動の総量が小さく道すじが"
                                  "交差しにくい）/ angle=重心まわりの角度の順 / random=無作為"},
    ("fly_to", "arc"): {"type": "number", "default": 0.25, "min": -4, "max": 4,
                        "desc": "道すじのふくらみ（2次ベジェの制御点を中点から 距離×arc だけ"
                                "ずらす）。正で進む向きの右手側（右へ進む粒は下側）、負で左手側"},
    ("fly_to", "swirl"): {"type": "number", "default": 0.0, "min": -50, "max": 50,
                          "desc": "道すじを中点のまわりに回す角度 rad（道の半ばで最大、両端で 0。"
                                  "正で時計回り）"},
    ("fly_to", "stagger"): {"type": "number", "default": 0.3, "min": 0, "max": 0.95,
                            "desc": "出発の遅れの幅（全体の進行度に対する比）。0 で全粒が同時に"
                                    "出る。残りの時間で各粒が smoothstep で加減速する"},
    ("fly_to", "stagger_by"): {"type": "choice", "default": "x",
                               "choices": list(_FLY_STAGGER_BY),
                               "desc": "出発の順番。x / y = 横 / 縦の並びで、進む向きの先頭の粒から"
                                       "（右へ飛ぶなら右端から。粒の列が途中で詰まらない）/ "
                                       "distance=遠くへ行く粒から / random=無作為"},
    ("fly_to", "particle_size"): {"type": "number", "default": 2, "min": 0.5, "max": 32,
                                  "desc": "粒（円）の半径 px（サブピクセルの位置・縁 1px の"
                                          "アンチエイリアス）。推奨 2（1080p）。描く時間は"
                                          "粒の数 × 半径² に比例する（1.2万粒で半径 2 は 1コマ"
                                          "約 50ms、半径 32 は約 1.5 秒。大きな粒は max_pixels を"
                                          "減らす）"},
    ("fly_to", "color_path"): {"type": "choice", "default": "oklab",
                               "choices": list(_FLY_COLOR_PATHS),
                               "desc": "粒の色の通り道。oklab=直線 / oklch=色相を回す"},
    ("fly_to", "dissolve"): {"type": "any", "default": [0.15, 0.15],
                             "desc": "(a, b)。最初の a の区間で A の絵から粒へ、最後の b の区間で"
                                     "粒から B の絵へ移る（全体の進行度に対する比。a + b <= 1）"},
    ("fly_to", "seed"): {"type": "int", "default": 0, "desc": "乱数の種（同じ値なら同じ絵）"},
    ("fly_to", "delay"): _PARTICLE_PARAM_META["delay"],
    ("fly_to", "duration"): {
        "type": "number", "default": None,
        "desc": "動く秒数（None は残り全部）。終わった後は B を Object の尺の終わりまで保持する"},
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
    "Project.marker": [
        "time にアンカー名（'q2.start' 等）を渡すと、レンダ時（タイムライン解決の後）に"
        "時刻へ解決される。存在しない名前は候補つきの ValueError",
        "アンカーはレンダでしか解決されないため、render() 前の export_chapters() / "
        "export_metadata() は dry_run でタイムラインを解決してから書き出す",
    ],
    "Project.param": [
        "型は default から推論する（int/float/bool/str）。解釈できない値は"
        "既定値へ黙って戻さず ValueError",
        "どの p.param() にも読まれなかった --param は誤記として ValueError"
        "（SCRIPTVEDIT_PARAM_* 由来は共有されうるので警告）。"
        "ただし p.param() を1回も呼ばないプロジェクトでは --param を解釈しないので、"
        "この検査も働かず黙って無視される",
        "`--param n=v` と `--param=n=v` は同値。`=` の無い指定は ValueError",
    ],
    "group": [
        "返り値は Group（Object ではない）",
        "time(N) は各メンバーを**順次配置**するのでグループ全体の尺は N 倍になる。"
        "同時に重ねたいときは stack(N) を使う",
    ],
    "text": ["size は定数のみ。lambda/Expr を渡すと FFmpeg 8 で SEGV するため拒否される",
             "x/y/alpha は Expr/lambda 可（アニメーション可能）",
             "border=2 の縁取りや shadow=(2, 2) の影で細い文字の可読性を上げられる",
             "複数行は text_align で行ごとの揃え、line_spacing で行間を決められる",
             "glow() を掛けても色は変わらない（白い文字は白く光る）"],
    "typewriter": ["size は定数のみ（text と同じ制約）"],
    "stills": [
        "何枚あっても ffmpeg への入力は1本。全面 PNG を1枚ずつ Object にすると"
        "手間が「枚数×尺」に比例し、数百枚ではコマンド長の上限も超える",
        "切り替わりは境目の時刻を最も近いフレームへ丸める（誤差は積もらない）。"
        "丸めた結果は obj.starts（各絵の開始秒）/ obj.frame_counts / obj.length()",
        "音声は obj.starts[i] に合わせて置く（列を @ t で置いたら t + obj.starts[i]）",
        "time() で総尺より長く表示すると最後の絵が残る（普通の動画は背景が見える）",
        "alpha を保つ。画像は全部同じ寸法・同じ形式であること（違えば ValueError。"
        "PNG と JPEG は混ぜられない。RGB の PNG と RGBA の PNG は混ぜてよい）",
        "生成物は __cache__/artifacts/stills/<鍵>.mov（可逆の qtrle。同じ絵が続く区間は"
        "ほぼ 0 バイト）。鍵は各画像の内容指紋・各絵のフレーム数・fps・size",
    ],
    "slots": [
        "s = slots() → s.row(...) で行 → 出来事（fill / read / link / put / set / mark / "
        "unmark / to_str / compare / swap / halt）→ s.build() で動画 Object。"
        "表示は s.build().time()（引数なしは図の尺ぶん）",
        "elide なしで描けるのは 64 箱まで（n は 100,000 まで）。elide=(先頭, 末尾) で畳み、"
        "capacity・ghost・出来事が触る番号の前後は自動で見せる。全行で番号の列は揃う",
        "fill: 上限（capacity。無ければ n）を超えた分は線に当たって accent になり外へ落ちる。"
        "描く塊どうしは 0.12 秒以上空け（あふれの時間を fill の 65% まで延ばす）、"
        "spill_visible 個まで描く。あふれの動きは seed で決まる",
        "あふれの札は row(overflow_label=)。'total'（既定。入った数の合計「400件」）/ "
        "'over'（上限を超えた数「+200」）/ 'undrawn'（描かなかった塊の数）/ None / 書式の文字列"
        "（名前 total・over・undrawn・limit。例 '{total:,}件（上限{limit}）'）",
        "read / link は範囲外の番号を受ける（行の外の斜線の区画「?」へ。区画は一番外の ghost "
        "のさらに外。ghost の番号はその点線の箱）。着いた瞬間に accent。範囲外の put / set / "
        "mark 等と、build(duration=) より後に終わる出来事は ValueError",
        "compare の直後に swap / set / put を続けてよい（持ち上げた箱を下ろしながら動かす）",
        "halt(style='dim') は全体の不透明度を 0.45 に（何度呼んでもそれより薄くしない）、"
        "'freeze' は以後のコマを止める（freeze の後の出来事は ValueError）。点滅・ノイズはしない",
        "文字は p.audit() へ申告する（size で縮めた倍率込み）。既定（番号 32px）で warning は"
        "出ない。1 つの長い字のために行の字を 32px 未満へは揃えて縮めない（その字だけ縮む）。"
        "左右に並べるときは size を縮めず cell=48, gap=12, value_size=32 にする",
        "生成物は framekit.build → __cache__/artifacts/frames/<鍵>.mov。鍵は行の定義・"
        "出来事の列・寸法・色・size・フォントの内容指紋・_SLOTS_VER（dry_run は描かない）",
    ],
    "frames": [
        "draw のコードは鍵に入らない。同じ key なら draw を呼ばずに前回の動画を使うので、"
        "描き方や元データを変えたら key を変える（版番号や元データを key に入れる）",
        "draw は実レンダでキャッシュが無いときだけ呼ばれる（dry_run では呼ばれない）",
        "time() で尺より長く表示すると最後のコマが残る。alpha を保つ",
        "生成物は __cache__/artifacts/frames/<鍵>.mov（可逆の qtrle。前のコマと同じ画素は"
        "書かないので、動かない部分の多い絵ほど小さい）",
    ],
    "globe": [
        "戻り値は組み立て役。turn / spin / points / arc / ripple / night / label を積んで "
        "build() で透過動画 Object にする（obj.figure.xy(coord, t)・obj.figure.subsolar が付く）",
        "座標は (緯度, 経度) の度。land の多角形だけ (経度, 緯度)。|緯度| が 90 を超えると ValueError",
        "陸地の既定は同梱の地球（Natural Earth 1:110m の陸。パブリックドメイン。同梱の素材・データ"
        "（assets/ と data/）の「全部自作」の唯一の例外で、出典は data/NOTICE.md）。land=False で陸なし。"
        "鍵は陸地の内容指紋",
        "地図を出すときは「地図: Natural Earth」と添えることを勧める（義務ではない）",
        "拠点の点（points）はまわりの陸の点を抜いて（堀）淡い光の輪を敷く（既定の色でも陸に埋もれない）。"
        "点が密で堀が陸をほとんど消すとき（plate 1400×700 に数千点など）は points(halo=False) で"
        "芯だけにする",
        "plate の view は既定で 'auto'（太平洋を渡る弧も地図の端で切れず1本につながる中心の経度）",
        "推奨: 1回 10 秒以内・1本の動画に 2 回まで。都市の点は出典を書くか「模式図」と明記する",
        "spin は既定で回さない（qtrle が効かず 900×900・5 秒で約 80MB）。3 度/秒を超えると警告",
        "night(when) の when は UTC の datetime（naive は ValueError）。太陽の真下は NOAA の簡略式",
        "札（label）は裏へ回ると消え、重なり・はみ出しは build のときに警告する",
        "plate の弧は弦に垂直に上へ反る（弦が縦に近いと地図の内側へ）。反りで日付変更線を"
        "またいで反対の端に描かれるときは反りを縮める",
        "numpy・opencv-python・Pillow が要る（framekit と同じ。描画のときに遅延 import）",
    ],
    "text_image": [
        "Pillow 9.1 以上が必要（pip install \"Pillow>=9.1\"）。戻り値は画像 Object（配置は move(x=, y=, anchor=)）",
        "morph_to / explode_to / assemble_from の入力・target・source に使える"
        "（text() 系には掛けられない）。morph の2枚は canvas= で同じ寸法にする",
        "区間が font を変えたとき、weight と font_index は基本書式から引き継がない",
        "行の縦位置は基本書式のフォントのメトリクスで決まり、字によって行がガタつかない",
        "p.audit() は文字サイズ・縁取りの有無・はみ出しを画面上の実寸"
        "（resize / scale の倍率込み）で検査する",
        "生成物は __cache__/artifacts/textimage/<鍵>.png（鍵は文字列・書式・"
        "フォントの内容指紋・Pillow の版）",
    ],
    "text_transition": [
        "numpy・opencv-python・Pillow が要る（dry_run は Pillow とフォントだけ）。"
        "戻り値は透過の動画 Object（配置は move(x=, y=, anchor=)）",
        "各状態のコマは text_image(状態, **fmt, **obj.figure.text_image_kwargs) と画素が一致する"
        "（text_image_kwargs は canvas と、fmt に無ければ align=anchor・padding）",
        "大きさの変わる字は大きい方の字形を縮めて置き、縁取りは途中でも border px のまま。"
        "入れ替わる組は上下に離れて運ばれ、ほかの字に重ならない高さを構築時に選ぶ"
        "（離れる量は swing × 行の字の高さまで。既定 1 倍で、キャンバスもその分だけ高くなる）",
        "鍵にはフォントの内容指紋と Pillow の版が入る（Pillow を更新すると作り直す）",
        "obj.figure.starts（各状態に着いた秒）/ pairs（遷移ごとの keep・move・replace・"
        "leave・enter とトークンの番号）/ tokens / schedule / state_frames",
        "赤くしたい字は後の状態の区間で赤にする（残る字の色は oklab で補間）",
        "max_width（折り返し）は受けない。1状態 400 トークン・3行まで、キャンバスは 8192px まで",
        "使いすぎると忙しくなる。「答えが変わる」瞬間だけに置く",
        "生成物は __cache__/artifacts/frames/<鍵>.mov（鍵は区間・時間・easing の式・"
        "動かし方・書式・フォントの内容指紋・描画の版）。time() で尺より長く出すと最後の状態が残る",
    ],
    "odometer": [
        "text_transition の糖衣（位置で対応・変わる桁は字のマスの中で roll）",
        "2進の数え盤: base=2, digits=32, signed='twos', group=8, sep=' '",
        "carry='ripple' は変わる桁を右から ripple 秒ずつ遅らせる。1桁は 0.25 秒以上回す"
        "（足りなければ ValueError）",
        "odometer.text(from_text, to_text, **kw): 書式つきの文字列（日時など）の数字の"
        "位置だけを回す。roll_dir='auto' は数字の並びで比べる（巻き戻すと下）",
    ],
    "counter": ["size は定数のみ（text と同じ制約）",
                "最初のコマは from_、最後のコマは必ず to を表示する"
                "（総尺がフレーム格子に乗らない動画の末尾でも、出力される最後のコマが to。"
                "configure(duration=) / render(end=) で途中を切った場合は切った時点の値）",
                "|値|×10^小数桁 が 2^53（約 9.007e15）未満なら全桁が正しい。"
                "定数の from_ / to がこれを超えると ValueError",
                "32ビットを超える整数・桁区切り・小数は、桁数と符号ごとの drawtext を"
                "切り替えて表示する（フィルタが数個に増える）"],
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
                 "（生成処理は素の source しか読まないため）",
                 "sdf は輪郭（アルファ）で形を補間する。背景が透明な画像が前提で、"
                 "全面不透明・全体が半透明の素材や、2枚の不透明部が重ならない組は"
                 "形が動かずクロスフェードになる（生成時に警告。"
                 "p.audit() は morph-sdf-crossfade を出す）",
                 "morph_to(b, delay=0.5, duration=1.5) で「0.5 秒待って 1.5 秒で変形、"
                 "残りは b を保持」。前後に同じ絵の静止画を別に置かなくてよい",
                 "sdf の整列の余白は対称に付き、move の anchor は2枚を中央で重ねた"
                 "共通キャンバス（余白を除く）が基準。動く区間は最低2コマ作るので、"
                 "duration が1コマ以下でも最後は target の絵になる"],
    "explode_to": ["bakeable ops の末尾に1つだけ置ける（終端フレーム生成Effect）",
                   "expand は既定（None）で自動。粒が素材の矩形で箱型に切れない",
                   "余白（expand）は対称に付き、move の anchor は余白を除いた元の絵の箱が基準"
                   "（topleft 等でも静止画として置いたときと同じ位置に映る）",
                   "散らしたまま残すには fade=False。duration=秒 と組み合わせると、"
                   "散り終えた状態を Object の尺の終わりまで保持する",
                   "静止の間は blend ではなく delay=秒 で作る（その間のコマを粒子計算で焼かない）",
                   "重さは「余白込みのキャンバス面積 × 動くコマ数」。"
                   "目安は 12000 粒・1080p・2 秒で 15 秒前後（2回目からはキャッシュ）"],
    "assemble_from": ["bakeable ops の末尾に1つだけ置ける（終端フレーム生成Effect）",
                      "source は Object のみ（パス文字列は TypeError）。画像 media_type 限定",
                      "source に Transform/Effect が付いていると ValueError",
                      "expand は既定（None）で自動。fade=False で粒が最初から濃いまま集まる",
                      "余白（expand）は対称に付き、move の anchor は余白を除いた source の絵の箱が基準",
                      "assemble_from(src, duration=1.6) で 1.6 秒で集まり、"
                      "残りは集まった絵を保持する"],
    "fly_to": ["bakeable ops の末尾に1つだけ置ける（終端フレーム生成Effect）。"
               "後ろに置けるのは live（move 等）だけ",
               "target は加工していない画像 Object のみ（Transform/Effect 付き・text() 系・"
               "動画は ValueError、パス文字列は TypeError）。text_image は使える",
               "重なる形どうしは morph_to（sdf）、離れた形どうしは fly_to"
               "（sdf は離れた形だとクロスフェードになる）",
               "最初のコマは A、最後のコマは offset の位置の B と画素一致する。"
               "delay / duration の前後は最初・最後のコマを保持（前後に同じ絵の静止画を置かなくてよい）",
               "B を出し続けるなら別の Object へ引き継がず、fly_to の Object の time() を延ばす"
               "（duration の後は B を保持する）。別の静止画の B（anchor='center'、中心 = A の中心 + "
               "offset）へ引き継ぐときは、A の中心を整数の画素に置き、⌈Wa/2⌉ + ⌊dx − Wb/2⌋ と "
               "⌈Ha/2⌉ + ⌊dy − Hb/2⌋ が偶数になる offset にすると画素一致する（overlay は左上を "
               "4:2:0 の 2px 格子へ切り捨てるので、奇数だと 1px ずれる）",
               "キャンバスは A の箱・B の箱・粒の道すじを覆い、A の中心に対して左右・上下"
               "それぞれ対称に広がる（余白は偶数）。move の anchor は余白を除いた A の箱が基準"
               "なので topleft 等でも A は静止画と同じ位置に映る。4096px を超えると ValueError",
               "A と B に不透明な画素（α>0.1）が無いと ValueError",
               "重さは「余白込みのキャンバス面積 × 動くコマ数」と粒の数。目安は 1.2万粒・"
               "1080p の文字どうしで前処理 1〜2 秒 + 1コマ 35〜95ms（2回目からはキャッシュ）",
               "粒子は最も目を引く道具。explode_to / assemble_from と合わせて1本に2〜3回まで。"
               "道すじが字幕を横切らないよう、arc の向きと重ね順（priority）は呼び出し側で決める"],
    "rotate": ["時間依存の式（u を含む式）は不可。時間変化する回転は rotate_to() を使う"],
    "flip": ["flip() は左右反転、flip(vertical=True) は上下反転だけ。"
             "両方（180度回転と同じ絵）は flip(horizontal=True, vertical=True) と明示する",
             "両方 False は ValueError。寸法は変わらない（静止画は PNG チェックポイントへ焼ける）"],
    "pip": ["scale → rounded → outline → drop_shadow → move の組を返すプリセット。"
            "配置の move は live なので、pip 全体がチェックポイントに焼けるわけではない"],
    "loop": ["引数なしの time() と組み合わせない: time() は尺を素材の長さで確定させるので、"
             "bgm.time() <= loop() は1回再生で終わる（loop(until=) も効かない）。"
             "bgm <= loop()（time を呼ばない）なら総尺まで、bgm.time(30) <= loop() や "
             "bgm.until('outro.end') <= loop() ならその尺までループする",
             "尺の決まり方: time(N) / until() / show(N) で決まった尺 → loop(until=秒)"
             "（タイムラインの絶対時刻）→ Project の総尺"],
    "keyframes": ["時刻は秒ではなく u（0..1。表示区間の進行度）。obj.time(4) なら u=0.5 は"
                  "表示開始から2秒後。範囲外の時刻は端の値で止まるだけでエラーにならないので、"
                  "秒のまま渡すと u=1 より後のキーは黙って届かない。"
                  "秒で書くなら keyframes_sec（区間1つなら ramp）",
                  "最低2点・最大128点"],
    "sfx": ["at は数値1つでも数値のリストでもよい（at=2.5 と at=[2.5] は同一・同じキャッシュ鍵）",
            "p.audit() の重なり判定は [0, 最後の at + 素材長] ではなく各発音区間で行う"],
    "scale": ["pad サイズ決定のため、u のみに依存する数値評価可能な式であること"],
    "narrate": ['backend="voicevox"（既定候補）は VOICEVOX（別プロセス）の起動が必要',
                'backend="voicevox" を明示していれば、VOICEVOX 停止中でも一度合成した台詞は'
                "キャッシュ（__cache__/tts/engine_sig.json に控えたエンジン署名で鍵を作る）から"
                "使える。合成が要る台詞だけ ConnectionError",
                'backend="edge" なら pip install edge-tts で使える（オンライン必須）',
                "backend=None は自動選択（VOICEVOX 起動中なら voicevox、無ければ edge）。"
                "エンジン停止中は edge-tts があれば別の声で合成され（無ければ RuntimeError）、"
                "VOICEVOX のキャッシュは使われない",
                "**tts_kwargs は voice と同じ（readings / pre_silence / post_silence / "
                "pause_length / pause_scale / intonation / volume_scale / kana など）。"
                "readings は読み上げにだけ効き、字幕は元の文のまま",
                "subtitle_textで読み上げと表示文を分離でき、subtitle_max_charsは日本語禁則対応",
                "subtitle_safe_areaは領域に収まる字幕矩形の位置を画面内へクランプする"],
    "duck_under": ["sidechainは自動で無音延長され、others終了後もBGMは指定尺まで続く",
                   "hold（ms）を指定すると、相手が止んでから hold の間は直前の発声の平均的な"
                   "検出レベルを保ち、その後 release で戻る（読点・文の間で戻らない）。"
                   "release を長くする代わりに使う（例: release=250, hold=600）。"
                   "hold > 0 では検出用の枝が 48kHz モノラルの包絡になる",
                   "others は複数指定できる（duck_under(n1, n2, n3)）。サイドチェーンは各 other を"
                   " amix(normalize=0) で合算した1本で、どれか1つでも鳴っている間は下がる",
                   "1つのObjectに duck_under は1回だけ（相手が複数なら1回の呼び出しにまとめる）",
                   "検出は各 other の形式統一（48kHz・ステレオ化）より前の音声で行う。"
                   "モノラルのナレーションも元の音量のまま threshold と比べられる"
                   "（揃えた後だと各チャンネル -3dB で検出され、ダッキングが浅くなる）。"
                   "相手が複数なら各 other を 48kHz モノラルへダウンミックス"
                   "（ステレオは (L+R)/2）してから合算する",
                   "p.audit() の audio-overlap-no-duck は「BGM 役（duck_under / loop を持つ音声）と、"
                   "それがダックしていない音声」の1秒以上の重なりを数える（前景同士は数えない）"],
    "video_sequence": ["返却Objectのdurationは合成尺（sum(実長) - t_dur*(n-1)）へ自動設定される。"
                       "time() を呼ばなくてよく、引数なしの time() も通る"
                       "（生成物が未生成の初回レンダでも、合成尺は入力の probe で確定している）",
                       "自動設定された duration は仮の値。後から speed() / trim() を足すか "
                       "compute(duration=d) で焼き直すと、レイヤー実行後に加工後の尺へ入れ直される"
                       "（time(d) / show(d) で明示した尺は変えない）"],
    "audio_sequence": ["返却Objectのdurationは連結後の実尺へ自動設定される",
                       "Narrationを渡すと字幕もcrossfade込みで配置され、数値@へ追従する",
                       "連結前に各入力を 48kHz・ステレオへ揃える（acrossfade の出力形式は"
                       "先頭入力に従うため）。モノラルは中央定位で各チャンネル約 -3dB"],
    "Project.normalize_audio": [
        "normalize_audio の有無に関わらず、音声は混ぜる前に全入力が 48kHz・ステレオへ揃う"
        "（amix / sidechaincompress の出力形式は先頭入力に従うため）。"
        "モノラル素材は中央定位になり各チャンネル約 -3dB（パンの法則）",
        "入力がすべてモノラルだと出力はステレオ（各チャンネル約 -3dB）になり、"
        "以前のモノラル出力より再生音量が約 3dB 下がる。音量を揃えるには normalize_audio を使う",
        "mode='dynamic'（既定）は1パスの loudnorm で短期ラウドネスを目標へ寄せ続ける。"
        "声の無い BGM だけの区間が持ち上がり、声が入ると沈む（ポンピング）。"
        "ナレーション＋BGM の動画は mode='linear' を推奨",
        "mode='linear' は本レンダの前に音声だけを全編1回 null 出力で流して統合ラウドネスと"
        " true peak を測り（loudnorm print_format=json）、volume=(target-測定値)dB →"
        " aresample → alimiter で仕上げる。区間どうしの音量差は変わらない",
        "mode='linear' の測定結果は __cache__/artifacts/loudness/<鍵>.json に保存され、"
        "音声グラフと音声素材が同じなら再測定しない（target / true_peak / limiter を"
        "変えても測り直さない）。部分レンダ（start/end）も全編の測定値で増幅する",
        "mode='linear' の dry_run は測定しない: 測定コマンドは cache 側に出し、main の"
        "増幅量は volume=<MEASURED_GAIN>dB と表記する（キャッシュ状態に依存しない）",
        "mode='linear' でピークの多い素材（TTS の声など）を大きく持ち上げると、上限を超える"
        "ピークをリミッターが削る分だけ統合ラウドネスが目標よりやや低くなる"
        "（実測: TTS の声 +8.8dB で -0.46 LU）。limiter=False のときは true peak が上限を"
        "超える場合に限り、超えない所で増幅を止める（目標より低くなり、警告を出す）",
    ],
    "voice": ['backend="voicevox"（既定候補）は VOICEVOX（別プロセス）の起動が必要',
              'backend="voicevox" を明示していれば、VOICEVOX 停止中でも一度合成した台詞は'
              "キャッシュ（__cache__/tts/engine_sig.json に控えたエンジン署名で鍵を作る）から"
              "使える。合成が要る台詞だけ ConnectionError",
              "backend=None（既定）はエンジン停止中は edge-tts があれば別の声で合成され"
              "（無ければ RuntimeError）、VOICEVOX のキャッシュは使われない",
              'backend="edge" なら pip install edge-tts で使える（オンライン必須）',
              "speaker の意味はバックエンドごとに違う（数値ID / 音声名）",
              "**tts_kwargs は scriptvedit.tts.tts() へそのまま渡る: cache_dir / host / port、"
              '語の読み替え readings={"金": "カネ"}（合成に渡す文だけ。全バックエンド可）、'
              "VOICEVOX 専用の pre_silence / post_silence（前後の無音・秒）/ "
              "pause_length / pause_scale（句読点の間）/ intonation / volume_scale / "
              "kana（AquesTalk 風カナ）。指定した項目だけがキャッシュ鍵に入る",
              "語が読まれ始める秒は scriptvedit.tts.tts_marks(同じ引数).time_of('語')"
              "（VOICEVOX 専用。wav の先頭からの秒）"],
    "beat_sync": ["scipy が必要（未インストールなら ImportError）",
                  "beats / onsets は秒。keyframes の時刻は u（0..1）なので、"
                  "scriptvedit.beat.beats_to_keyframes へ渡す前に表示尺で割る"
                  "（beats_to_keyframes は単位を変換しない）"],
    "Project.inspect": ["レイヤーを実行しない。render()（dry_run=True でよい）か audit() の"
                        "後に呼ぶ。前に呼ぶとガントチャートではなく p.layer() の登録情報だけの表になる"],
    "Project.layer": ["priority が同じレイヤーは p.layer() を呼んだ順に重なり、後が上"
                      "（レイヤー内の Object は作った順で、後が上）"],
    "slide": ["HTML レンダリングに web 経路（Playwright 等）を使う"],
    "lut": [".cube 形式のみ"],
    "subtitles": ["SRT の文字コードは UTF-8",
                  "フォントは名前で探す。システムに無いフォントは fontsdir で渡す"],
    "flow_graph": [
        "戻り値は FlowGraph（Object ではない）。g.send / g.broadcast / g.state / g.cut で"
        "出来事を足し、g.build(duration=None) で動画 Object にする（None は最後の出来事 + 1 秒）",
        "時刻 t はすべて build() の動画の先頭を 0 とした秒。send は各パケットが止まる秒"
        "（pass は着く秒）のリスト、broadcast は {名前: 届く秒} を返す",
        "send(t, path, n=1, every=0.12, speed=700, color='fg', size=7（直径 px）, trail=0.25, "
        "fate='pass' | ('stop', 名前) | ('drop', 0.6), label=None)。パケットは辺の上を弧長で"
        "等速に進み、曲がった辺でも辺から外れない。止まったパケットは前のパケットとの中心の"
        "間隔 1.6 ×（2つの直径の平均）で手前へ並ぶ（重ならない）",
        "send の label はノードの枠・点・ノードの文字・先に出た札に重ねない（重なるコマでは"
        "隠れ、0.15 秒手前から薄れて、離れてから現れる）。置き場所は画面の上・進行方向の左右・"
        "下から、隠れる間・辺が下を通る間・はみ出しの最も少ないものを選んで保つ（途中のノードの"
        "先で1回・止まる所で1回だけ、一度隠れてから変わりうる。止まった札はパケットより後ろの"
        "左右に残る）",
        "broadcast(t, root, hop=0.35, color='accent', packets=True, ripple=True): 辺の delay"
        "（無ければ hop）で Dijkstra。届いた順に色が変わり波紋が出る。cut は経路を変えない",
        "state(t, node, color=, dim=0〜1, mark='x'|'check'|'none', dur=0.25) / "
        "cut(t, (a, b), dur=0.3)（dim の破線にして薄れさせる）",
        "obj.figure.pos（{名前: (x, y)} キャンバスの px）と obj.figure.arrival（broadcast で"
        "最初に届く秒）を他の Object の配置・時刻合わせに使える",
        "上限: ノード 5,000・辺 10,000・同時に見えるパケット 2,000。layered / radial で根から"
        "辿れないノードがあれば ValueError。文字の重なり・はみ出しは警告する",
        "label_pos='auto' の点の文字は辺・ほかのノードと重ならない側へ置く（layered は葉だけ"
        "流れの向き）。どの側も辺が通るときは文字の下に panel 色の板を敷いて警告する",
        "途中の box を通るパケットは box の中では描かない。止まる列は手前のノードを跨いで並ぶ",
        "模式図であって実際の経路ではない（「模式図」の注記は呼び出し側が付ける）。"
        "構築（flow_graph() の呼び出し）にも numpy・opencv-python・Pillow が要る"
        "（pip install \"scriptvedit[figures]\"）",
    ],
    "flow_tree": [
        "葉が 500 を超える段は点の塊（直径 5px・seed で散らす）にし、そこへの broadcast の"
        "パケットは親1つあたり 4 個に束ねる（量は束ねた分の合計）。点の塊には波紋を出さない",
        "amount を渡すとパケットの直径が √(量) に比例する（面積が量に比例し、合計は保たれる）。"
        "g.amount に {名前: 量}、g.levels に段ごとの名前",
        "radial の根は1つ（levels[0] は 1）。t=None なら配らない（g.broadcast を自分で呼ぶ）",
        "curve= は子の多い親からの辺で自動で弱まる（子 40 で約 0.04。渦に見えない）",
    ],
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
    "flip": ("img <= flip()                # 左右反転\n"
             "img2 <= flip(vertical=True)  # 上下反転"),
    "wipe": "img <= wipe(direction='left')",
    "text": "t = text('こんにちは', x=0.5, y=0.2, size=48, color='white')\nt.time(3) <= fade(lambda u: u)",
    "typewriter": "typewriter('タイプ表示', cps=12).time(4)",
    "text_image": ("t = text_image([('犯人は、', {}), ('正規表現が1本', {'color': 'red'})],\n"
                   "               size=72, border=4, max_width=1600)\n"
                   "t.time(3) <= move(x=0.5, y=0.5, anchor='center')\n"
                   "boom = text_image('崩壊', size=160, border=6, padding=60)\n"
                   "boom.time(2) <= explode_to(max_pixels=8000, expand=300)"),
    "text_transition": (
        "t = text_transition(['Fundation', 'Foundation'], enter='drop', size=120)\n"
        "t.time(3) <= move(x=0.5, y=0.5, anchor='center')\n"
        "text_transition([r'\\s+$', [r'\\s+', ('+', {'color': '#e0241b'}), '$']], duration=0.8)\n"
        "text_transition([official, [(r'\\s+$', {'size': 150})]], unit=r'\\\\.|.',\n"
        "                leave='fall', anchor='center', font=mono)"),
    "odometer": (
        "odometer(2**31 - 1, 2**31, base=2, digits=32, signed='twos', group=8, sep=' ')\n"
        "odometer(2147483647, -2147483648, group=3)\n"
        "odometer.text('2038-01-19 03:14:07', '1901-12-13 20:45:52', roll_dir='down')"),
    "counter": ("counter(0, 100, format='%d%%').time(3)\n"
                "counter(0, 1234567, format='¥%,d', easing='ease_out_cubic', border=3).time(3)"),
    "subtitles": "subtitles('subs.srt', style='FontName=Meiryo,FontSize=36', fontsdir=here('fonts'))",
    "blend_mode": "obj <= blend_mode('screen')",
    "mask": "obj <= mask('mask_circle.png')",
    "opacity": "obj <= opacity(0.5)",
    "tint": ("line <= tint('red')                       # 白い線画を赤に\n"
             "line.time(3) <= tint('#ffcc00', amount=ramp(1.0, 1.5))   # 1.0〜1.5 秒で色が付く\n"
             "icon <= tint('blue', mode='fill')         # 黒い線画も青に"),
    "ramp": ("obj.time(6) <= fade(ramp(0, 0.4) * (1 - ramp(0.4, 0, from_end=True)))\n"
             "obj.time(6) <= wipe('left', progress=ramp(1.2, 1.6, ease_out_cubic))"),
    "keyframes_sec": "obj.time(6) <= fade(keyframes_sec((0, 0), (0.25, 1), (5.5, 1), (6, 0)))",
    "speed": "clip_.time(4) <= speed(2.0)   # 2倍速",
    "reverse": "clip_ <= reverse()",
    "morph_to": "img <= morph_to(Object(asset('images/target.png')))",
    "fly_to": ("red = text_image('今も、書く人しだい', size=44, color='#e0241b')\n"
               "close = text_image('事件は、まだ、終わっていない。', size=96, color='#e0241b')\n"
               "# red の中心から左へ 560px・上へ 300px の所で close になる（2 秒。後は close を保持）\n"
               "red.time(3) <= fly_to(close, offset=(-560, -300), arc=0.2, stagger=0.35,\n"
               "                      duration=2.0)\n"
               "red <= move(x=1520 / 1920, y=800 / 1080, anchor='center')"),
    "slideshow": "slideshow(['a.png', 'b.png', 'c.png'], each=3.0, transition='fade')",
    "transition": "transition(obj_a, obj_b, kind='wipeleft', duration=1.0)",
    "stills": ("pages = stills([('p1.png', 6.4), ('p2.png', 7.1), ('p3.png', 5.0)])\n"
               "pages.time(20)            # 総尺 18.5 秒より長い分は最後の絵が残る\n"
               "voice2 = Object('v2.wav') @ pages.starts[1]   # 2枚目の開始に音声を合わせる\n"
               "stills([('a.png', 0), ('b.png', 3.2), ('c.png', 9)], total=12)   # 開始秒で書く形"),
    "frames": ("def draw(i):                       # i はコマ番号（時刻は i / fps 秒）\n"
               "    im = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0))\n"
               "    ImageDraw.Draw(im).rectangle([100, 500, 100 + i * 20, 560], fill='white')\n"
               "    return im\n"
               "bar = frames(draw, duration=2.0, key=['bar', 1])   # 描き方を変えたら key を変える\n"
               "bar.time(5)                        # 2 秒より後は最後のコマが残る"),
    "slots": ("s = slots()\n"
              "s.row('shelf', 200, label='項目の上限', capacity=200, elide=(6, 3))\n"
              "s.fill(1.0, 'shelf', 400, dur=2.5)    # 倍の 400 個 → 200 を超えた分があふれる\n"
              "s.halt(3.8)\n"
              "s.build().time() <= move(x=0.5, y=0.5, anchor='center')\n"
              "t = slots()\n"
              "t.row('rule', 21, label='受け取る項目')\n"
              "t.row('input', 20, label='渡す項目', ghost=[21])\n"
              "t.link(1.2, ('rule', 21), ('input', 21))  # 先が ghost なので赤くなる"),
    "globe": ("g = globe(projection='plate', size=(1400, 700))   # 陸は同梱の地球・中心は自動\n"
              "g.points(sites)                                # 出典を書くか「模式図」と明記する\n"
              "g.arc((37.8, -122.4), (35.7, 139.7), t=1.0)    # 太平洋を渡る弧も1本につながる\n"
              "g.ripple((35.7, 139.7), t=1.8)\n"
              "g.build().time(4) <= move(x=0.5, y=0.5, anchor='center')"),
    "keyframes": "img <= scale(keyframes((0, 1.0), (0.5, 1.5), (1, 1.0), easing=ease_in_out_sine))",
    "avolume": "bgm <= avolume(0.3)",
    "loop": ("bgm <= loop() & duck_under(narration_audio)   # time() は呼ばない（総尺までループ）\n"
             "bgm2.time(30) <= loop()                        # 30秒までループ"),
    "duck_under": ("bgm <= duck_under(voice_obj, ratio=8)\n"
                   "# 相手が複数（どれかが鳴っている間は下がる）: "
                   "bgm <= duck_under(n1, n2, n3, ratio=8)"),
    "sfx": ("sfx('効果音.mp3', at=2.5, volume=0.8)              # 1回だけ\n"
            "sfx('効果音.mp3', at=[0.5, 1.5, 3.0], volume=0.8)  # 同じ音を複数回"),
    "narrate": ("n = narrate('長い読み上げ原稿', speaker=1, subtitle_text='短い字幕', "
                "subtitle_max_chars=14, subtitle_safe_area=0.05)"),
    "group": "g = group(obj_a, obj_b)\ng <= move(x=lambda u: u)",
    "pip": "video <= pip(x=0.75, y=0.75, scale=0.3, radius=12)",
    "anchor": "obj.time(3, name='intro')\npause.until('intro.end')",
    "scene": "with scene('導入', 5.0):\n    ...",
    "flow_graph": ("g = flow_graph({'lb': {'label': 'ロードバランサー', 'shape': 'box'},\n"
                   "                's1': {'label': 'サーバー1'}, 's2': {'label': 'サーバー2'}},\n"
                   "               [('lb', 's1'), ('lb', 's2')], layout='layered')\n"
                   "g.send(0.5, ['lb', 's1']); g.send(0.9, ['s2', 'lb'], fate=('drop', 0.5))\n"
                   "g.state(2.0, 's2', dim=0.6, mark='x'); g.cut(2.0, ('lb', 's2'))\n"
                   "obj = g.build()          # obj.figure.pos['s1'] でノードの位置"),
    "flow_tree": ("g = flow_tree([1, 50, 3000], t=0.5, amount=400000)   # 根から一斉に配る\n"
                  "obj = g.build()\n"
                  "obj.figure.arrival['L2_0']      # 葉に届く秒"),
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
                "pause.time(5) を挟む / obj @ 12 で絶対配置する / "
                "obj.time(3, name='intro') と "
                "pause.until('intro.end') のアンカーで待ち合わせる、のいずれかを使う。"
                "アンカー名は Project 全体で共有されるので、別レイヤーで打った "
                "name= を pause.until() や @ 'intro.end' から参照できる。"
                "a >> b（直後連結）は同じレイヤーの中だけで使える"
                "（a を変数で参照するが、レイヤー .py は別々の名前空間で実行されるため"
                "別レイヤーの Object は見えない）。"
                "動画の総尺は全レイヤーの最大値から自動算出される。",
    },
    {
        # 順次配置の並び順は「Object を作った順」（Object.__init__ が
        # proj.objects へ append し、_resolve_anchors はその順でカーソルを進める）。
        # time()/show() を呼んだ順ではないので、音声を作って time() で進めた後に
        # 字幕を作ると字幕が後ろへずれる（実制作で踏んだ）。エラーにはならない。
        "id": "object_registered_at_creation",
        "topic": "タイムライン",
        "severity": "warning",
        "applies_to": ["Object", "Object.time", "Object.show", "text"],
        "text": "Object（text() 等のファクトリが作るものも含む）は、コンストラクタを"
                "呼んだ時点のカーソル位置で登録される。カーソル上の並び順は作った順で決まり、"
                "後から呼んだ time() / show() では動かない。"
                "音声 v を作って v.time(4) でカーソルを進めた後に字幕 t = text(...) を作ると、"
                "t.show(4) は 4 秒の位置（音声の後ろ）に出る。音声と字幕を同時に出すなら、"
                "字幕の text() を先に作ってから音声 Object を作る（または narrate() を使う）。"
                "priority が同じ Object は登録順に重なり、後に登録したものが上になる。",
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
        "id": "text_no_terminal_frame_effect",
        "topic": "テキスト",
        "severity": "error",
        "applies_to": ["text", "typewriter", "counter", "morph_to", "explode_to",
                       "assemble_from", "fly_to", "text_image"],
        "text": "text/typewriter/counter（drawtext 系。実体の画像を持たない）には "
                "morph_to / explode_to / assemble_from / fly_to を掛けられず、compute() もできない"
                "（どちらも ValueError）。文字を粒子化・モーフするときは "
                "text_image() で透過 PNG の画像 Object にしてから適用する。"
                "morph_to / fly_to の target / assemble_from の source も同じ。",
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
        "applies_to": ["morph_to", "explode_to", "assemble_from", "fly_to"],
        "text": "morph_to / explode_to / assemble_from / fly_to は終端フレーム生成Effect。"
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
        "audio_chain": "clip <= avolume(0.6) & atrim(3)   # AudioEffect 同士も &。"
                       "Effect と AudioEffect は & で混ぜられない（TypeError。別々に <=）",
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
    "秒で書く時間": ["elapsed", "remaining", "ramp", "keyframes_sec"],
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
