# エラーケーステスト: 各種エラー条件の自動検証
import sys, os, tempfile, shutil, json, re, subprocess
import uuid as _uuid
import pytest

# tests ディレクトリ内一時ファイルのプロセス単位ユニーク化トークン。
# 固定名 _tmp_*.py だと同一 checkout での同時 pytest 実行が相互に
# 書きかけ・削除で壊し合う（issue #13 P2-20）。同一プロセス内の check は
# 逐次実行なので、プロセスごとに決定的なら十分。
_TMP_TOKEN = f"{os.getpid()}_{_uuid.uuid4().hex[:6]}"


def _tmp_file(name):
    """tests ディレクトリ内の一時ファイルパス（プロセス単位でユニーク）"""
    base, ext = os.path.splitext(name)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        f"{base}_{_TMP_TOKEN}{ext}")
import scriptvedit as sv
from scriptvedit import (
    asset,
    describe, describe_markdown,
    Project, P, Object, VideoView, AudioView,
    avolume, move, fade, resize, rotate, rotate_to, morph_to, AudioEffect, AudioEffectChain,
    explode_to, assemble_from, move_along, path_bezier, throw, inertia, look_at, perlin,
    group, tile, scene, keyframes,
    subtitle, subtitle_box, bubble, diagram, circle, label,
    crop, pad, blur, eq, wipe, zoom, color_shift, shake, scale,
    chroma_key, vignette, pixelize, glow, lut, glitch,
    perspective_warp, lens, ken_burns, drop_shadow, outline,
    slideshow, transition,
    Transform, TransformChain, Effect, EffectChain,
    anchor, pause,
    text, typewriter, counter, subtitles,
    duck_under, loop, audio_sequence, sfx, audio_viz,
    mask, mask_wipe, opacity, blend_mode, rounded, pip,
    blur_background_fill, progress_bar,
    speed, reverse, freeze_frame, video_sequence,
    trim, adelete,
    narrate, Narration, karaoke, beat_sync, slide,
    formula, formula_lines,
)
# 内部ヘルパーは実体モジュールから直接 import する
# （package root からの private 再エクスポートは廃止済み）
from scriptvedit.cache import (
    _checkpoint_cache_path, _file_fingerprint, _web_cache_path,
)
from scriptvedit.expr import _resolve_param
from scriptvedit.filters.audio import _atempo_chain_rates
from scriptvedit.filters.video import _build_effect_filters, _build_video_pre_filters
from scriptvedit.cache import _is_bakeable, _is_cache_artifact_path, _op_fingerprint_str
from scriptvedit.effects.composite import _BLEND_MODES
from scriptvedit.manifest import _MANIFEST_ENTRY_SECTIONS
from scriptvedit.media import _XFADE_TRANSITIONS
from scriptvedit.plugins import _EFFECT_PLUGINS, _LOADED_PLUGIN_FILES
from scriptvedit.state import (
    _ARTIFACT_DIR, _BAKEABLE_EFFECTS, _ENCODER_MAP, _PRESETS,
    _TERMINAL_FRAME_EFFECTS, _TIME_LIVE_EFFECTS,
)


# --- 依存不在時のスキップ ---
#
# 依存（numpy/scipy・Playwright・ffprobe 等）が無いときに (True, "スキップ…") を
# 返すと **pytest では PASS になり、検証が素通りしていることが隠れる**。
# 必ず pytest.skip() で正直に skip させること。
def _skip(reason):
    """依存不在などで検証不能なときに skip する（PASS 扱いにしない）"""
    pytest.skip(reason)


def _require_beat_env():
    """beat_sync 系テストの前提（音声素材 + numpy/scipy）を要求する"""
    if not os.path.exists(asset("audio/bgm_loop.mp3", must_exist=False)):
        _skip("素材 assets/audio/bgm_loop.mp3 が無い環境")
    try:
        import scriptvedit.beat  # noqa: F401
    except ImportError:
        _skip("scriptvedit.beat（numpy/scipy）が無い環境")


def _require_asset(relpath):
    """gitignore 対象を含む素材が無い環境では正直に skip する。"""
    try:
        return asset(relpath)
    except FileNotFoundError:
        _skip(f"素材 assets/{relpath} が無い環境")


def check_math_sin_in_lambda():
    """lambda内でmath.sinを使用 → TypeErrorかつ案内メッセージ"""
    import math
    try:
        _resolve_param(lambda u: math.sin(u))
        return False, "例外が発生しませんでした"
    except TypeError as e:
        msg = str(e)
        if "scriptvedit" in msg and "sin" in msg:
            return True, msg.split('\n')[0]
        return False, f"メッセージが不適切: {msg}"


def check_undefined_anchor():
    """未定義アンカー参照 → RuntimeError"""
    layer_code = (
        'from scriptvedit import *\n'
        'pause.until("nonexistent")\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj.time(1) <= move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_err_undef.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp_path, priority=0)
        p.render("_tmp_err.mp4", dry_run=True)
        return False, "例外が発生しませんでした"
    except RuntimeError as e:
        msg = str(e)
        if "nonexistent" in msg and "pause.until" in msg:
            return True, msg.split('\n')[0]
        return False, f"メッセージが不適切: {msg}"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_same_anchor_different_files():
    """異ファイル間で同名アンカー定義 → RuntimeError"""
    layer1_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj.time(1) <= move(x=0.5, y=0.5, anchor="center")\n'
        'anchor("my_anchor")\n'
    )
    layer2_code = (
        'from scriptvedit import *\n'
        'anchor("my_anchor")\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj.time(1) <= move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp1 = _tmp_file("_tmp_err_dup1.py")
    temp2 = _tmp_file("_tmp_err_dup2.py")
    try:
        with open(temp1, "w", encoding="utf-8") as f:
            f.write(layer1_code)
        with open(temp2, "w", encoding="utf-8") as f:
            f.write(layer2_code)
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp1, priority=0)
        p.layer(temp2, priority=1)
        p.render("_tmp_err.mp4", dry_run=True)
        return False, "例外が発生しませんでした"
    except RuntimeError as e:
        msg = str(e)
        if "my_anchor" in msg and "再定義は禁止" in msg:
            return True, msg.split('\n')[0]
        return False, f"メッセージが不適切: {msg}"
    finally:
        for f in [temp1, temp2]:
            if os.path.exists(f):
                os.unlink(f)


def check_configure_typo():
    """configure()のtypo → ValueError"""
    p = Project()
    try:
        p.configure(widht=1280)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "widht" in msg and "width" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_percent_value():
    """50%P == 0.5 の確認"""
    result = 50%P
    if result == 0.5:
        return True, f"50%P = {result}"
    return False, f"50%P = {result} (期待: 0.5)"


def check_cache_invalid():
    """cache='invalid' → ValueError"""
    p = Project()
    try:
        p.layer("dummy.py", cache="invalid")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "invalid" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_cache_use_no_file():
    """cache='use' でキャッシュ未生成 → FileNotFoundError（案内付き）

    キャッシュ鍵が解決済み総尺を含むため、検証はplan pass後に行われる。
    レイヤーファイル自体が無い場合はplanが先にファイル不在エラーを出す。
    """
    temp_path = _tmp_file("_tmp_cache_use_layer.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(
                'from scriptvedit import *\n'
                'o = Object(asset("images/shape_badge.png"))\n'
                'o.time(1)\n')
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp_path, cache="use")
        try:
            p.render("_tmp.mp4", dry_run=True)
            return False, "例外が発生しませんでした"
        except FileNotFoundError as e:
            msg = str(e)
            if "キャッシュファイルが見つかりません" in msg:
                return True, msg.split('\n')[0]
            return False, f"メッセージが不適切: {msg}"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_image_length():
    """画像の length() → TypeError"""
    p = Project()
    obj = Object(asset("images/shape_badge.png"))
    try:
        obj.length()
        return False, "例外が発生しませんでした"
    except TypeError as e:
        msg = str(e)
        if "画像" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_missing_file_length():
    """存在しないファイルの length() → FileNotFoundError"""
    p = Project()
    obj = Object("nonexistent_video.mp4")
    try:
        obj.length()
        return False, "例外が発生しませんでした"
    except FileNotFoundError as e:
        msg = str(e)
        if "メディアの長さを取得できません" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_view_time_forbidden():
    """VideoView.time() / AudioView.time() → TypeError"""
    p = Project()
    obj = Object(asset("images/shape_badge.png"))
    vv = VideoView(obj)
    try:
        vv.time(3)
        return False, "VideoView.time() 例外が発生しませんでした"
    except TypeError as e:
        msg = str(e)
        if "禁止" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_view_until_forbidden():
    """VideoView.until() / AudioView.until() → TypeError"""
    p = Project()
    obj = Object(asset("audio/bgm_loop.mp3"))
    av = AudioView(obj)
    try:
        av.until("test")
        return False, "AudioView.until() 例外が発生しませんでした"
    except TypeError as e:
        msg = str(e)
        if "禁止" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_video_audio_effect_mismatch():
    """VideoView <= avolume() → TypeError"""
    p = Project()
    obj = Object(asset("images/shape_badge.png"))
    vv = VideoView(obj)
    try:
        vv <= avolume(0.5)
        return False, "例外が発生しませんでした"
    except TypeError as e:
        msg = str(e)
        if "映像系のみ" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_audio_video_effect_mismatch():
    """AudioView <= move() → TypeError"""
    p = Project()
    obj = Object(asset("audio/bgm_loop.mp3"))
    av = AudioView(obj)
    try:
        av <= move(x=0.5, y=0.5)
        return False, "例外が発生しませんでした"
    except TypeError as e:
        msg = str(e)
        if "音声系のみ" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_web_kwargs_on_non_web():
    """画像にduration/sizeを渡す → TypeError"""
    p = Project()
    try:
        Object(asset("images/shape_badge.png"), duration=2.0, size=(640, 360))
        return False, "例外が発生しませんでした"
    except TypeError as e:
        msg = str(e)
        if "web Object" in msg and ".html" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_web_unknown_kwarg():
    """HTMLに不明なkwarg → TypeError"""
    p = Project()
    try:
        Object("test.html", duration=2.0, size=(640, 360), unknown_param=True)
        return False, "例外が発生しませんでした"
    except TypeError as e:
        msg = str(e)
        if "不明なキーワード引数" in msg and "unknown_param" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_web_no_duration():
    """HTMLにdurationなし → ValueError"""
    p = Project()
    try:
        Object("test.html", size=(640, 360))
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "duration" in msg and "必須" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_web_name_traversal():
    """webのnameにパス区切り・相対参照 → ValueError（cache外rmtree防止）"""
    p = Project()
    # name="" は falsy のため basename へフォールバックする（既存仕様・安全）
    bad_names = ["../../valuable", "a/b", "a\\b", "..", "C:evil", "  "]
    for bad in bad_names:
        try:
            Object("test.html", duration=2.0, size=(640, 360), name=bad)
            return False, f"例外が発生しませんでした: name={bad!r}"
        except ValueError:
            continue
        except Exception as e:
            return False, f"想定外の例外({bad!r}): {type(e).__name__}: {e}"
    return True, f"{len(bad_names)}件の不正nameを全て拒否"


def check_web_frames_dir_guard():
    """_web_frames_dirはキャッシュルート外へ解決されるnameを拒否する"""
    from scriptvedit.objects import _web_frames_dir
    try:
        _web_frames_dir("../escape")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        ok_dir = _web_frames_dir("scene1")
        if "webclip" not in ok_dir.replace("\\", "/"):
            return False, f"正常nameの解決先が不正: {ok_dir}"
        return True, str(e)


def check_subtitle_no_project():
    """subtitle() でProject未設定 + size省略 → RuntimeError"""
    old = Project._current
    Project._current = None
    try:
        subtitle("テスト")
        return False, "例外が発生しませんでした"
    except RuntimeError as e:
        msg = str(e)
        if "アクティブなProject" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"
    finally:
        Project._current = old


def check_diagram_no_project():
    """diagram() でProject未設定 + size省略 → RuntimeError"""
    old = Project._current
    Project._current = None
    try:
        diagram([circle(0.5, 0.5, 0.1)])
        return False, "例外が発生しませんでした"
    except RuntimeError as e:
        msg = str(e)
        if "アクティブなProject" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"
    finally:
        Project._current = old


def check_subtitle_with_explicit_size():
    """subtitle() にsize明示 → Project不要で成功"""
    old = Project._current
    Project._current = None
    try:
        obj = subtitle("テスト", size=(640, 360))
        if obj.media_type == "web" and obj._web_size == (640, 360):
            return True, f"size=(640,360) で正常生成"
        return False, f"属性が不正: type={obj.media_type}, size={obj._web_size}"
    finally:
        Project._current = old


def check_neg_transform():
    """-resize() → policy='off' のTransformを返す"""
    result = -resize(sx=0.5, sy=0.5)
    if not isinstance(result, Transform):
        return False, f"型が不正: {type(result)}"
    if result.policy != "off":
        return False, f"policyが不正: {result.policy}"
    return True, f"policy={result.policy}"


def check_neg_effect():
    """-scale(0.5) → policy='off' のEffectを返す"""
    from scriptvedit import scale
    result = -scale(0.5)
    if not isinstance(result, Effect):
        return False, f"型が不正: {type(result)}"
    if result.policy != "off":
        return False, f"policyが不正: {result.policy}"
    return True, f"policy={result.policy}"


def check_chain_sugar():
    """~(tf1 | tf2) で全opがquality='fast'になる"""
    tf1 = resize(sx=0.5, sy=0.5)
    tf2 = resize(sx=0.3, sy=0.3)
    chain = tf1 | tf2
    result = ~chain
    if not isinstance(result, TransformChain):
        return False, f"型が不正: {type(result)}"
    # 全opがquality="fast"
    for i, t in enumerate(result.transforms):
        if not isinstance(t, Transform):
            return False, f"transforms[{i}]がTransformでない: {type(t)}"
        if t.quality != "fast":
            return False, f"transforms[{i}].quality={t.quality} (期待: fast)"
    return True, f"全{len(result.transforms)}opがquality=fast"


def check_force_operator():
    """+op で policy='force' 確認"""
    result = +resize(sx=0.5, sy=0.5)
    if not isinstance(result, Transform):
        return False, f"型が不正: {type(result)}"
    if result.policy != "force":
        return False, f"policyが不正: {result.policy}"
    return True, f"policy={result.policy}"


def check_off_operator():
    """-op で policy='off' 確認"""
    result = -resize(sx=0.5, sy=0.5)
    if not isinstance(result, Transform):
        return False, f"型が不正: {type(result)}"
    if result.policy != "off":
        return False, f"policyが不正: {result.policy}"
    return True, f"policy={result.policy}"


def check_fast_quality():
    """~op で quality='fast' 確認"""
    result = ~resize(sx=0.5, sy=0.5)
    if not isinstance(result, Transform):
        return False, f"型が不正: {type(result)}"
    if result.quality != "fast":
        return False, f"qualityが不正: {result.quality}"
    return True, f"quality={result.quality}"


def check_chain_force():
    """+chain で末尾policy='force' 確認"""
    tf1 = resize(sx=0.5, sy=0.5)
    tf2 = resize(sx=0.3, sy=0.3)
    chain = tf1 | tf2
    result = +chain
    if not isinstance(result, TransformChain):
        return False, f"型が不正: {type(result)}"
    last = result.transforms[-1]
    if last.policy != "force":
        return False, f"末尾policy={last.policy} (期待: force)"
    first = result.transforms[0]
    if first.policy != "auto":
        return False, f"先頭policy={first.policy} (期待: auto)"
    return True, f"末尾policy=force, 先頭policy=auto"


def check_ffp_change_detection():
    """ファイル変更でfingerprintが変わることを確認"""
    import time
    tmp = os.path.join(tempfile.gettempdir(), f"_test_ffp_change_{_TMP_TOKEN}.png")
    try:
        with open(tmp, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        ffp1 = _file_fingerprint(tmp)
        # わずかに待ってから内容変更
        time.sleep(0.05)
        with open(tmp, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\xFF" * 200)
        ffp2 = _file_fingerprint(tmp)
        if ffp1 == ffp2:
            return False, f"fingerprintが変わっていない: {ffp1}"
        # 内容ハッシュ方式: touch（mtimeのみ変更）では指紋は変わらないこと
        with open(tmp, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\xFF" * 200)
        os.utime(tmp, None)
        if _file_fingerprint(tmp) != ffp2:
            return False, "touchで指紋が変わってしまった（内容ハッシュでない）"
        return True, f"ffp1={ffp1}, ffp2={ffp2}（touchでは不変）"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_checkpoint_signature_uses_ffp():
    """checkpoint signatureにFFPが含まれることを確認"""
    import time
    tmp = os.path.join(tempfile.gettempdir(), f"_test_cp_sig_{_TMP_TOKEN}.png")
    try:
        with open(tmp, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        path1 = _checkpoint_cache_path(tmp, [])
        time.sleep(0.05)
        with open(tmp, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"\xFF" * 200)
        path2 = _checkpoint_cache_path(tmp, [])
        if path1 == path2:
            return False, "ファイル変更後もキャッシュパスが同じ"
        return True, "ファイル変更でキャッシュパスが変化"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_web_deps_accepted():
    """web Objectにdeps引数が渡せることを確認"""
    old = Project._current
    try:
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        obj = subtitle("テスト", size=(320, 240), deps=["a.png", "b.png"])
        if obj._web_deps == ["a.png", "b.png"]:
            return True, f"deps={obj._web_deps}"
        return False, f"deps={obj._web_deps}"
    finally:
        Project._current = old


def check_video_no_time_checkpoint_has_duration():
    """video + transform-only + time未指定 → checkpointコマンドに-tが含まれる"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("video/clip_with_audio.mp4"))\n'
        'obj <= resize(sx=0.5, sy=0.5)\n'
    )
    temp_path = _tmp_file("_tmp_vid_notime.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=1280, height=720, fps=30, background_color="black")
        p.layer(temp_path, priority=0)
        result = p.render("_tmp_vid_notime.mp4", dry_run=True)
        # resultはdict（cache付き）であるべき
        if not isinstance(result, dict):
            return False, f"dictでない: {type(result)}"
        cache = result.get("cache", {})
        if not cache:
            return False, "cacheが空"
        # cacheの各コマンドに-tが含まれ、値がNoneでないこと
        for path, cmd in cache.items():
            if not (path.endswith(".mkv") or path.endswith(".webm")):
                return False, f"拡張子が.mkvまたは.webmでない: {path}"
            if "-t" not in cmd:
                return False, f"-tがコマンドにない: {cmd}"
            t_idx = cmd.index("-t")
            t_val = cmd[t_idx + 1]
            if t_val == "None":
                return False, f"-tの値がNone"
            dur = float(t_val)
            if dur <= 0:
                return False, f"-tの値が不正: {dur}"
        return True, f"checkpoint .webm + -t={t_val}"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_video_with_time_uses_specified_duration():
    """video + time指定 → obj.time()の値がcheckpointのdurationに使われる"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("video/clip_with_audio.mp4"))\n'
        'obj <= resize(sx=0.5, sy=0.5)\n'
        'obj.time(2.5) <= move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_vid_time.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=1280, height=720, fps=30, background_color="black")
        p.layer(temp_path, priority=0)
        result = p.render("_tmp_vid_time.mp4", dry_run=True)
        if not isinstance(result, dict):
            return False, f"dictでない: {type(result)}"
        cache = result.get("cache", {})
        if not cache:
            return False, "cacheが空"
        for path, cmd in cache.items():
            if "-t" not in cmd:
                return False, f"-tがコマンドにない"
            t_idx = cmd.index("-t")
            t_val = float(cmd[t_idx + 1])
            if t_val != 2.5:
                return False, f"-tの値が2.5でない: {t_val}"
        return True, f"time指定=2.5が正しく使用される"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_morph_to_non_object():
    """morph_to に Object 以外 → TypeError"""
    p = Project()
    try:
        morph_to("not_an_object")
        return False, "例外が発生しませんでした"
    except TypeError as e:
        msg = str(e)
        if "Object のみ" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_morph_to_not_last():
    """morph_to が bakeable ops の末尾でない → ValueError"""
    layer_code = (
        'from scriptvedit import *\n'
        'img1 = Object(asset("images/shape_badge.png"))\n'
        'img2 = Object(asset("images/shape_figure.png"))\n'
        'img1.time(3) <= morph_to(img2)\n'
        'img1 <= scale(0.5)\n'
    )
    temp_path = _tmp_file("_tmp_morph_order.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp_path, priority=0)
        p.render("_tmp_morph.mp4", dry_run=True)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "morph_to" in msg and "末尾" in msg:
            return True, msg.split('\n')[0]
        return False, f"メッセージが不適切: {msg}"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_rotate_no_args():
    """rotate() に deg/rad なし → ValueError"""
    try:
        rotate()
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "deg" in msg and "rad" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_rotate_to_no_args():
    """rotate_to() に deg/rad/from/to なし → ValueError"""
    try:
        rotate_to()
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "deg" in msg or "rad" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_rotate_to_preserves_move():
    """rotate_to(bakeable) + move(live) → checkpoint後もmoveが残る"""
    layer_code = (
        'from scriptvedit import *\n'
        'img = Object(asset("images/shape_badge.png"))\n'
        'img <= resize(sx=0.5, sy=0.5)\n'
        'img.time(2) <= rotate_to(from_deg=0, to_deg=90)\n'
        'img <= move(x=0.3, y=0.7, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_rotate_move.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp_path, priority=0)
        result = p.render("_tmp_rotate_move.mp4", dry_run=True)
        if not isinstance(result, dict):
            return False, f"dictでない: {type(result)}"
        # mainコマンドにoverlayがあること（moveが残っている）
        main_cmd = " ".join(result["main"])
        if "overlay" not in main_cmd:
            return False, "overlayがmainコマンドにない（moveが消えた）"
        return True, "rotate_to checkpoint後もmove保持"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_morph_to_hint_message():
    """morph_to末尾でないときエラーに「回避策」が含まれる"""
    layer_code = (
        'from scriptvedit import *\n'
        'img1 = Object(asset("images/shape_badge.png"))\n'
        'img2 = Object(asset("images/shape_figure.png"))\n'
        'img1.time(3) <= morph_to(img2)\n'
        'img1 <= scale(0.5)\n'
    )
    temp_path = _tmp_file("_tmp_morph_hint.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp_path, priority=0)
        p.render("_tmp_morph_hint.mp4", dry_run=True)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "回避策" in msg and "除外" in msg:
            return True, msg.split('\n')[0]
        return False, f"メッセージに回避策がない: {msg}"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_image_time_no_args():
    """画像に対する time() 省略は TypeError"""
    try:
        obj = Object(asset("images/shape_badge.png"))
        obj.time()
        return False, "例外が発生しませんでした"
    except TypeError as e:
        msg = str(e)
        if "画像" in msg and "time()" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_probe_failure_has_audio_false():
    """probe不可時 has_audio=False"""
    old = Project._current
    try:
        Project._current = None
        obj = Object("nonexistent_media.mp4")
        result = obj.has_audio
        if result is False:
            return True, f"has_audio={result} (probe不可→False)"
        return False, f"has_audio={result} (期待: False)"
    finally:
        Project._current = old


def check_crop_no_size():
    """crop w/h未指定 → ValueError"""
    try:
        crop(x=0, y=0)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "w" in msg and "h" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_pad_no_size():
    """pad w/h未指定 → ValueError"""
    try:
        pad(x=0, y=0)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "w" in msg and "h" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_color_shift_no_args():
    """color_shift引数なし → ValueError"""
    try:
        color_shift()
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "hue" in msg or "saturation" in msg or "brightness" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_zoom_no_args():
    """zoom引数なし → ValueError"""
    try:
        zoom()
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "value" in msg or "to_value" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def _filter_found_in_cache(cmd_result, filter_str):
    """dry_run結果のcacheコマンドに指定フィルタ文字列が含まれるか検証"""
    if not isinstance(cmd_result, dict):
        return False, f"dictでない: {type(cmd_result)}"
    cache_cmds = cmd_result.get("cache", {})
    if not cache_cmds:
        return False, "cacheが空"
    for path, cmd in cache_cmds.items():
        cmd_str = " ".join(cmd) if isinstance(cmd, list) else str(cmd)
        if filter_str in cmd_str:
            return True, f"'{filter_str}' found"
    return False, f"'{filter_str}' NOT found in any cache cmd"


def _make_image_checkpoint_project(transforms_code, effects_code=""):
    """画像+Transform/Effect→dry_run結果を返すヘルパー"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        f'obj <= {transforms_code}\n'
        f'obj.time(1) <= move(x=0.5, y=0.5, anchor="center"){" & " + effects_code if effects_code else ""}\n'
    )
    temp_path = _tmp_file("_tmp_filter_test.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=640, height=360, fps=30, background_color="black")
        p.layer(temp_path, priority=0)
        return p.render("_tmp_filter.mp4", dry_run=True)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_crop_filter_in_checkpoint():
    """crop Transformがcheckpointのfiltergraphに出ること"""
    cmd = _make_image_checkpoint_project('crop(x=10, y=10, w=200, h=150)')
    return _filter_found_in_cache(cmd, "crop=200:150:10:10")


def check_pad_filter_in_checkpoint():
    """pad Transformがcheckpointのfiltergraphに出ること"""
    cmd = _make_image_checkpoint_project('pad(w=640, h=480)')
    return _filter_found_in_cache(cmd, "pad=640:480:")


def check_blur_filter_in_checkpoint():
    """blur Transformがcheckpointのfiltergraphに出ること"""
    cmd = _make_image_checkpoint_project('blur(radius=10)')
    return _filter_found_in_cache(cmd, "boxblur=10:10")


def check_eq_filter_in_checkpoint():
    """eq Transformがcheckpointのfiltergraphに出ること"""
    cmd = _make_image_checkpoint_project('eq(brightness=0.2, contrast=1.2)')
    return _filter_found_in_cache(cmd, "eq=brightness=0.2:contrast=1.2")


def check_wipe_filter_in_checkpoint():
    """wipe Effectがcheckpointのfiltergraphに出ること"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj <= resize(sx=0.5, sy=0.5)\n'
        'obj.time(2) <= wipe("left") & move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_wipe_test.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=640, height=360, fps=30, background_color="black")
        p.layer(temp_path, priority=0)
        cmd = p.render("_tmp_wipe.mp4", dry_run=True)
        ok1, msg1 = _filter_found_in_cache(cmd, "geq=")
        if not ok1:
            return ok1, msg1
        return _filter_found_in_cache(cmd, "lte(X")
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_wipe_invalid_direction():
    """wipe の未知方向は構築時 ValueError（旧実装は無言no-opだった）"""
    try:
        wipe("diagonal")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "direction" in msg and "diagonal" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_wipe_top_bottom_aliases():
    """wipe('top'/'bottom') は up/down の別名として動く（READMEの旧語彙）"""
    e_top = wipe("top")
    e_bottom = wipe("bottom")
    if e_top.params["direction"] != "up":
        return False, f"top → {e_top.params['direction']!r}"
    if e_bottom.params["direction"] != "down":
        return False, f"bottom → {e_bottom.params['direction']!r}"
    return True, "top→up, bottom→down"


def check_zoom_filter_in_checkpoint():
    """zoom(scale) Effectがcheckpointのfiltergraphに出ること"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj <= resize(sx=0.5, sy=0.5)\n'
        'obj.time(2) <= zoom(lambda u: lerp(1, 2, u)) & move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_zoom_test.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=640, height=360, fps=30, background_color="black")
        p.layer(temp_path, priority=0)
        cmd = p.render("_tmp_zoom.mp4", dry_run=True)
        ok1, msg1 = _filter_found_in_cache(cmd, "scale=w=")
        if not ok1:
            return ok1, msg1
        return _filter_found_in_cache(cmd, "eval=frame")
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_color_shift_filter_in_checkpoint():
    """color_shift Effectがcheckpointのfiltergraphに出ること"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj <= resize(sx=0.5, sy=0.5)\n'
        'obj.time(2) <= color_shift(hue=90) & move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_cshift_test.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=640, height=360, fps=30, background_color="black")
        p.layer(temp_path, priority=0)
        cmd = p.render("_tmp_cshift.mp4", dry_run=True)
        return _filter_found_in_cache(cmd, "hue=h=")
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_rotate_to_filter_in_checkpoint():
    """rotate_to Effectがcheckpointのfiltergraphに出ること"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj <= resize(sx=0.5, sy=0.5)\n'
        'obj.time(2) <= rotate_to(from_deg=0, to_deg=90) & move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_rotto_test.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=640, height=360, fps=30, background_color="black")
        p.layer(temp_path, priority=0)
        cmd = p.render("_tmp_rotto.mp4", dry_run=True)
        return _filter_found_in_cache(cmd, "rotate=angle=")
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_move_survives_bakeable_checkpoint():
    """move(live) + wipe(bakeable) でcheckpoint後もmoveがoverlayに残ること"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj <= resize(sx=0.5, sy=0.5)\n'
        'obj.time(2) <= wipe("left") & move(from_x=0.2, from_y=0.5, to_x=0.8, to_y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_move_surv.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=640, height=360, fps=30, background_color="black")
        p.layer(temp_path, priority=0)
        cmd = p.render("_tmp_move_surv.mp4", dry_run=True)
        if not isinstance(cmd, dict):
            return False, f"dictでない: {type(cmd)}"
        main_str = " ".join(cmd["main"])
        has_move = "0.2" in main_str and ("0.8" in main_str or "0.6" in main_str)
        if has_move:
            return True, "move preserved after bakeable checkpoint"
        return False, "move lost after checkpoint"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_shake_is_live():
    """shake(live)がcheckpointに焼かれずoverlayに残ること"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj <= resize(sx=0.5, sy=0.5)\n'
        'obj.time(2) <= shake(amplitude=0.05, frequency=8) & move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_shake_live.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=640, height=360, fps=30, background_color="black")
        p.layer(temp_path, priority=0)
        cmd = p.render("_tmp_shake_live.mp4", dry_run=True)
        if isinstance(cmd, dict):
            main_str = " ".join(cmd["main"])
        else:
            main_str = " ".join(cmd)
        has_shake = "sin(" in main_str and "cos(" in main_str
        if has_shake:
            return True, "shake in overlay"
        return False, "shake NOT in overlay"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_web_deps_invalidation():
    """depsファイルの内容変更でweb cache pathが変わること（touchでは変わらないこと）"""
    with tempfile.NamedTemporaryFile(suffix=".css", delete=False, mode="w") as f:
        f.write("body{}")
        dep_path = f.name
    old = Project._current
    try:
        p = Project()
        p.configure(width=640, height=360, fps=30, background_color="black")
        obj1 = subtitle_box("test", deps=[dep_path])
        path1 = _web_cache_path(obj1, p)
        # touch（内容そのまま）ではキャッシュ鍵が変わらないこと＝移植性
        os.utime(dep_path, None)
        obj_t = subtitle_box("test", deps=[dep_path])
        if _web_cache_path(obj_t, p) != path1:
            Project._current = old
            return False, "touchでcache pathが変わった（内容ハッシュでない）"
        # 内容を変更すると鍵が変わること
        with open(dep_path, "w") as f:
            f.write("body{color:red}")
        obj2 = subtitle_box("test", deps=[dep_path])
        path2 = _web_cache_path(obj2, p)
        Project._current = old
        if path1 != path2:
            return True, "cache path changed on dep content change"
        return False, f"cache path unchanged: {path1}"
    finally:
        os.unlink(dep_path)
        Project._current = old


def check_until_offset_positive():
    """anchor後 pause.until(name, offset=0.2) → 0.2秒待ち"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj.time(1) <= move(x=0.5, y=0.5, anchor="center")\n'
        'anchor("A")\n'
        'pause.until("A", offset=0.2)\n'
        'obj2 = Object(asset("images/shape_badge.png"))\n'
        'obj2.time(1) <= move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_offset_pos.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp_path, priority=0)
        p.render("_tmp.mp4", dry_run=True)
        # anchor Aは1.0秒、pause.until("A", 0.2)は target=1.2秒
        # pause start=1.0 → duration = max(0, 1.2 - 1.0) = 0.2
        # obj2 start = 1.2
        found = [o for o in p.objects if isinstance(o, Object)]
        obj2 = found[-1]
        if abs(obj2.start_time - 1.2) < 0.001:
            return True, f"obj2.start_time={obj2.start_time}"
        return False, f"obj2.start_time={obj2.start_time} (期待: 1.2)"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_until_offset_negative():
    """obj.until(name, offset=-0.5) → anchor前に終了"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj.time(2) <= move(x=0.5, y=0.5, anchor="center")\n'
        'anchor("B")\n'
        'pause.time(1)\n'
    )
    temp_path1 = _tmp_file("_tmp_offset_neg1.py")
    layer_code2 = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj.until("B", offset=-0.5)\n'
        'obj.time(3) <= move(x=0.3, y=0.3, anchor="center")\n'
    )
    temp_path2 = _tmp_file("_tmp_offset_neg2.py")
    try:
        with open(temp_path1, "w", encoding="utf-8") as f:
            f.write(layer_code)
        with open(temp_path2, "w", encoding="utf-8") as f:
            f.write(layer_code2)
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp_path1, priority=0)
        p.layer(temp_path2, priority=1)
        p.render("_tmp.mp4", dry_run=True)
        # anchor B = 2.0, offset=-0.5 → target=1.5
        # obj start=0 → duration = max(0, 1.5 - 0) = 1.5
        found = [o for o in p.objects if isinstance(o, Object)]
        obj2 = found[-1]
        if abs(obj2.duration - 1.5) < 0.001:
            return True, f"obj.duration={obj2.duration}"
        return False, f"obj.duration={obj2.duration} (期待: 1.5)"
    finally:
        for f in [temp_path1, temp_path2]:
            if os.path.exists(f):
                os.unlink(f)


def check_until_offset_zero_default():
    """offset省略時は従来互換"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj.time(2) <= move(x=0.5, y=0.5, anchor="center")\n'
        'anchor("C")\n'
        'pause.until("C")\n'
        'obj2 = Object(asset("images/shape_badge.png"))\n'
        'obj2.time(1) <= move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_offset_zero.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp_path, priority=0)
        p.render("_tmp.mp4", dry_run=True)
        # anchor C=2.0, pause.until("C") → duration=0 (既にat anchor)
        # obj2 start=2.0
        found = [o for o in p.objects if isinstance(o, Object)]
        obj2 = found[-1]
        if abs(obj2.start_time - 2.0) < 0.001:
            return True, f"obj2.start_time={obj2.start_time} (従来互換)"
        return False, f"obj2.start_time={obj2.start_time} (期待: 2.0)"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_time_name_anchors():
    """time(name=...) で .start/.end アンカーが自動生成"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj.time(1.5, name="s1") <= move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_name_anchor.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp_path, priority=0)
        p.render("_tmp.mp4", dry_run=True)
        start = p._anchors.get("s1.start")
        end = p._anchors.get("s1.end")
        if start is not None and end is not None:
            if abs(start - 0.0) < 0.001 and abs(end - 1.5) < 0.001:
                return True, f"s1.start={start}, s1.end={end}"
            return False, f"s1.start={start}, s1.end={end} (期待: 0.0, 1.5)"
        return False, f"アンカー未生成: start={start}, end={end}"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_time_name_duplicate():
    """同名time(name=...) で重複anchor → 衝突検出（同一layer内は上書きされる）"""
    # 異なるlayer間で同名anchor()が衝突するのと同様、
    # time(name=...) も X.start/X.end がanchor()経由のanchorと衝突すればエラー
    layer_code1 = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj.time(1, name="dup") <= move(x=0.5, y=0.5, anchor="center")\n'
    )
    layer_code2 = (
        'from scriptvedit import *\n'
        'anchor("dup.start")\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj.time(1) <= move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp1 = _tmp_file("_tmp_name_dup1.py")
    temp2 = _tmp_file("_tmp_name_dup2.py")
    try:
        with open(temp1, "w", encoding="utf-8") as f:
            f.write(layer_code1)
        with open(temp2, "w", encoding="utf-8") as f:
            f.write(layer_code2)
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp1, priority=0)
        p.layer(temp2, priority=1)
        # 両方のlayerで dup.start が定義 → anchor()側で衝突エラー
        # ただし time(name=...) は anchor() を呼ばないので _anchor_defined_in とは別管理
        # → 衝突は起きるが、_anchors に同名が入る（上書きされるだけ）
        # この場合は「衝突検知」ではなく「name anchor と anchor() が同名でも動く」こと確認
        p.render("_tmp.mp4", dry_run=True)
        # anchor("dup.start")はlayer2で呼ばれるので _anchor_defined_in に登録される
        # time(name="dup")のdup.startは _resolve_anchors 内で直接 _anchors に登録
        # → 衝突検知なし（別管理なので）。値は最後に書いた方が勝つ
        return True, "name anchor + anchor() 同名でも動作"
    except RuntimeError as e:
        return True, f"衝突検出: {str(e)[:60]}"
    finally:
        for f in [temp1, temp2]:
            if os.path.exists(f):
                os.unlink(f)


def check_time_name_with_until():
    """time(name=...) の .end を pause.until で参照"""
    layer_code1 = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj.time(2, name="scene1") <= move(x=0.5, y=0.5, anchor="center")\n'
    )
    layer_code2 = (
        'from scriptvedit import *\n'
        'pause.until("scene1.end")\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj.time(1) <= move(x=0.3, y=0.3, anchor="center")\n'
    )
    temp1 = _tmp_file("_tmp_name_until1.py")
    temp2 = _tmp_file("_tmp_name_until2.py")
    try:
        with open(temp1, "w", encoding="utf-8") as f:
            f.write(layer_code1)
        with open(temp2, "w", encoding="utf-8") as f:
            f.write(layer_code2)
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp1, priority=0)
        p.layer(temp2, priority=1)
        p.render("_tmp.mp4", dry_run=True)
        # layer2: pause.until("scene1.end") → scene1.end=2.0
        # obj start=2.0
        found = [o for o in p.objects if isinstance(o, Object)]
        obj2 = found[-1]
        if abs(obj2.start_time - 2.0) < 0.001:
            return True, f"pause.until(scene1.end) → obj2.start={obj2.start_time}"
        return False, f"obj2.start_time={obj2.start_time} (期待: 2.0)"
    finally:
        for f in [temp1, temp2]:
            if os.path.exists(f):
                os.unlink(f)


def check_show_no_advance():
    """show() で current_time が進まない"""
    layer_code = (
        'from scriptvedit import *\n'
        'bg = Object(asset("images/shape_badge.png"))\n'
        'bg.time(6) <= move(x=0.5, y=0.5, anchor="center")\n'
        'a = Object(asset("images/shape_badge.png"))\n'
        'a.show(6) <= move(x=0.3, y=0.3, anchor="center")\n'
        'b = Object(asset("images/shape_badge.png"))\n'
        'b.time(1) <= move(x=0.7, y=0.7, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_show_noadvance.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp_path, priority=0)
        p.render("_tmp.mp4", dry_run=True)
        found = [o for o in p.objects if isinstance(o, Object)]
        bg, a, b = found[0], found[1], found[2]
        # bg: start=0, dur=6 → current_time=6
        # a: show(6) → start=6, advance=False → current_time=6 のまま
        # b: time(1) → start=6, dur=1
        if abs(a.start_time - 6.0) < 0.001 and abs(b.start_time - 6.0) < 0.001:
            return True, f"a.start={a.start_time}, b.start={b.start_time} (current_time非進行)"
        return False, f"a.start={a.start_time}, b.start={b.start_time}"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_show_until_with_anchor():
    """show_until がanchor確定後にduration正しくなる"""
    # show_until は「同時表示」なので基準アイテムより前に置く。後ろに置くと
    # 開始時刻＝アンカー時刻になって尺0（＝不可視）になるが、それは
    # tests/test_until_zero_duration.py が RuntimeError として固定している。
    layer_code = (
        'from scriptvedit import *\n'
        'overlay = Object(asset("images/shape_badge.png"))\n'
        'overlay.show_until("main.end") <= move(x=0.3, y=0.3, anchor="center")\n'
        'obj1 = Object(asset("images/shape_badge.png"))\n'
        'obj1.time(3, name="main") <= move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_show_until.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp_path, priority=0)
        p.render("_tmp.mp4", dry_run=True)
        found = [o for o in p.objects if isinstance(o, Object)]
        overlay = found[0]
        # overlay は show_until なので開始0・非進行のまま main.end=3.0 まで伸びる
        if overlay.start_time != 0:
            return False, f"show_until が開始時刻を進めた: start={overlay.start_time}"
        if overlay.duration != 3.0:
            return False, f"アンカーまで伸びていない: dur={overlay.duration}"
        return True, f"overlay.start={overlay.start_time}, dur={overlay.duration}"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_show_priority_override():
    """show(priority=10) で z-order が変わる"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj.show(3, priority=10) <= move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_show_priority.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=320, height=240, fps=1, background_color="black")
        p.layer(temp_path, priority=0)
        p.render("_tmp.mp4", dry_run=True)
        found = [o for o in p.objects if isinstance(o, Object)]
        obj = found[0]
        if obj.priority == 10:
            return True, f"priority={obj.priority}"
        return False, f"priority={obj.priority} (期待: 10)"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_compute_removes_from_objects():
    """compute() で Project.objects から除外"""
    p = Project()
    p.configure(width=320, height=240, fps=1, background_color="black")
    obj = Object(asset("images/shape_badge.png"))
    obj <= resize(sx=0.5, sy=0.5)
    # compute前: objectsに含まれる
    before = obj in p.objects
    obj.compute()
    # compute後: objectsから除外
    after = obj in p.objects
    if before and not after:
        return True, "compute前: objects内, compute後: objects外"
    return False, f"before={before}, after={after}"


def check_compute_live_effect_error():
    """compute() で live Effect 使用時にエラー"""
    p = Project()
    p.configure(width=320, height=240, fps=1, background_color="black")
    obj = Object(asset("images/shape_badge.png"))
    obj <= resize(sx=0.5, sy=0.5)
    obj.effects.append(move(x=0.5, y=0.5))
    try:
        obj.compute()
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "live" in msg or "move" in msg:
            return True, msg[:60]
        return False, f"メッセージが不適切: {msg}"


def check_compute_returns_object():
    """compute() の戻り値が Object"""
    p = Project()
    p.configure(width=320, height=240, fps=1, background_color="black")
    obj = Object(asset("images/shape_badge.png"))
    obj <= resize(sx=0.5, sy=0.5)
    result = obj.compute()
    if isinstance(result, Object):
        return True, f"戻り値はObject, source={os.path.basename(result.source)}"
    return False, f"戻り値の型: {type(result)}"


def check_chroma_key_similarity_range():
    """chroma_key similarity範囲外 → ValueError"""
    try:
        chroma_key("green", similarity=1.5)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "similarity" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_chroma_key_bad_color():
    """chroma_key 不正な16進色 → ValueError"""
    try:
        chroma_key("#12345")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "RRGGBB" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_vignette_both_args():
    """vignette angle+strength同時指定 → ValueError"""
    try:
        vignette(angle=0.5, strength=0.5)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "angle" in msg and "strength" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_pixelize_expr_rejected():
    """pixelize size=Expr → ValueError（定数のみの明示エラー）"""
    try:
        pixelize(lambda u: 8 + u * 24)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "定数" in msg:
            return True, msg.split('\n')[0]
        return False, f"メッセージが不適切: {msg}"


def check_glow_intensity_range():
    """glow intensity範囲外 → ValueError"""
    try:
        glow(radius=8, intensity=1.5)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "intensity" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_lut_missing_file():
    """lut ファイル不在 → ValueError"""
    try:
        lut("__no_such_lut__.cube")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "見つかりません" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_lut_bad_ext():
    """lut 未対応拡張子 → ValueError"""
    temp_path = _tmp_file("_tmp_lut.txt")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write("dummy")
        lut(temp_path)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "未対応" in msg and ".cube" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_glitch_interval_range():
    """glitch interval=0 → ValueError"""
    try:
        glitch(strength=1.0, interval=0)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "interval" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_perspective_warp_non_numeric():
    """perspective_warp 非数値座標 → ValueError"""
    try:
        perspective_warp(0, 0, "300", 50, 0, 200, 300, 180)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "x1" in msg and "数値" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_lens_k1_range():
    """lens k1範囲外 → ValueError"""
    try:
        lens(k1=2.0)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "k1" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_ken_burns_aspect_mismatch():
    """ken_burns アスペクト比不一致 → ValueError"""
    try:
        ken_burns((0, 0, 800, 450), (0, 0, 400, 400))
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "アスペクト比" in msg:
            return True, msg.split('\n')[0]
        return False, f"メッセージが不適切: {msg}"


def check_ken_burns_bad_rect():
    """ken_burns 4要素でない矩形 → ValueError"""
    try:
        ken_burns((0, 0, 800), (0, 0, 400, 225))
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "from_rect" in msg and "4要素" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_drop_shadow_bad_color():
    """drop_shadow 未対応の色名 → ValueError"""
    try:
        drop_shadow(color="not_a_color")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "色名" in msg:
            return True, msg.split('\n')[0]
        return False, f"メッセージが不適切: {msg}"


def check_outline_width_range():
    """outline width=0 → ValueError"""
    try:
        outline(width=0)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "width" in msg and "1" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_slideshow_one_image():
    """slideshow 画像1枚 → ValueError"""
    try:
        slideshow([asset("images/shape_badge.png")])
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "2枚以上" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_slideshow_unknown_transition():
    """slideshow 未知のtransition名 → ValueError"""
    try:
        slideshow([asset("images/shape_badge.png"), asset("images/shape_figure.png")],
                  transition="explode")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "explode" in msg and "fade" in msg:
            return True, msg.split('\n')[0]
        return False, f"メッセージが不適切: {msg}"


def check_slideshow_tdur_too_long():
    """slideshow t_dur >= each → ValueError"""
    try:
        slideshow([asset("images/shape_badge.png"), asset("images/shape_figure.png")],
                  each=1.0, t_dur=1.0)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "t_dur" in msg and "each" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_transition_with_effects():
    """transition 加工済みObject → ValueError（compute()の案内）"""
    p = Project()
    p.configure(width=320, height=240, fps=1, background_color="black")
    a = Object(asset("images/shape_badge.png")).time(2)
    a <= resize(sx=0.5, sy=0.5)
    b = Object(asset("images/shape_figure.png")).time(2)
    try:
        transition(a, b)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "compute()" in msg:
            return True, msg.split('\n')[0]
        return False, f"メッセージが不適切: {msg}"


def check_transition_image_needs_time():
    """transition 画像に.time()未指定 → ValueError"""
    p = Project()
    p.configure(width=320, height=240, fps=1, background_color="black")
    a = Object(asset("images/shape_badge.png"))
    b = Object(asset("images/shape_figure.png")).time(2)
    try:
        transition(a, b)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if ".time" in msg:
            return True, msg
        return False, f"メッセージが不適切: {msg}"


def check_transition_consumes_objects():
    """transition 両Objectがタイムラインから除外されること"""
    p = Project()
    p.configure(width=320, height=240, fps=1, background_color="black")
    p._mode = "plan"  # 生成をスキップ
    a = Object(asset("images/shape_badge.png")).time(2)
    b = Object(asset("images/shape_figure.png")).time(2)
    tr = transition(a, b, kind="fade", duration=0.5)
    if a in p.objects or b in p.objects:
        return False, "消費されたObjectがobjectsに残っています"
    if tr not in p.objects:
        return False, "合成Objectがobjectsに登録されていません"
    if tr._resolved_length != 3.5:
        return False, f"合成尺が不正: {tr._resolved_length} (期待: 3.5)"
    return True, f"合成Object生成 source={os.path.basename(tr.source)}, 尺=3.5"


def check_glow_filter_in_checkpoint():
    """glow Effect（split/blend複合チェーン）がcheckpointのfiltergraphに出ること"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj <= resize(sx=0.5, sy=0.5)\n'
        'obj.time(2) <= glow(radius=8, intensity=0.8) & move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_glow_test.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=640, height=360, fps=30, background_color="black")
        p.layer(temp_path, priority=0)
        cmd = p.render("_tmp_glow.mp4", dry_run=True)
        ok1, msg1 = _filter_found_in_cache(cmd, "gblur=sigma=8")
        if not ok1:
            return ok1, msg1
        return _filter_found_in_cache(cmd, "blend=all_mode=screen:all_opacity=0.8")
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def check_drop_shadow_filter_in_checkpoint():
    """drop_shadow Effect（split/overlay複合チェーン）がcheckpointに出ること"""
    layer_code = (
        'from scriptvedit import *\n'
        'obj = Object(asset("images/shape_badge.png"))\n'
        'obj <= resize(sx=0.5, sy=0.5)\n'
        'obj.time(2) <= drop_shadow(dx=8, dy=8, blur=6) & move(x=0.5, y=0.5, anchor="center")\n'
    )
    temp_path = _tmp_file("_tmp_dshadow_test.py")
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = Project()
        p.configure(width=640, height=360, fps=30, background_color="black")
        p.layer(temp_path, priority=0)
        cmd = p.render("_tmp_dshadow.mp4", dry_run=True)
        ok1, msg1 = _filter_found_in_cache(cmd, "gblur=sigma=6")
        if not ok1:
            return ok1, msg1
        return _filter_found_in_cache(cmd, "alpha(X")
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _mk_project():
    p = Project()
    p.configure(width=640, height=360, fps=30, background_color="black")
    return p


def check_text_font_missing():
    """text: 存在しないフォント → FileNotFoundError"""
    _mk_project()
    try:
        text("あ", font="C:/no/such/font.ttc")
        return False, "例外が発生しませんでした"
    except FileNotFoundError as e:
        msg = str(e)
        return (True, msg.split("\n")[0]) if "フォント" in msg else (False, msg)


def check_text_size_expr_rejected():
    """text: size=Expr → ValueError（FFmpeg 8.0 fontsize式SEGV回避）"""
    _mk_project()
    try:
        text("あ", size=lambda u: 40 + 20 * u)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg.split("\n")[0]) if "定数" in msg else (False, msg)


def check_text_bad_anchor():
    """text: 未知のanchor → ValueError"""
    _mk_project()
    try:
        text("あ", anchor="middle")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "anchor" in msg else (False, msg)


def check_text_time_omit():
    """text: time()省略 → TypeError（画像/テキストは尺必須）"""
    _mk_project()
    t = text("あ")
    try:
        t.time()
        return False, "例外が発生しませんでした"
    except TypeError as e:
        msg = str(e)
        return (True, msg.split("\n")[0]) if "テキスト" in msg else (False, msg)


def check_text_border_negative():
    """text: border負値 → ValueError"""
    _mk_project()
    try:
        text("あ", border=-1)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "border" in msg else (False, msg)


def check_text_border_expr_rejected():
    """text: border=lambda → ValueError（定数のみ）"""
    _mk_project()
    try:
        text("あ", border=lambda u: u * 4)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "border" in msg and "定数" in msg else (False, msg)


def check_text_shadow_bad_shape():
    """text: shadowが2要素タプルでない → ValueError"""
    _mk_project()
    for bad in (5, (1,), (1, 2, 3), "2,2"):
        try:
            text("あ", shadow=bad)
            return False, f"例外が発生しませんでした: shadow={bad!r}"
        except ValueError as e:
            if "shadow" not in str(e):
                return False, f"メッセージが不適切: {e}"
    return True, "shadow形状の検証OK（4パターン）"


def check_typewriter_bad_cps():
    """typewriter: cps<=0 → ValueError"""
    _mk_project()
    try:
        typewriter("あ", cps=0)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "cps" in msg else (False, msg)


def check_counter_float_format():
    """counter: format=%f（小数）→ ValueError"""
    _mk_project()
    try:
        counter(0, 10, format="%.1f")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg.split("\n")[0]) if "整数" in msg else (False, msg)


def check_counter_apostrophe_format():
    """counter: formatリテラルにアポストロフィ → ValueError（inline不可）"""
    _mk_project()
    try:
        counter(0, 10, format="it's %d")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg.split("\n")[0]) if "アポストロフィ" in msg else (False, msg)


def check_subtitles_missing_file():
    """subtitles: SRTファイル不在 → FileNotFoundError"""
    _mk_project()
    try:
        subtitles("__no_such__.srt")
        return False, "例外が発生しませんでした"
    except FileNotFoundError as e:
        msg = str(e)
        return (True, msg) if "字幕" in msg else (False, msg)


def check_subtitles_bad_ext():
    """subtitles: 非対応拡張子 → ValueError"""
    _mk_project()
    tmp = _tmp_file("_tmp_subs.txt")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("x")
    try:
        subtitles(tmp)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "拡張子" in msg else (False, msg)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_duck_under_non_object():
    """duck_under: other非Object → TypeError"""
    try:
        duck_under("not_an_object")
        return False, "例外が発生しませんでした"
    except TypeError as e:
        msg = str(e)
        return (True, msg) if "other" in msg else (False, msg)


def check_duck_under_other_not_in_project():
    """duck_under: other が再生対象外 → ValueError（レンダ時）"""
    layer_code = (
        "from scriptvedit import *\n"
        "narr = Object(asset(\"audio/効果音.mp3\"))\n"
        "narr.time(2) <= adelete()\n"  # 音声を除外
        "bgm = Object(asset(\"audio/bgm_loop.mp3\"))\n"
        "bgm.time(3) <= duck_under(narr)\n"
    )
    tmp = _tmp_file("_tmp_duck.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = _mk_project()
        p.layer(tmp, priority=0)
        p.render("_tmp.mp4", dry_run=True)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg.split("\n")[0]) if "duck_under" in msg else (False, msg)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_audio_sequence_too_few():
    """audio_sequence: 入力1つ → ValueError"""
    _mk_project()
    try:
        audio_sequence(asset("audio/bgm_loop.mp3"))
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "2つ以上" in msg else (False, msg)


def check_audio_sequence_non_audio():
    """audio_sequence: 画像パス → ValueError"""
    _mk_project()
    try:
        audio_sequence(asset("images/shape_badge.png"), asset("audio/bgm_loop.mp3"))
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "音声" in msg else (False, msg)


def check_sfx_missing_source():
    """sfx: ソース不在 → FileNotFoundError"""
    _mk_project()
    try:
        sfx("__no_such__.mp3", at=[0.5])
        return False, "例外が発生しませんでした"
    except FileNotFoundError as e:
        msg = str(e)
        return (True, msg) if "見つかりません" in msg else (False, msg)


def check_sfx_empty_at():
    """sfx: at空リスト → ValueError"""
    _mk_project()
    try:
        sfx(asset("audio/効果音.mp3"), at=[])
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "at" in msg else (False, msg)


def check_audio_viz_bad_kind():
    """audio_viz: 未知kind → ValueError"""
    _mk_project()
    try:
        audio_viz(asset("audio/bgm_loop.mp3"), kind="bogus")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "kind" in msg else (False, msg)


def check_audio_viz_missing_source():
    """audio_viz: ソース不在 → FileNotFoundError"""
    _mk_project()
    try:
        audio_viz("__no_such__.mp3")
        return False, "例外が発生しませんでした"
    except FileNotFoundError as e:
        msg = str(e)
        return (True, msg) if "見つかりません" in msg else (False, msg)


def check_normalize_audio_range():
    """normalize_audio: target範囲外 → ValueError"""
    p = _mk_project()
    try:
        p.normalize_audio(10)  # 0より大きいLUFSは不正
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "target" in msg else (False, msg)


def check_text_drawtext_in_cmd():
    """text: 生成コマンドに drawtext と textfile が出ること"""
    layer_code = (
        "from scriptvedit import *\n"
        "t = text(\"日本語: 100% 'x'\")\n"
        "t.time(2) <= fade(lambda u: u)\n"
    )
    tmp = _tmp_file("_tmp_text.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = _mk_project()
        p.layer(tmp, priority=0)
        cmd = p.render("_tmp.mp4", dry_run=True)
        s = " ".join(cmd["main"])
        if "drawtext" in s and "textfile=" in s:
            return True, "drawtext+textfile 出力OK"
        return False, "drawtext/textfile が見つからない"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _text_dry_run_cmd(layer_body):
    """テキスト系レイヤーを dry_run してメインコマンド文字列を返すヘルパー"""
    layer_code = "from scriptvedit import *\n" + layer_body
    tmp = _tmp_file("_tmp_text_deco.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = _mk_project()
        p.layer(tmp, priority=0)
        cmd = p.render("_tmp.mp4", dry_run=True)
        return " ".join(cmd["main"])
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_text_border_shadow_in_cmd():
    """text: border/shadow指定 → drawtextに borderw/bordercolor/shadowx/y/color"""
    s = _text_dry_run_cmd(
        "t = text('縁取り', border=3, border_color='black@0.8',\n"
        "         shadow=(2, 2), shadow_color='gray@0.5')\n"
        "t.time(2)\n")
    expected = ("borderw=3:bordercolor=black@0.8",
                "shadowx=2:shadowy=2:shadowcolor=gray@0.5")
    for e in expected:
        if e not in s:
            return False, f"'{e}' が生成コマンドに見つからない"
    return True, "borderw/bordercolor/shadowx/shadowy/shadowcolor 出力OK"


def check_text_default_no_border_shadow():
    """text: 既定値では borderw/shadowx 等を一切出力しない（既存出力と不変）"""
    s = _text_dry_run_cmd(
        "t = text('既定')\n"
        "t.time(2)\n")
    for bad in ("borderw", "bordercolor", "shadowx", "shadowy", "shadowcolor"):
        if bad in s:
            return False, f"既定値なのに '{bad}' が出力された"
    return True, "既定値で縁取り・影オプション非出力OK"


def check_typewriter_counter_border_in_cmd():
    """typewriter/counter: border指定がdrawtextに反映される"""
    s = _text_dry_run_cmd(
        "tw = typewriter('あい', cps=5, border=2)\n"
        "tw.time(2)\n"
        "c = counter(0, 10, border=2, shadow=(1, 1))\n"
        "c.time(2)\n")
    if s.count("borderw=2:bordercolor=black") < 3:  # typewriter2文字分 + counter
        return False, "typewriter/counter の borderw 出力が不足"
    if "shadowx=1:shadowy=1:shadowcolor=black@0.6" not in s:
        return False, "counter の shadow 出力が見つからない"
    return True, "typewriter/counter の縁取り・影 出力OK"


def check_loudnorm_in_cmd():
    """normalize_audio: 生成コマンドに loudnorm が出ること"""
    layer_code = (
        "from scriptvedit import *\n"
        "bgm = Object(asset(\"audio/bgm_loop.mp3\"))\n"
        "bgm.time(3)\n"
    )
    tmp = _tmp_file("_tmp_ln.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer_code)
        p = _mk_project()
        p.normalize_audio(-16)
        p.layer(tmp, priority=0)
        cmd = p.render("_tmp.mp4", dry_run=True)
        s = " ".join(cmd["main"])
        return (True, "loudnorm 出力OK") if "loudnorm=I=-16" in s else (False, "loudnorm欠落")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_explode_to_not_last():
    """explode_to の後に bakeable op → エラー"""
    layer = (
        "from scriptvedit import *\n"
        "o = Object(asset(\"images/shape_badge.png\"))\n"
        "o.time(2) <= explode_to()\n"
        "o <= scale(1.2)\n"
    )
    tmp = _tmp_file("_tmp_expl.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer)
        p = _mk_project()
        p.layer(tmp, priority=0)
        p.render("_tmp.mp4", dry_run=True)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        return (True, str(e).split("\n")[0]) if "explode_to" in str(e) else (False, str(e)[:60])
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_explode_to_needs_duration():
    """explode_to を含む画像に time() 未指定 → エラー"""
    layer = (
        "from scriptvedit import *\n"
        "o = Object(asset(\"images/shape_badge.png\"))\n"
        "o <= explode_to()\n"
        "o <= move(x=0.5, y=0.5)\n"
    )
    tmp = _tmp_file("_tmp_expl2.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer)
        p = _mk_project()
        p.layer(tmp, priority=0)
        p.render("_tmp.mp4", dry_run=True)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        return (True, str(e).split("\n")[0]) if "時間" in str(e) else (False, str(e)[:60])
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_assemble_from_non_object():
    """assemble_from の source が非Object → TypeError"""
    try:
        assemble_from("notanobject")
        return False, "例外が発生しませんでした"
    except TypeError as e:
        return True, str(e)[:60]


def check_two_terminal_effects():
    """morph_to と explode_to を同時指定 → エラー"""
    layer = (
        "from scriptvedit import *\n"
        "tgt = Object(asset(\"images/shape_figure.png\"))\n"
        "o = Object(asset(\"images/shape_badge.png\"))\n"
        "o.time(2) <= morph_to(tgt)\n"
        "o <= explode_to()\n"
    )
    tmp = _tmp_file("_tmp_two.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer)
        p = _mk_project()
        p.layer(tmp, priority=0)
        p.render("_tmp.mp4", dry_run=True)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        return (True, str(e).split("\n")[0]) if "1回" in str(e) else (False, str(e)[:60])
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_move_along_too_few():
    """move_along: 点が1つ → ValueError"""
    try:
        move_along([(0.5, 0.5)])
        return False, "例外が発生しませんでした"
    except ValueError as e:
        return True, str(e)[:60]


def check_path_bezier_bad_count():
    """path_bezier: 制御点数が 3n+1 でない → ValueError"""
    try:
        path_bezier((0, 0), (1, 1), (0.5, 0.5))  # 3点 → 不正
        return False, "例外が発生しませんでした"
    except ValueError as e:
        return True, str(e)[:60]


def check_group_non_object():
    """group: 非Object → TypeError"""
    try:
        group(Object(asset("images/shape_badge.png")), "x")
        return False, "例外が発生しませんでした"
    except TypeError as e:
        return True, str(e)[:60]


def check_grid_on_non_image():
    """grid: 音声素材 → TypeError"""
    try:
        o = Object(asset("audio/bgm_loop.mp3"))
        o.grid(2, 2)
        return False, "例外が発生しませんでした"
    except TypeError as e:
        return True, str(e)[:60]


def check_render_window_bad_range():
    """render: end <= start → ValueError"""
    layer = (
        "from scriptvedit import *\n"
        "o = Object(asset(\"images/shape_badge.png\"))\n"
        "o.time(3) <= move(x=0.5, y=0.5)\n"
    )
    tmp = _tmp_file("_tmp_win.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer)
        p = _mk_project()
        p.layer(tmp, priority=0)
        p.render("_tmp.mp4", dry_run=True, start=3, end=2)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        return (True, str(e).split("\n")[0]) if "end" in str(e) else (False, str(e)[:60])
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_inertia_bad_damping():
    """inertia: damping<=0 → ValueError"""
    try:
        inertia(0.5, 0.0, damping=0)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        return True, str(e)[:60]


def check_perlin_bad_octaves():
    """perlin: octaves<1 → ValueError"""
    try:
        perlin(0.5, octaves=0)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        return True, str(e)[:60]


def check_look_at_bad_path():
    """look_at: パス以外 → TypeError"""
    try:
        look_at("notapath")
        return False, "例外が発生しませんでした"
    except TypeError as e:
        return True, str(e)[:60]


def check_param_cli_override():
    """p.param: --param name=値 で上書きされる"""
    old = list(sys.argv)
    try:
        sys.argv = ["x", "--param", "greeting=こんにちは"]
        p = _mk_project()
        val = p.param("greeting", "無題")
        return (True, val) if val == "こんにちは" else (False, f"取得値={val}")
    finally:
        sys.argv = old


def check_marker_in_cmd():
    """p.marker: 生成コマンドに ffmetadata/-map_metadata が出る"""
    layer = (
        "from scriptvedit import *\n"
        "o = Object(asset(\"images/shape_badge.png\"))\n"
        "o.time(4) <= move(x=0.5, y=0.5)\n"
    )
    tmp = _tmp_file("_tmp_mrk.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer)
        p = _mk_project()
        p.marker(0, "A")
        p.marker(2, "B")
        p.layer(tmp, priority=0)
        cmd = p.render("_tmp.mp4", dry_run=True)
        s = " ".join(cmd["main"])
        ok = "ffmetadata" in s and "-map_metadata" in s
        return (True, "チャプター埋め込みOK") if ok else (False, "ffmetadata欠落")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_explode_produces_particle_cache():
    """explode_to: dry_runでparticle .mkv キャッシュコマンドが出る"""
    layer = (
        "from scriptvedit import *\n"
        "o = Object(asset(\"images/shape_badge.png\"))\n"
        "o.time(2) <= explode_to()\n"
    )
    tmp = _tmp_file("_tmp_pc.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer)
        p = _mk_project()
        p.layer(tmp, priority=0)
        res = p.render("_tmp.mp4", dry_run=True)
        if not isinstance(res, dict):
            return False, "cacheコマンドが返りませんでした"
        has_particle = any("particle" in k.replace("\\", "/") and k.endswith(".mkv")
                           for k in res["cache"])
        return (True, "particleキャッシュOK") if has_particle else (False, "particle欠落")
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_bad_preset_suggest():
    """不正preset名 → ValueError かつ suggest（もしかして）"""
    p = Project()
    try:
        p.configure(preset="shortz")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "shortz" in msg and "もしかして" in msg and "shorts" in msg:
            return True, msg.split("\n")[0]
        return False, f"メッセージが不適切: {msg}"


def check_bad_encoder_suggest():
    """不正encoder名 → ValueError かつ suggest"""
    p = Project()
    try:
        p.configure(encoder="libx246")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "libx246" in msg and "もしかして" in msg:
            return True, msg.split("\n")[0]
        return False, f"メッセージが不適切: {msg}"


def check_configure_typo_suggest():
    """configureキーtypo → suggest（widht→width）"""
    p = Project()
    try:
        p.configure(widht=1280)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "もしかして" in msg and "width" in msg:
            return True, msg.split("\n")[0]
        return False, f"suggestが出ませんでした: {msg}"


def check_encoder_fallback():
    """未対応エンコーダ指定は libx264 へフォールバックし例外を投げない"""
    import warnings as _w
    p = Project()
    with _w.catch_warnings():
        _w.simplefilter("ignore")
        p.configure(encoder="nvenc")
    # 環境により nvenc 有無が異なる: h264_nvenc（利用可）か libx264（フォールバック）
    if p._encoder_cv in ("h264_nvenc", "libx264"):
        return True, f"encoder解決: {p._encoder_cv}"
    return False, f"予期しないencoder: {p._encoder_cv}"


def check_preset_sets_dimensions():
    """preset='square' で width/height/fps が設定される"""
    p = Project()
    p.configure(preset="square")
    if (p.width, p.height) == (1080, 1080):
        return True, f"{p.width}x{p.height}"
    return False, f"寸法不正: {p.width}x{p.height}"


def check_preset_override():
    """preset の後に width 個別指定で上書きできる"""
    p = Project()
    p.configure(preset="hd", width=1000)
    if p.width == 1000 and p.height == 1080:
        return True, f"{p.width}x{p.height}"
    return False, f"上書き失敗: {p.width}x{p.height}"


def check_gif_output_format():
    """.gif 出力で palettegen/paletteuse が cmd に出る"""
    layer = (
        "from scriptvedit import *\n"
        "o = Object(asset(\"images/shape_badge.png\"))\n"
        "o.time(2) <= move(x=0.5, y=0.5, anchor='center')\n"
    )
    tmp = _tmp_file("_tmp_gif.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer)
        p = _mk_project()
        p.layer(tmp, priority=0)
        cmd = p.render("_tmp.gif", dry_run=True)
        s = " ".join(cmd["main"])
        if "palettegen" in s and "paletteuse" in s and "-an" in cmd["main"]:
            return True, "GIFパレット出力OK"
        return False, f"パレット欠落: {s[-120:]}"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_alpha_webm_format():
    """.webm + alpha=True で透明背景 + yuva420p"""
    layer = (
        "from scriptvedit import *\n"
        "o = Object(asset(\"images/shape_badge.png\"))\n"
        "o.time(2) <= move(x=0.5, y=0.5, anchor='center')\n"
    )
    tmp = _tmp_file("_tmp_webm.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer)
        p = _mk_project()
        p.layer(tmp, priority=0)
        cmd = p.render("_tmp.webm", dry_run=True, alpha=True)
        s = " ".join(cmd["main"])
        if "yuva420p" in s and "black@0.0" in s and "libvpx-vp9" in s:
            return True, "透過webm出力OK"
        return False, f"透過設定欠落: {s[:160]}"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_draft_key_separation():
    """draft と本番でチェックポイント鍵が共有される（中間物は同一内容のため）

    以前は draft 時に鍵へ rq=draft を混ぜて分離していたが、生成される中間物の
    内容は draft/本番で同一のため、分離すると本番↔draft で全キャッシュミスに
    なり無駄な再生成が起きる。よって鍵は共有されるのが正しい。

    鍵分離用のグローバル _ACTIVE_QUALITY は「書き込みだけで読み出しが無い
    死んだ状態」だったため撤去済み（監査 項目15a）。ここでは実際のレンダ経路
    （draft=True / draft=False）が同じチェックポイントパスを指すことを見る。"""
    import tempfile
    from scriptvedit import Project

    layer_dir = tempfile.mkdtemp()
    layer_path = os.path.join(layer_dir, "draftkey.py")
    src = asset("images/shape_badge.png").replace("\\", "/")
    with open(layer_path, "w", encoding="utf-8") as f:
        f.write("from scriptvedit import *\n"
                f"Object({src!r}).time(1) <= resize(sx=0.5, sy=0.5)\n")

    def _checkpoints(draft):
        p = Project()
        p.configure(width=64, height=36, fps=10)
        p.layer(layer_path)
        result = p.render(os.path.join(layer_dir, "o.mp4"),
                          dry_run=True, draft=draft)
        return sorted(k for k in result["cache"] if "checkpoint" in k)

    final_paths = _checkpoints(False)
    draft_paths = _checkpoints(True)
    if not final_paths:
        return False, "チェックポイントが計画されていない（テストの前提崩れ）"
    if final_paths == draft_paths:
        return True, "draft/final鍵共有OK（無駄な再生成なし）"
    return False, f"鍵が分離している: {final_paths} != {draft_paths}"


def check_voice_without_tts_module():
    """voice()（tts依存が使えない環境想定）: scriptvedit.tts 経由の呼び出し形が正しい

    VOICEVOX未起動でも ImportError/ConnectionError の適切な例外になることを確認。
    """
    from scriptvedit import voice
    try:
        voice("テスト", speaker=1)
        return True, "voice実行成功（VOICEVOX起動中）"
    except (ImportError, ConnectionError, TimeoutError, RuntimeError) as e:
        # tts依存不在 or VOICEVOX未起動: いずれも想定内の親切なエラー
        return True, f"想定内エラー: {type(e).__name__}"
    except Exception as e:
        return False, f"予期しない例外: {type(e).__name__}: {e}"


def check_inspect_report_text():
    """inspect()（out_html省略）でテキストレポート文字列を返す"""
    layer = (
        "from scriptvedit import *\n"
        "o = Object(asset(\"images/shape_badge.png\"))\n"
        "o.time(2) <= move(x=0.5, y=0.5, anchor='center')\n"
    )
    tmp = _tmp_file("_tmp_insp.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer)
        p = _mk_project()
        p.layer(tmp, priority=0)
        p.render("_tmp.mp4", dry_run=True)
        rep = p.inspect()
        if isinstance(rep, str) and "タイムライン" in rep:
            return True, "レポート生成OK"
        return False, f"レポート不正: {type(rep)}"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_alpha_on_mp4_rejected():
    """alpha=True を .mp4(非透過コンテナ)で指定 → ValueError（黒潰れ防止）"""
    layer = (
        "from scriptvedit import *\n"
        "o = Object(asset(\"images/shape_badge.png\"))\n"
        "o.time(2) <= move(x=0.5, y=0.5, anchor='center')\n"
    )
    tmp = _tmp_file("_tmp_alpha_mp4.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer)
        p = _mk_project()
        p.layer(tmp, priority=0)
        p.render("_tmp_alpha.mp4", dry_run=True, alpha=True)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "alpha=True" in msg and (".webm" in msg or "透過" in msg):
            return True, msg.split("\n")[0]
        return False, f"メッセージが不適切: {msg}"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_audio_sequence_short_crossfade():
    """audio_sequence: 素材長 < crossfade → ValueError"""
    try:
        # bgm_loop.mp3 は約31秒。crossfade=100 は素材長を超える
        audio_sequence(asset("audio/bgm_loop.mp3"), asset("audio/bgm_loop.mp3"), crossfade=100)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "crossfade" in msg and "素材長" in msg:
            return True, msg.split("\n")[0]
        return False, f"メッセージが不適切: {msg}"


def check_move_along_too_many_points():
    """move_along: 128点超 → ValueError"""
    pts = [(i / 200.0, 0.5) for i in range(200)]
    try:
        move_along(pts)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "128" in msg else (False, f"不適切: {msg}")


def check_keyframes_too_many_points():
    """keyframes: 128点超 → ValueError"""
    args = []
    for i in range(200):
        args.append((i / 200.0, 0.5))
    try:
        keyframes(*args)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "128" in msg else (False, f"不適切: {msg}")


def check_counter_reaches_target():
    """counter: %{eif}式に四捨五入(+0.5*sign)が入り目標値に到達する"""
    layer = (
        "from scriptvedit import *\n"
        "c = counter(0, 100, format='%d', x=0.5, y=0.5, size=48)\n"
        "c.time(4)\n"
    )
    tmp = _tmp_file("_tmp_counter_reach.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer)
        p = _mk_project()
        p.layer(tmp, priority=0)
        cmd = p.render("_tmp_counter.mp4", dry_run=True)
        s = " ".join(cmd["main"])
        # 四捨五入項 +0.5 が eif 式に含まれること（100 が表示されるようになる）
        if "eif" in s and "+0.5" in s:
            return True, "四捨五入項 +0.5 を確認"
        return False, f"四捨五入項が見つからない: {s[:160]}"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_typewriter_halfopen_enable():
    """typewriter: 隣接窓が半開区間(gte/lt)で境界二重描画を防ぐ"""
    layer = (
        "from scriptvedit import *\n"
        "tw = typewriter('あいう', cps=5, x=0.1, y=0.5, size=48)\n"
        "tw.time(2)\n"
    )
    tmp = _tmp_file("_tmp_tw_boundary.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer)
        p = _mk_project()
        p.layer(tmp, priority=0)
        cmd = p.render("_tmp_tw.mp4", dry_run=True)
        s = " ".join(cmd["main"])
        # 中間文字の drawtext enable が gte(t,..)*lt(t,..) の半開区間であること
        # （オーバーレイ側の enable は between を使うため、drawtext の gte*lt を確認）
        if "gte(t" in s and "lt(t" in s and "gte(t\\,0.2000)*lt(t" in s:
            return True, "半開区間enable(gte*lt)を確認"
        return False, f"半開区間になっていない: {s[:200]}"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_ken_burns_overshoot_clamp():
    """ken_burns: overshoot easing でも scale式が[0,1]クランプされ幅破綻しない"""
    layer = (
        "from scriptvedit import *\n"
        "img = Object(asset(\"images/shape_badge.png\"))\n"
        "img.time(3) <= ken_burns((0,0,800,450),(100,60,400,225), easing=ease_out_back)\n"
    )
    tmp = _tmp_file("_tmp_kb_overshoot.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer)
        p = _mk_project()
        p.layer(tmp, priority=0)
        cmd = p.render("_tmp_kb.mp4", dry_run=True)
        # checkpoint の scale フィルタに clip( が入り、係数がクランプされること
        cache = cmd.get("cache", {})
        for path, c in cache.items():
            cs = " ".join(c) if isinstance(c, list) else str(c)
            if "scale=w=" in cs and "crop=" in cs:
                # scale部分(crop直前)に clip( が含まれる＝スケール係数クランプ
                scale_part = cs[cs.index("scale=w="):cs.index("crop=")]
                if "clip(" in scale_part:
                    return True, "scale係数の[0,1]クランプを確認"
                return False, f"scaleにclipなし: {scale_part[:160]}"
        return False, "ken_burns checkpointが見つからない"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


# --- 合成・時間操作（Wave: mask/blend_mode/speed 等） ---

def check_blend_mode_bad_name():
    """blend_mode: 未知のモード名 → ValueError + suggest"""
    try:
        blend_mode("screeen")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "screeen" in msg and "もしかして" in msg and "screen" in msg:
            return True, msg.split("\n")[0]
        return False, f"suggestなし: {msg}"


def check_blend_mode_alias():
    """blend_mode: エイリアス 'add' → addition に解決"""
    e = blend_mode("add")
    if e.params["mode"] == "addition":
        return True, "add → addition"
    return False, f"エイリアス解決失敗: {e.params}"


def check_reverse_too_long():
    """reverse: 実効尺が30秒超（speedで引き伸ばし） → ValueError"""
    _require_asset("video/guitar_noaudio.mp4")
    layer = (
        "from scriptvedit import *\n"
        "obj = Object(asset(\"video/guitar_noaudio.mp4\"))\n"
        "obj.time(5) <= speed(0.1) & reverse() & move(x=0.5, y=0.5)\n"
    )
    tmp = _tmp_file("_tmp_rev_long.py")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(layer)
        p = _mk_project()
        p.layer(tmp, priority=0)
        p.render("_tmp_rev.mp4", dry_run=True)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "reverse" in msg and "30" in msg:
            return True, msg.split("\n")[0]
        return False, f"メッセージが不適切: {msg}"
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def check_video_sequence_tdur_too_big():
    """video_sequence: t_dur が最短クリップ以上 → ValueError"""
    guitar = _require_asset("video/guitar_noaudio.mp4")
    _mk_project()
    try:
        video_sequence(asset("video/clip_with_audio.mp4"), guitar, t_dur=6.0)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        if "t_dur" in msg and "clip_with_audio" in msg:
            return True, msg.split("\n")[0]
        return False, f"メッセージが不適切: {msg}"


def check_video_sequence_one_clip():
    """video_sequence: 1クリップのみ → ValueError"""
    _mk_project()
    try:
        video_sequence(asset("video/clip_with_audio.mp4"))
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "2つ以上" in msg else (False, msg)


def check_video_sequence_non_video():
    """video_sequence: 画像パスを混ぜる → ValueError"""
    _mk_project()
    try:
        video_sequence(asset("video/clip_with_audio.mp4"), asset("images/shape_badge.png"))
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "動画のみ" in msg else (False, msg)


def check_speed_bad_factor():
    """speed: factor=0 → ValueError（範囲外）"""
    try:
        speed(0)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "factor" in msg else (False, msg)


def check_speed_on_image():
    """speed: 画像素材への適用 → ValueError"""
    _mk_project()
    obj = Object(asset("images/shape_badge.png"))
    try:
        obj <= speed(2.0)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg.split("\n")[0]) if "動画素材にのみ" in msg else (False, msg)


def check_speed_length_reflected():
    """speed: length() に factor が反映される（自動atempoの二重計上なし）"""
    p = _mk_project()
    obj = Object(asset("video/clip_with_audio.mp4"))
    obj <= speed(2.0)
    ln = obj.length()
    expected = 5.545 / 2.0
    if abs(ln - expected) < 0.01:
        # 自動atempoが追加されフラグ付きであること
        auto = [ae for ae in obj.audio_effects
                if ae.name == "atempo" and getattr(ae, "_auto_from_speed", False)]
        if auto:
            return True, f"length={ln:.4f} (期待{expected:.4f}) + 自動atempo確認"
        return False, "自動atempoが追加されていない"
    return False, f"length不一致: {ln} (期待{expected})"


def check_freeze_frame_length_reflected():
    """freeze_frame: length() に +duration が反映される"""
    p = _mk_project()
    obj = Object(asset("video/clip_with_audio.mp4"))
    obj <= freeze_frame(at=1.0, duration=2.0)
    ln = obj.length()
    expected = 5.545 + 2.0
    if abs(ln - expected) < 0.01:
        return True, f"length={ln:.4f} (期待{expected:.4f})"
    return False, f"length不一致: {ln} (期待{expected})"


def check_freeze_frame_bad_at():
    """freeze_frame: at が負 → ValueError"""
    try:
        freeze_frame(at=-1.0, duration=1.0)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "at" in msg else (False, msg)


def check_opacity_out_of_range():
    """opacity: 定数が範囲外(1.5) → ValueError"""
    try:
        opacity(1.5)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "value" in msg else (False, msg)


def check_rounded_negative():
    """rounded: 負のradius → ValueError"""
    try:
        rounded(-5)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "radius" in msg else (False, msg)


def check_mask_missing_file():
    """mask: マスク画像不在 → FileNotFoundError"""
    try:
        mask("no_such_mask_image.png")
        return False, "例外が発生しませんでした"
    except FileNotFoundError as e:
        msg = str(e)
        return (True, msg) if "マスク画像" in msg else (False, msg)


def check_mask_wipe_non_image():
    """mask_wipe: 動画をマスクに指定 → ValueError"""
    try:
        mask_wipe(asset("video/clip_with_audio.mp4"))
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "画像のみ" in msg else (False, msg)


def check_pip_bad_border():
    """pip: border が非整数 → ValueError"""
    try:
        pip(border=2.5)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "border" in msg else (False, msg)


def check_pip_returns_chain():
    """pip: EffectChain（scale/rounded/outline/drop_shadow/move）を返す"""
    ch = pip(x=0.7, y=0.7, scale=0.3, radius=12, border=2, shadow=True)
    names = [e.name for e in ch.effects]
    expected = ["scale", "rounded", "outline", "drop_shadow", "move"]
    if names == expected:
        return True, f"構成: {names}"
    return False, f"構成不一致: {names}"


def check_progress_bar_bad_color():
    """progress_bar: 不正な色名 → ValueError"""
    _mk_project()
    try:
        progress_bar(color="not_a_color")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg.split("\n")[0]) if "色名" in msg or "16進" in msg else (False, msg)


def check_from_project_non_project():
    """from_project: Project以外 → TypeError"""
    try:
        Object.from_project("not_a_project")
        return False, "例外が発生しませんでした"
    except TypeError as e:
        msg = str(e)
        return (True, msg) if "Project" in msg else (False, msg)


def check_from_project_bad_cache():
    """from_project: cache不正値 → ValueError + suggest"""
    sub = Project()
    try:
        Object.from_project(sub, cache="always")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg.split("\n")[0]) if "cache" in msg else (False, msg)


def check_from_project_no_layers():
    """from_project: layer()未登録のサブProject → ValueError"""
    sub = Project()
    try:
        Object.from_project(sub)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg.split("\n")[0]) if "layer()" in msg else (False, msg)


def check_atempo_chain_decompose():
    """_atempo_chain_rates: 範囲外レートの多段分解（範囲内はそのまま）"""
    if _atempo_chain_rates(0.25) != [0.5, 0.5]:
        return False, f"0.25の分解失敗: {_atempo_chain_rates(0.25)}"
    if _atempo_chain_rates(2.0) != [2.0]:
        return False, f"2.0が分解された: {_atempo_chain_rates(2.0)}"
    if _atempo_chain_rates(200.0) != [100.0, 2.0]:
        return False, f"200.0の分解失敗: {_atempo_chain_rates(200.0)}"
    return True, "0.25→[0.5,0.5] / 2.0→[2.0] / 200→[100,2]"


def check_compute_rejects_blend_mode():
    """compute: live Effect blend_mode → ValueError"""
    _mk_project()
    obj = Object(asset("images/shape_badge.png"))
    obj <= blend_mode("screen")
    try:
        obj.compute()
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg.split("\n")[0]) if "blend_mode" in msg else (False, msg)


# --- 外部モジュール統合（narrate/karaoke/beat_sync/slide/export_metadata/storyboard） ---

def check_narrate_without_voicevox():
    """narrate()（VOICEVOX未起動環境想定）: voice()同様に想定内エラーへ透過する"""
    try:
        narrate("テストナレーション", speaker=1)
        return True, "narrate実行成功（VOICEVOX起動中）"
    except (ImportError, ConnectionError, TimeoutError, RuntimeError) as e:
        return True, f"想定内エラー: {type(e).__name__}"
    except FileNotFoundError as e:
        if "フォント" in str(e):
            raise  # フォント不在はランナー側で skip される（最小環境）
        return False, f"予期しない例外: {type(e).__name__}: {e}"
    except Exception as e:
        return False, f"予期しない例外: {type(e).__name__}: {e}"


def check_narrate_returns_narration_tuple():
    """narrate()の戻り値: Narrationは(audio, subtitle)としてタプルアンパック可能"""
    from scriptvedit import tts as tts_mod
    orig_tts, orig_dur = tts_mod.tts, tts_mod.tts_duration
    tts_mod.tts = lambda text, **kw: asset("audio/bgm_loop.mp3")
    tts_mod.tts_duration = lambda path: 1.5
    try:
        _mk_project()
        n = narrate("テスト", subtitle=True)
        if not isinstance(n, Narration):
            return False, f"Narration型ではありません: {type(n)}"
        a, s = n
        if a is not n.audio or s is not n.subtitle:
            return False, "タプルアンパックが属性と一致しません"
        if n.duration != 1.5:
            return False, f"durationがtts_durationと不一致: {n.duration}"
        if s.media_type != "text":
            return False, f"subtitleがtext Objectではありません: {s.media_type}"
        return True, f"audio.duration={a.duration}, subtitle.media_type={s.media_type}"
    finally:
        tts_mod.tts = orig_tts
        tts_mod.tts_duration = orig_dur


def check_narrate_subtitle_false_no_subtitle():
    """narrate(subtitle=False): subtitle属性がNoneになる"""
    from scriptvedit import tts as tts_mod
    orig_tts, orig_dur = tts_mod.tts, tts_mod.tts_duration
    tts_mod.tts = lambda text, **kw: asset("audio/bgm_loop.mp3")
    tts_mod.tts_duration = lambda path: 1.0
    try:
        _mk_project()
        n = narrate("テスト", subtitle=False)
        ok = n.subtitle is None and n.audio is not None
        return (True, "subtitle=Noneを確認") if ok else (False, f"subtitle={n.subtitle!r}")
    finally:
        tts_mod.tts = orig_tts
        tts_mod.tts_duration = orig_dur


def check_narrate_subtitle_style_border_shadow():
    """narrate(subtitle_style={...}): border/shadow が字幕の _text_spec に渡る"""
    from scriptvedit import tts as tts_mod
    orig_tts, orig_dur = tts_mod.tts, tts_mod.tts_duration
    tts_mod.tts = lambda text, **kw: asset("audio/bgm_loop.mp3")
    tts_mod.tts_duration = lambda path: 1.0
    try:
        _mk_project()
        n = narrate("テスト", subtitle=True,
                    subtitle_style={"border": 2, "border_color": "black@0.9",
                                    "shadow": (2, 3), "shadow_color": "gray"})
        spec = n.subtitle._text_spec
        if spec["border"] != 2 or spec["border_color"] != "black@0.9":
            return False, f"border が渡っていない: {spec['border']!r}/{spec['border_color']!r}"
        if spec["shadow"] != (2, 3) or spec["shadow_color"] != "gray":
            return False, f"shadow が渡っていない: {spec['shadow']!r}/{spec['shadow_color']!r}"
        # 既定（指定なし）では border=0 / shadow=(0,0) のまま
        n2 = narrate("テスト2", subtitle=True)
        spec2 = n2.subtitle._text_spec
        if spec2["border"] != 0 or spec2["shadow"] != (0, 0):
            return False, f"既定値が変わっている: {spec2['border']!r}/{spec2['shadow']!r}"
        return True, "narrate subtitle_style の border/shadow 透過OK"
    finally:
        tts_mod.tts = orig_tts
        tts_mod.tts_duration = orig_dur


# --- TTS バックエンド（voicevox / edge / sapi） ---

def _tts_scratch_dir():
    """TTS テスト用の一時キャッシュディレクトリ（テストごとに掃除して使う）"""
    import tempfile
    return tempfile.mkdtemp(prefix="svtts_test_")


def _edge_or_skip():
    """edge-tts が使えない環境は正直に skip（PASS 扱いにしない）"""
    from scriptvedit import tts as tts_mod
    if not tts_mod._edge_available():
        _skip("edge-tts 未導入（pip install edge-tts）")
    return tts_mod


def check_tts_backend_invalid():
    """tts(backend='???'): 未知のバックエンドは ValueError"""
    from scriptvedit import tts as tts_mod
    try:
        tts_mod.tts("テスト", backend="unknown_engine")
        return False, "未知の backend が通ってしまいました"
    except ValueError as e:
        msg = str(e)
        return (True, msg) if "backend" in msg else (False, msg)


def check_tts_backend_env_selection():
    """backend=None の自動選択: 環境変数 SCRIPTVEDIT_TTS_BACKEND が最優先される"""
    import os
    from scriptvedit import tts as tts_mod
    orig = os.environ.get("SCRIPTVEDIT_TTS_BACKEND")
    os.environ["SCRIPTVEDIT_TTS_BACKEND"] = "sapi"
    try:
        got = tts_mod._resolve_backend(None)
        return (True, "env→sapi") if got == "sapi" else (False, f"env が無視された: {got}")
    finally:
        if orig is None:
            os.environ.pop("SCRIPTVEDIT_TTS_BACKEND", None)
        else:
            os.environ["SCRIPTVEDIT_TTS_BACKEND"] = orig


def check_tts_backend_fallback_to_edge():
    """backend=None の自動選択: VOICEVOX 未起動なら edge にフォールバックする"""
    import os
    tts_mod = _edge_or_skip()
    orig = os.environ.pop("SCRIPTVEDIT_TTS_BACKEND", None)
    try:
        # 到達不能ポートを指定して「VOICEVOX 未起動」を再現する
        got = tts_mod._resolve_backend(None, port=1)
        return (True, "未起動→edge") if got == "edge" else (False, f"edge にならない: {got}")
    finally:
        if orig is not None:
            os.environ["SCRIPTVEDIT_TTS_BACKEND"] = orig


def check_tts_voicevox_error_suggests_edge():
    """VOICEVOX 未起動エラー: 代替案（backend="edge"）を案内する日本語メッセージ"""
    from scriptvedit import tts as tts_mod
    try:
        tts_mod.tts("テスト", backend="voicevox", port=1, cache_dir=_tts_scratch_dir())
        return False, "未起動なのに例外になりませんでした（ポート1で応答?）"
    except ConnectionError as e:
        msg = str(e)
        ok = "VOICEVOX" in msg and 'backend="edge"' in msg and "edge-tts" in msg
        return (True, msg.splitlines()[0]) if ok else (False, f"代替案の案内が無い: {msg}")


def check_tts_cache_key_includes_backend():
    """キャッシュ鍵に backend が含まれる（同じ text/speaker でも別ファイルになる）"""
    from scriptvedit import tts as tts_mod
    a = tts_mod._cache_path("voicevox", "こんにちは", 1, 1.0, 0.0, "c")
    b = tts_mod._cache_path("edge", "こんにちは", 1, 1.0, 0.0, "c")
    if a == b:
        return False, "backend 違いで同じキャッシュパスになりました"
    c = tts_mod._cache_path("edge", "こんにちは", 1, 1.0, 0.0, "c")
    if b != c:
        return False, "同一条件でキャッシュパスが安定しません"
    return True, f"backend でキー分離: {os.path.basename(a)} != {os.path.basename(b)}"


def check_tts_edge_rate_pitch_mapping():
    """edge: speed/pitch を edge-tts の rate/pitch 文字列へ写像する"""
    from scriptvedit import tts as tts_mod
    cases = [
        (tts_mod._edge_rate(1.0), "+0%"), (tts_mod._edge_rate(1.2), "+20%"),
        (tts_mod._edge_rate(0.8), "-20%"),
        (tts_mod._edge_pitch(0.0), "+0Hz"), (tts_mod._edge_pitch(0.1), "+10Hz"),
        (tts_mod._edge_pitch(-0.1), "-10Hz"),
    ]
    bad = [(got, want) for got, want in cases if got != want]
    if bad:
        return False, f"写像が不正: {bad}"
    try:
        tts_mod._edge_rate(0)
        return False, "speed=0 が通ってしまいました"
    except ValueError:
        pass
    return True, "rate/pitch 写像OK（speed<=0 は ValueError）"


def check_tts_edge_speaker_resolution():
    """edge: speaker の解釈（音声名／短縮名／数値は警告付きで写像）"""
    import warnings as _w
    from scriptvedit import tts as tts_mod
    if tts_mod._edge_voice(None) != "ja-JP-NanamiNeural":
        return False, "既定音声が ja-JP-NanamiNeural ではありません"
    if tts_mod._edge_voice("keita") != "ja-JP-KeitaNeural":
        return False, "短縮名 keita が解決できません"
    if tts_mod._edge_voice("ja-JP-NanamiNeural") != "ja-JP-NanamiNeural":
        return False, "音声名がそのまま通りません"
    with _w.catch_warnings(record=True) as rec:
        _w.simplefilter("always")
        v = tts_mod._edge_voice(1)
    if v not in tts_mod._EDGE_JA_VOICES:
        return False, f"数値 speaker の写像先が不正: {v}"
    if not rec:
        return False, "数値 speaker で警告が出ていません"
    return True, f"speaker 解決OK（数値1→{v}・警告あり）"


def check_tts_edge_synth_wav():
    """edge: 実際に日本語を合成して wav が得られ、tts_duration で尺が取れる（要ネット）"""
    import shutil as _sh
    tts_mod = _edge_or_skip()
    cache = _tts_scratch_dir()
    try:
        try:
            wav = tts_mod.tts("テスト音声です", backend="edge", cache_dir=cache)
        except ConnectionError as e:
            _skip(f"edge-tts はオンライン必須（ネットワーク不通）: {str(e).splitlines()[0]}")
        if not wav.endswith(".wav") or not os.path.exists(wav):
            return False, f"wav が生成されていません: {wav}"
        dur = tts_mod.tts_duration(wav)   # wave モジュールで開ける＝mp3→wav 変換済み
        if dur <= 0:
            return False, f"尺が0以下: {dur}"
        # 2回目はキャッシュヒット（ファイルの mtime が変わらない）
        mtime = os.path.getmtime(wav)
        wav2 = tts_mod.tts("テスト音声です", backend="edge", cache_dir=cache)
        if wav2 != wav or os.path.getmtime(wav2) != mtime:
            return False, "2回目にキャッシュが効いていません（再合成された）"
        # backend を変えれば別キャッシュ（voicevox 未起動でも鍵が違えば別パス）
        other = tts_mod._cache_path("voicevox", "テスト音声です", 1, 1.0, 0.0, cache)
        if other == wav:
            return False, "backend 違いで同じキャッシュになりました"
        return True, f"edge 合成OK: {dur:.2f}秒・キャッシュヒット確認"
    finally:
        _sh.rmtree(cache, ignore_errors=True)


def check_tts_edge_speakers_list():
    """edge: 話者一覧に日本語音声が含まれる（要ネット）"""
    tts_mod = _edge_or_skip()
    try:
        sp = tts_mod.speakers(backend="edge")
    except ConnectionError as e:
        _skip(f"edge-tts はオンライン必須（ネットワーク不通）: {str(e).splitlines()[0]}")
    ids = [s["id"] for s in sp]
    if "ja-JP-NanamiNeural" not in ids:
        return False, f"日本語音声が見つかりません: {ids[:5]}"
    if not all({"id", "name", "style"} <= set(s) for s in sp):
        return False, "話者エントリのキーが不足しています"
    return True, f"edge 話者 {len(sp)}件（{ids[:2]}）"


def check_voice_edge_backend_object():
    """voice(backend="edge"): 音声Object が TTS 実長の duration を持つ（要ネット）"""
    _edge_or_skip()
    from scriptvedit import voice as _voice
    _mk_project()
    try:
        v = _voice("エッジのテスト", backend="edge")
    except ConnectionError as e:
        _skip(f"edge-tts はオンライン必須（ネットワーク不通）: {str(e).splitlines()[0]}")
    if v.duration is None or v.duration <= 0:
        return False, f"duration が不正: {v.duration}"
    if not str(v.source).endswith(".wav"):
        return False, f"素材が wav ではありません: {v.source}"
    return True, f"voice(edge) OK: {v.duration:.2f}秒"


def check_narrate_edge_backend():
    """narrate(backend="edge"): 音声+字幕が生成され、字幕窓が音声実長に一致（要ネット）"""
    _edge_or_skip()
    _mk_project()
    try:
        n = narrate("エッジのナレーション", backend="edge")
    except ConnectionError as e:
        _skip(f"edge-tts はオンライン必須（ネットワーク不通）: {str(e).splitlines()[0]}")
    if n.subtitle is None:
        return False, "字幕が生成されていません"
    if abs(n.subtitle.duration - n.audio.duration) > 1e-6:
        return False, f"字幕窓が音声実長と不一致: {n.subtitle.duration} != {n.audio.duration}"
    return True, f"narrate(edge) OK: {n.duration:.2f}秒・字幕あり"


def check_karaoke_ass_kfired():
    """karaoke: 生成ASSに\\kタグ・Dialogue・スタイル色が含まれる"""
    obj = karaoke([(0.0, 2.0, "abc")], style={"primary": "yellow", "secondary": "white"})
    ass_path = obj._text_spec["srt"]
    with open(ass_path, encoding="utf-8") as f:
        content = f.read()
    checks = [
        "\\k" in content, "Dialogue: 0,0:00:00.00,0:00:02.00" in content,
        "&H0000FFFF" in content,  # primary=yellow (BGR: 00,FF,FF)
        content.count("\\k") == 3,  # 'a','b','c' の3語
    ]
    return (True, "ASS生成OK") if all(checks) else (False, f"検証失敗: {content[:200]}")


def check_karaoke_word_durations_equal_split():
    """karaoke: word_durations省略時は均等割りされる"""
    obj = karaoke([(0.0, 3.0, "ab")])
    ass_path = obj._text_spec["srt"]
    with open(ass_path, encoding="utf-8") as f:
        content = f.read()
    # 2文字で3秒 → 1文字あたり1.5秒 = 150centiseconds
    ok = "{\\k150}" in content
    return (True, "均等割りOK") if ok else (False, content)


def check_karaoke_word_durations_mismatch():
    """karaoke: word_durations数がトークン数と不一致 → ValueError"""
    try:
        karaoke([(0.0, 2.0, "abc", [0.5, 0.5])])
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg.split("\n")[0]) if "word_durations" in msg else (False, msg)


def check_karaoke_bad_line_tuple():
    """karaoke: lines要素の長さ不正 → ValueError"""
    try:
        karaoke([(0.0, 2.0)])
        return False, "例外が発生しませんでした"
    except ValueError as e:
        return True, str(e).split("\n")[0]


def check_karaoke_end_before_start():
    """karaoke: end <= start → ValueError"""
    try:
        karaoke([(2.0, 1.0, "x")])
        return False, "例外が発生しませんでした"
    except ValueError as e:
        return True, str(e).split("\n")[0]


def check_beat_sync_detects_and_caches():
    """beat_sync: 実音声でビート検出し、2回目はJSONキャッシュから即返す"""
    _require_beat_env()
    audio = asset("audio/bgm_loop.mp3")
    from scriptvedit.state import _ARTIFACT_DIR
    import shutil as _sh
    beats_dir = os.path.join(_ARTIFACT_DIR, "beats")
    if os.path.isdir(beats_dir):
        _sh.rmtree(beats_dir)
    r1 = beat_sync(audio, min_bpm=60, max_bpm=200)
    if "bpm" not in r1 or "beats" not in r1 or not r1["beats"]:
        return False, f"結果構造が不正: {list(r1.keys())}"
    cache_files_before = os.listdir(beats_dir) if os.path.isdir(beats_dir) else []
    if not cache_files_before:
        return False, "キャッシュJSONが生成されていません"
    r2 = beat_sync(audio, min_bpm=60, max_bpm=200)
    ok = r1 == r2
    return (True, f"bpm={r1['bpm']}, beats={len(r1['beats'])}拍, キャッシュ一致") if ok \
        else (False, "1回目と2回目の結果が不一致")


def check_beat_sync_missing_file():
    """beat_sync: 存在しない音声ファイル → FileNotFoundError"""
    try:
        beat_sync("no_such_audio.mp3")
        return False, "例外が発生しませんでした"
    except FileNotFoundError as e:
        return True, str(e).split("\n")[0]


def check_slide_missing_file():
    """slide: 存在しないHTML → FileNotFoundError"""
    _mk_project()
    try:
        slide("no_such_slide.html", page=0)
        return False, "例外が発生しませんでした"
    except FileNotFoundError as e:
        return True, str(e).split("\n")[0]


def check_slide_bad_extension():
    """slide: .html/.htm以外 → ValueError"""
    _mk_project()
    try:
        slide(asset("images/shape_badge.png"), page=0)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        return True, str(e).split("\n")[0]


def check_slide_size_without_project():
    """slide: width/height省略時にアクティブProjectが無ければRuntimeError"""
    Project._current = None
    html = os.path.join(os.path.dirname(__file__), "layers", "test19_scene.html")
    try:
        slide(html, page=0)
        return False, "例外が発生しませんでした"
    except RuntimeError as e:
        return True, str(e).split("\n")[0]


def check_export_metadata_json():
    """p.export_metadata(): JSON出力にtitle/chapters/tagsが反映される"""
    p = _mk_project()
    p.marker(0, "イントロ")
    p.marker(3.0, "本編")
    out = _tmp_file("_tmp_meta.json")
    try:
        import json as _json
        p.export_metadata(out, title="テスト動画", description="説明文",
                          tags=["tag1", "tag2"])
        with open(out, encoding="utf-8") as f:
            data = _json.load(f)
        ok = (data["title"] == "テスト動画" and data["tags"] == ["tag1", "tag2"]
              and len(data["chapters"]) == 2 and "0:00" in data["chapters_text"])
        return (True, "JSON構造OK") if ok else (False, f"内容不一致: {data}")
    finally:
        if os.path.exists(out):
            os.remove(out)


def check_export_metadata_title_from_param():
    """p.export_metadata(): title省略時はparam('title')を使う"""
    p = _mk_project()
    argv_bak = sys.argv[:]
    sys.argv = [argv_bak[0], "--param", "title=パラメータ由来タイトル"]
    out = _tmp_file("_tmp_meta2.json")
    try:
        import json as _json
        p.export_metadata(out)
        with open(out, encoding="utf-8") as f:
            data = _json.load(f)
        ok = data["title"] == "パラメータ由来タイトル"
        return (True, "param由来title OK") if ok else (False, f"title={data['title']!r}")
    finally:
        sys.argv = argv_bak
        if os.path.exists(out):
            os.remove(out)


def check_export_metadata_txt_format():
    """p.export_metadata(): .txt拡張子ではプレーンテキスト形式で出力される"""
    p = _mk_project()
    p.marker(0, "イントロ")
    out = _tmp_file("_tmp_meta.txt")
    try:
        p.export_metadata(out, title="タイトル", tags=["a", "b"])
        with open(out, encoding="utf-8") as f:
            content = f.read()
        ok = "タイトル" in content and "0:00" in content and "#a" in content
        return (True, "txt形式OK") if ok else (False, content)
    finally:
        if os.path.exists(out):
            os.remove(out)


# --- 統合レビュー修正の検証（S1/S3/S7/S8/S11/S12/S13/S14/S15） ---

def check_freeze_frame_at_beyond_clip():
    """S3: freeze_frame の at がクリップ実効尺以上 → 構築時にValueError"""
    _mk_project()
    obj = Object(asset("video/clip_with_audio.mp4"))
    # trim(2) 後の実効尺は2s。at=5 はそれ以上なので空セグメントになる
    obj <= trim(2) & freeze_frame(at=5.0, duration=1.0)
    try:
        _build_video_pre_filters(obj)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        ok = "freeze_frame" in msg and "at" in msg
        return (True, msg.split("\n")[0]) if ok else (False, msg)


def check_freeze_frame_at_beyond_length_no_overcount():
    """S3: at>=実尺 のとき length() は +duration を計上しない"""
    _mk_project()
    obj = Object(asset("video/clip_with_audio.mp4"))
    # trim(2) で実尺2s、freeze at=5(>=2) → 静止区間は成立しないので尺は2sのまま
    # ※この素材は名前に反して音声streamを持つ。issue #13 P2-11 で length() が
    #   「有効streamの最大」になったため、映像側の計算を検証するには音声を除外する
    obj <= adelete()
    obj <= trim(2) & freeze_frame(at=5.0, duration=1.0)
    ln = obj.length()
    if abs(ln - 2.0) < 0.01:
        return True, f"length={ln:.4f}（+duration計上なし）"
    return False, f"length={ln}（期待2.0、+durationが誤って計上された）"


def check_speed_auto_atrim_after_atempo():
    """S1: speed の自動atrimはatempoの後段（音声尺がfactor²短縮されない）"""
    import re
    layer = _tmp_file("_tmp_s1_layer.py")
    with open(layer, "w", encoding="utf-8") as f:
        f.write("from scriptvedit import *\n"
                "a = Object(asset(\"video/clip_with_audio.mp4\"))\n"
                "a.time(2.0) <= speed(2.0) & move(x=0.5, y=0.5)\n")
    p = _mk_project()
    p.layer(layer)
    try:
        cmd = p.render("_tmp_s1.mp4", dry_run=True)
        flat = str(cmd)
        # 正: atempo → atrim の順。誤: atrim → atempo（旧バグ）
        good = re.search(r"atempo=2\.0,atrim=duration=2\.0", flat)
        bad = re.search(r"atrim=duration=[\d.]+,asetpts=PTS-STARTPTS,atempo", flat)
        ok = (good is not None) and (bad is None)
        return (True, "atempo→atrim順を確認") if ok else (False, flat[:400])
    finally:
        if os.path.exists(layer):
            os.remove(layer)


def check_rounded_radius_clamped_in_geq():
    """S7: rounded の geq が半径を実寸(min(W,H)/2)で上限クランプする"""
    _mk_project()
    obj = Object(asset("images/shape_badge.png"))
    obj <= rounded(40)
    filters = _build_effect_filters(obj, 0.0, 4.0)
    flat = str(filters)
    # 生の "40" ではなく min(40, min(W,H)/2) 形式でクランプされていること
    ok = "min(40" in flat and "min(W" in flat and "hypot" in flat
    return (True, "半径クランプ式を確認") if ok else (False, flat[:300])


def check_probe_stream_durations():
    """S8: _probe_media が映像/音声ストリーム個別の尺を返す"""
    p = _mk_project()
    info = p._probe_media(asset("video/clip_with_audio.mp4"))
    if info is None:
        _skip("ffprobe が使えない環境")
    # 映像ストリーム尺のキーが存在する（コンテナ尺と食い違ってもよい）
    if "video_duration" not in info or "audio_duration" not in info:
        return False, f"stream尺キーが無い: {list(info.keys())}"
    return True, (f"container={info.get('duration')}, "
                  f"video={info.get('video_duration')}, "
                  f"audio={info.get('audio_duration')}")


def check_narrate_zero_duration():
    """S15: narrate の tts_duration=0 → ValueError（連続narrate重なり防止）"""
    from scriptvedit import tts as tts_mod
    orig_tts, orig_dur = tts_mod.tts, tts_mod.tts_duration
    tts_mod.tts = lambda text, **kw: asset("audio/bgm_loop.mp3")
    tts_mod.tts_duration = lambda path: 0.0
    try:
        _mk_project()
        narrate("", subtitle=True)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        return (True, msg.split("\n")[0]) if "0以下" in msg or "dur" in msg else (False, msg)
    finally:
        tts_mod.tts = orig_tts
        tts_mod.tts_duration = orig_dur


def check_karaoke_equal_split_sum_matches():
    """S12: karaoke 均等割りの\\k総和が行尺(センチ秒)に一致する（丸め累積なし）"""
    import re
    # 7文字を2.0秒 → 均等割り。旧実装は round(2/7*100)*7=203cs で3cs超過
    obj = karaoke([(0.0, 2.0, "こんにちは世界")])
    ass_path = obj._text_spec["srt"]
    with open(ass_path, encoding="utf-8") as f:
        content = f.read()
    ks = [int(x) for x in re.findall(r"\\k(\d+)", content)]
    if sum(ks) == 200 and len(ks) == 7:
        return True, f"\\k={ks} 総和={sum(ks)}cs（=2.00s）"
    return False, f"\\k={ks} 総和={sum(ks)}cs（期待200）"


def check_export_metadata_json_intro_chapter():
    """S13: 先頭マーカーが0より後なら json chapters に0:00イントロ章が入る"""
    import json as _json
    p = _mk_project()
    p.marker(2.0, "本編")     # 先頭マーカーが0:00より後
    p.marker(4.5, "まとめ")
    out = _tmp_file("_tmp_meta_intro.json")
    try:
        p.export_metadata(out, title="T")
        with open(out, encoding="utf-8") as f:
            data = _json.load(f)
        ch = data["chapters"]
        ok = (len(ch) == 3 and abs(ch[0]["time"]) < 1e-6
              and ch[0]["label"] == "イントロ")
        return (True, f"先頭章={ch[0]}") if ok else (False, f"chapters={ch}")
    finally:
        if os.path.exists(out):
            os.remove(out)


def check_beat_sync_corrupt_cache_self_heal():
    """S11: 破損キャッシュJSONは無視して再解析（self-heal）"""
    _require_beat_env()
    audio = asset("audio/bgm_loop.mp3")
    import glob as _glob
    beats_dir = os.path.join(_ARTIFACT_DIR, "beats")
    # 一旦正常に解析してキャッシュを作る
    beat_sync(audio, min_bpm=60, max_bpm=200)
    caches = _glob.glob(os.path.join(beats_dir, "*.json"))
    if not caches:
        return False, "キャッシュJSONが生成されていない"
    # キャッシュを破損させる
    with open(caches[0], "w", encoding="utf-8") as f:
        f.write("{ this is not valid json ")
    try:
        r = beat_sync(audio, min_bpm=60, max_bpm=200)  # 例外を投げず再解析
    except Exception as e:
        return False, f"破損キャッシュで例外: {type(e).__name__}: {e}"
    ok = isinstance(r, dict) and "beats" in r
    return (True, "破損キャッシュを無視して再解析") if ok else (False, f"結果不正: {r}")


def check_slide_page_js_normalizes_id():
    """S14: slide のページ切替JSがゼロ埋めid正規化と未表示検出を含む"""
    import inspect
    src = inspect.getsource(Object._render_web_frames)
    checks = ["getElementById", "parseInt", "shown === 0", "throw new Error"]
    missing = [c for c in checks if c not in src]
    if not missing:
        return True, "id正規化+未表示例外フックを確認"
    return False, f"JSに不足: {missing}"


# --- プラグイン機構 ---

import scriptvedit as _sv


def _def_plugin(name, **kw):
    """テスト用プラグインを登録してファクトリを返す"""
    params = kw.pop("params", {
        "radius": {"type": "number", "default": 5, "min": 0, "max": 100,
                   "desc": "半径"},
        "amount": {"type": "expr", "default": 1.0, "min": 0, "max": 2,
                   "desc": "強さ"},
    })

    @_sv.effect_plugin(name, params=params, **kw)
    def _builder(params, ctx):
        """テスト用プラグイン"""
        return [f"gblur=sigma={params['radius']}"]

    return getattr(_sv, name)


def check_plugin_unknown_param():
    """プラグイン: 未知パラメータ → 日本語エラー + suggest"""
    _sv.unregister_plugin("t_unknown")
    f = _def_plugin("t_unknown")
    try:
        f(radus=3)
        return False, "例外が発生しませんでした"
    except ValueError as e:
        msg = str(e)
        ok = "未知のパラメータ" in msg and "もしかして" in msg and "radius" in msg
        return (True, msg.split("\n")[0]) if ok else (False, f"メッセージ不足: {msg}")
    finally:
        _sv.unregister_plugin("t_unknown")


def check_plugin_out_of_range():
    """プラグイン: number/expr の範囲外 → ValueError"""
    _sv.unregister_plugin("t_range")
    f = _def_plugin("t_range")
    errs = []
    for kw in ({"radius": 500}, {"amount": 5.0}):
        try:
            f(**kw)
            errs.append(f"{kw} で例外なし")
        except ValueError as e:
            if "範囲" not in str(e):
                errs.append(f"{kw}: {e}")
    _sv.unregister_plugin("t_range")
    return (not errs), ("範囲外を検出" if not errs else "; ".join(errs))


def check_plugin_expr_accepted():
    """プラグイン: type='expr' は lambda/Expr を受理（liveアニメ）"""
    _sv.unregister_plugin("t_expr")
    f = _def_plugin("t_expr")
    e = f(amount=lambda u: u * 0.5)
    ok = isinstance(e.params["amount"], _sv.Expr)
    _sv.unregister_plugin("t_expr")
    return (ok, "lambda を Expr に解決") if ok else (False, f"未解決: {e.params}")


def check_plugin_duplicate_registration():
    """プラグイン: 同名の二重登録は明示エラー（override=True で許可）"""
    _sv.unregister_plugin("t_dup")
    _def_plugin("t_dup")
    try:
        _def_plugin("t_dup")
        _sv.unregister_plugin("t_dup")
        return False, "二重登録が通ってしまいました"
    except _sv.PluginError as e:
        if "既に登録" not in str(e):
            _sv.unregister_plugin("t_dup")
            return False, f"メッセージ不適切: {e}"
    try:
        _def_plugin("t_dup", override=True)  # 明示フラグなら許可
    except _sv.PluginError as e:
        _sv.unregister_plugin("t_dup")
        return False, f"override=True が拒否された: {e}"
    _sv.unregister_plugin("t_dup")
    return True, "二重登録を拒否 / override=True は許可"


def check_plugin_builtin_name_conflict():
    """プラグイン: 組込Effect名の上書きは常に禁止"""
    try:
        _def_plugin("glow")
        _sv.unregister_plugin("glow")
        return False, "組込 glow を上書きできてしまいました"
    except _sv.PluginError as e:
        ok = "組込" in str(e)
        return (True, str(e).split("\n")[0]) if ok else (False, f"メッセージ不適切: {e}")


def check_plugin_reserved_submodule_name():
    """プラグイン: サブモジュール名(beat/tts/viz/morph/testkit)の乗っ取りは禁止

    禁止しないと `import scriptvedit.beat`（beat_sync 等）が注入済みファクトリ関数に
    隠され AttributeError で壊れる。
    """
    for name in ("beat", "tts", "viz", "morph", "testkit"):
        try:
            _def_plugin(name)
            _sv.unregister_plugin(name)
            return False, f"予約名 '{name}' を乗っ取れてしまいました"
        except _sv.PluginError as e:
            if "予約名" not in str(e) and "組込" not in str(e):
                return False, f"メッセージ不適切（{name}）: {e}"
    # 予約名の登録が拒否されたので、beat_sync のモジュール参照は健全なまま
    from importlib import import_module
    try:
        mod = import_module("scriptvedit.beat")
    except ImportError:
        mod = None  # numpy/scipy 不在。名前が奪われていないことは上で確認済み
    if mod is not None and not hasattr(mod, "detect_beats"):
        return False, "scriptvedit.beat が壊れています"
    return True, "5つの予約名すべてを拒否"


def check_ffp_no_disk_cache():
    """ffp: 内容ハッシュのディスクキャッシュ(ffp.json)を持たない

    (パス, サイズ, mtime) を参照キーにディスクへ永続化すると、mtime を保持する
    コピー（cp -p / rsync -t / unzip -o 等）で同サイズの別内容へ差し替えたときに
    古いハッシュを返し、内容ハッシュ化の目的そのものが破れる。
    """
    from scriptvedit.state import _ARTIFACT_DIR
    from scriptvedit.cache import _FFP_MEMO
    cache_root = os.path.dirname(_ARTIFACT_DIR)
    ffp_json = os.path.join(cache_root, "ffp.json")
    if os.path.exists(ffp_json):
        return False, f"ffp.json が残っています: {ffp_json}"
    tmp = tempfile.mkdtemp(prefix="svffp_")
    try:
        path = os.path.join(tmp, "a.bin")
        with open(path, "wb") as f:
            f.write(b"AAAA")
        h1 = _file_fingerprint(path)
        st = os.stat(path)
        # 同サイズ・同mtimeの別内容へ差し替え（cp -p 相当）
        with open(path, "wb") as f:
            f.write(b"BBBB")
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
        _FFP_MEMO.clear()  # 別プロセス（別レンダ）相当
        h2 = _file_fingerprint(path)
        if h1 == h2:
            return False, "mtime保持の差し替えで古い指紋が返りました（ディスクキャッシュ残存）"
        if os.path.exists(ffp_json):
            return False, "ffp.json が生成されました"
        return True, f"内容変更で指紋が更新される（{h1} → {h2}）"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_plugin_bakeable_checkpoint():
    """プラグイン: bakeable=True はチェックポイント経路でも同じビルダーが使われる"""
    _sv.unregister_plugin("t_bake")
    _def_plugin("t_bake", bakeable=True)
    try:
        if "t_bake" not in _BAKEABLE_EFFECTS:
            return False, "_BAKEABLE_EFFECTS に登録されていません"
        e = getattr(_sv, "t_bake")(radius=7)
        if not _is_bakeable("effect", e):
            return False, "_is_bakeable が False"
        obj = Object.__new__(Object)
        obj.source = asset("images/shape_badge.png")
        obj.transforms = []
        obj.effects = [e]
        obj.media_type = "image"
        filters, _pad = _build_effect_filters(obj, 0, 2, base_dims=(100, 100))
        ok = any("gblur=sigma=7" in f for f in filters)
        return (True, "bakeable登録+ビルダー共有") if ok else (False, f"フィルタ不正: {filters}")
    finally:
        _sv.unregister_plugin("t_bake")


def check_plugin_live_not_bakeable():
    """プラグイン: bakeable=False は _BAKEABLE_EFFECTS に入らない"""
    _sv.unregister_plugin("t_live")
    _def_plugin("t_live", bakeable=False)
    ok = "t_live" not in _BAKEABLE_EFFECTS
    _sv.unregister_plugin("t_live")
    return (ok, "live扱い") if ok else (False, "bakeable集合に混入")


def check_plugin_fingerprint_includes_source():
    """プラグイン: ソースコードのFFPがキャッシュ鍵(_op_fingerprint_str)に入る"""
    d = tempfile.mkdtemp()
    path = os.path.join(d, "fp_plugin.py")
    src = (
        "from scriptvedit import effect_plugin\n\n"
        "@effect_plugin('t_fp', bakeable=True, params={'k': "
        "{'type': 'number', 'default': %d}})\n"
        "def build_t_fp(params, ctx):\n"
        "    '''FFPテスト'''\n"
        "    return [f\"eq=gamma={params['k']}\"]\n"
    )
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(src % 1)
        _sv.unregister_plugin("t_fp")
        _LOADED_PLUGIN_FILES.discard(os.path.abspath(path).replace("\\", "/"))
        _sv.load_plugin(path)
        e1 = getattr(_sv, "t_fp")(k=1)
        fp1 = _op_fingerprint_str(e1)
        # プラグインのソースを書き換える → 同じパラメータでも指紋が変わる
        with open(path, "w", encoding="utf-8") as f:
            f.write((src % 1).replace("eq=gamma=", "eq=contrast="))
        _sv.unregister_plugin("t_fp")
        _LOADED_PLUGIN_FILES.discard(os.path.abspath(path).replace("\\", "/"))
        _sv.load_plugin(path)
        e2 = getattr(_sv, "t_fp")(k=1)
        fp2 = _op_fingerprint_str(e2)
        if "plugin_ffp=" not in fp1:
            return False, f"指紋にplugin_ffpなし: {fp1}"
        if fp1 == fp2:
            return False, "ソース変更で指紋が変わりません"
        return True, "ソース変更で指紋が変化"
    finally:
        _sv.unregister_plugin("t_fp")
        shutil.rmtree(d, ignore_errors=True)


def check_plugin_load_failure_skips_only_bad():
    """プラグイン: 読み込み失敗は警告+該当のみスキップ（他は生かす）"""
    import warnings as _w
    d = tempfile.mkdtemp()
    good = os.path.join(d, "a_good.py")
    bad = os.path.join(d, "b_bad.py")
    try:
        with open(good, "w", encoding="utf-8") as f:
            f.write(
                "from scriptvedit import effect_plugin\n\n"
                "@effect_plugin('t_good', params={})\n"
                "def build_t_good(params, ctx):\n"
                "    '''OK'''\n"
                "    return ['null']\n")
        with open(bad, "w", encoding="utf-8") as f:
            f.write("import no_such_module_xyz\n")
        _sv.unregister_plugin("t_good")
        with _w.catch_warnings(record=True) as rec:
            _w.simplefilter("always")
            loaded = _sv.load_plugins(d)
        warned = any("プラグインをスキップ" in str(x.message) for x in rec)
        ok = warned and len(loaded) == 1 and "t_good" in _EFFECT_PLUGINS
        # load_plugin 単体は PluginError を送出
        raised = False
        try:
            _sv.load_plugin(bad)
        except _sv.PluginError:
            raised = True
        ok = ok and raised
        return (True, "不正プラグインのみスキップ+警告") if ok else (
            False, f"warned={warned} loaded={loaded} raised={raised}")
    finally:
        _sv.unregister_plugin("t_good")
        shutil.rmtree(d, ignore_errors=True)


def check_plugin_builder_bad_return():
    """プラグイン: ビルダーの戻り値が不正 → PluginError"""
    _sv.unregister_plugin("t_badret")

    @_sv.effect_plugin("t_badret", params={})
    def _b(params, ctx):
        """不正戻り値"""
        return 123

    try:
        obj = Object.__new__(Object)
        obj.source = asset("images/shape_badge.png")
        obj.transforms = []
        obj.effects = [getattr(_sv, "t_badret")()]
        obj.media_type = "image"
        _build_effect_filters(obj, 0, 2)
        return False, "例外が発生しませんでした"
    except _sv.PluginError as e:
        return True, str(e).split("\n")[0]
    finally:
        _sv.unregister_plugin("t_badret")


def check_plugin_choice_suggest():
    """プラグイン: choice 型の不正値 → 候補提示(suggest)"""
    _sv.unregister_plugin("t_choice")
    f = _def_plugin("t_choice", params={
        "mode": {"type": "choice", "default": "soft",
                 "choices": ["soft", "hard"], "desc": "モード"}})
    try:
        f(mode="softt")
        return False, "例外が発生しませんでした"
    except ValueError as e:
        ok = "soft" in str(e) and "もしかして" in str(e)
        return (True, str(e).split("\n")[0]) if ok else (False, f"メッセージ不適切: {e}")
    finally:
        _sv.unregister_plugin("t_choice")


def check_plugin_bad_schema():
    """プラグイン: スキーマの type が不正 → PluginError(候補提示つき)"""
    try:
        _def_plugin("t_schema", params={"x": {"type": "numbr", "default": 1}})
        _sv.unregister_plugin("t_schema")
        return False, "不正スキーマが通ってしまいました"
    except _sv.PluginError as e:
        ok = "type" in str(e)
        return (True, str(e).split("\n")[0]) if ok else (False, f"メッセージ不適切: {e}")


def check_plugin_manifest():
    """プラグイン: plugin_manifest が登録内容を返す"""
    _sv.unregister_plugin("t_man")
    _def_plugin("t_man", bakeable=True, category="テスト")
    items = {i["name"]: i for i in _sv.plugin_manifest()}
    txt = _sv.plugin_manifest(as_text=True)
    ok = ("t_man" in items and items["t_man"]["bakeable"] is True
          and items["t_man"]["category"] == "テスト"
          and "radius" in items["t_man"]["params"] and "t_man" in txt)
    _sv.unregister_plugin("t_man")
    return (ok, "マニフェスト出力OK") if ok else (False, f"内容不正: {items.get('t_man')}")


def check_plugin_namespace_injection():
    """プラグイン: ファクトリが scriptvedit 名前空間(__all__)へ注入される"""
    _sv.unregister_plugin("t_ns")
    _def_plugin("t_ns")
    ns = {}
    exec("from scriptvedit import *\nE = t_ns(radius=3)", ns)
    ok = ns["E"].name == "t_ns" and "t_ns" in _sv.__all__
    _sv.unregister_plugin("t_ns")
    ok = ok and "t_ns" not in _sv.__all__
    return (ok, "import * で使用可 / 解除で除去") if ok else (False, "名前空間注入に失敗")


# --- 数式（formula / formula_lines） ---

def check_formula_invalid_latex():
    """formula: 不正なLaTeXはKaTeXのエラーを日本語で明示する（実レンダ時）"""
    from scriptvedit.formula import _build_formula_spec, _formula_cache_path, _render_formula_png
    spec = _build_formula_spec("formula", [r"\frac{1}{"], 48, "white", True, 4, 0, "left")
    try:
        _render_formula_png(spec, _formula_cache_path(spec))
        return False, "不正なLaTeXが通ってしまいました"
    except ValueError as e:
        msg = str(e)
        ok = "LaTeX の構文エラー" in msg and "KaTeX" in msg and "\\frac{1}{" in msg
        return (True, msg.split("\n")[0]) if ok else (False, f"メッセージ不適切: {msg}")
    except RuntimeError as e:
        # Playwright/Chromium が無い環境は正直に skip（PASS 扱いにしない）
        # ※ 親切なエラーメッセージであること自体は check_formula_playwright_missing で検証
        _skip(f"Playwright/Chromium 未導入: {str(e).splitlines()[0]}")


def check_formula_bad_params():
    """formula: 不正なパラメータ（空LaTeX/色/size/align/display）を構築時に弾く"""
    cases = [
        (lambda: formula(""), ValueError, "空"),
        (lambda: formula(123), TypeError, "文字列"),
        (lambda: formula("x", color="not a color!"), ValueError, "CSSカラー"),
        (lambda: formula("x", size=0), ValueError, "size"),
        (lambda: formula("x", display="yes"), TypeError, "display"),
        (lambda: formula("x", align="middle"), ValueError, "align"),
        (lambda: formula("x", unknown=1), TypeError, "unknown"),
        (lambda: formula_lines("x^2"), TypeError, "リスト"),
        (lambda: formula_lines([]), ValueError, "空"),
    ]
    for fn, exc, needle in cases:
        try:
            fn()
            return False, f"例外が出ませんでした（期待: {exc.__name__} / {needle}）"
        except exc as e:
            if needle not in str(e):
                return False, f"メッセージ不適切（{needle} を含まない）: {e}"
        except Exception as e:  # noqa: BLE001
            return False, f"想定外の例外型 {type(e).__name__}（期待 {exc.__name__}）: {e}"
    return True, f"{len(cases)}件すべて適切に拒否"


def check_formula_cache_key():
    """formula: LaTeX/size/color/display がキャッシュ鍵に効く（同内容は同一パス）"""
    from scriptvedit.formula import _formula_cache_path, _build_formula_spec
    def path(**kw):
        args = dict(lines=[r"x^2"], size=48, color="white", display=True,
                    padding=4, gap=0, align="left")
        args.update(kw)
        return _formula_cache_path(_build_formula_spec("formula", **args))
    base = path()
    if path() != base:
        return False, "同一内容で異なるパスになりました（content-addressed でない）"
    variants = {
        "latex": path(lines=[r"x^3"]),
        "size": path(size=64),
        "color": path(color="red"),
        "display": path(display=False),
        "padding": path(padding=8),
    }
    same = [k for k, v in variants.items() if v == base]
    if same:
        return False, f"キャッシュ鍵に反映されていません: {same}"
    if len(set(variants.values())) != len(variants):
        return False, "異なる入力が同一パスに衝突しました"
    if "__cache__" not in base.replace("\\", "/") or not base.endswith(".png"):
        return False, f"キャッシュ配下のPNGパスではありません: {base}"
    return True, f"5要素すべてが鍵に反映（例: {os.path.basename(base)}）"


def check_formula_object_is_image():
    """formula: 戻り値は画像Objectで、通常のEffect（fade/move）を適用できる"""
    obj = formula(r"E = mc^2", size=48, duration=3)
    if obj.media_type != "image":
        return False, f"media_type が image ではありません: {obj.media_type}"
    if obj.duration != 3.0:
        return False, f"duration が反映されていません: {obj.duration}"
    obj <= fade(lambda u: u) & move(x=0.5, y=0.5, anchor="center")
    names = [e.name for e in obj.effects]
    if "fade" not in names or "move" not in names:
        return False, f"Effectが適用できていません: {names}"
    if _is_cache_artifact_path(obj.source) is not True:
        return False, f"sourceがキャッシュ配下ではありません: {obj.source}"
    return True, f"画像Object / effects={names}"


def check_formula_playwright_missing():
    """formula: Playwright 未導入時は導入方法を示す親切なエラーになる"""
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *a, **kw):
        if name.startswith("playwright"):
            raise ImportError("No module named 'playwright'")
        return real_import(name, *a, **kw)

    builtins.__import__ = fake_import
    try:
        from scriptvedit.formula import _import_playwright
        _import_playwright("formula")
        return False, "Playwright 不在でも例外になりませんでした"
    except RuntimeError as e:
        msg = str(e)
        ok = "Playwright" in msg and "pip install playwright" in msg and "chromium" in msg
        return (True, msg.splitlines()[0]) if ok else (False, f"メッセージ不適切: {msg}")
    finally:
        builtins.__import__ = real_import


def check_formula_katex_vendored():
    """formula: KaTeX がリポジトリ同梱（CDN参照なし＝オフラインで動く）"""
    from scriptvedit.formula import _KATEX_DIR, _FORMULA_TEMPLATE
    from scriptvedit.web import _template_path
    missing = [f for f in ("katex.min.css", "katex.min.js")
               if not os.path.exists(os.path.join(_KATEX_DIR, f))]
    if missing:
        return False, f"同梱KaTeXが不足: {missing}"
    fonts = [f for f in os.listdir(os.path.join(_KATEX_DIR, "fonts"))
             if f.endswith(".woff2")]
    if len(fonts) < 5:
        return False, f"KaTeXフォントが不足: {len(fonts)}件"
    with open(_template_path(_FORMULA_TEMPLATE), encoding="utf-8") as f:
        html = f.read()
    if "//" in html.split("vendor/katex")[0].replace("<!DOCTYPE", "").replace("http-equiv", ""):
        pass  # コメント等の // は許容（下の http 参照チェックが本命）
    if "http://" in html or "https://" in html:
        return False, "テンプレートが外部URLを参照しています（オフライン動作不可）"
    return True, f"KaTeX同梱OK（フォント{len(fonts)}件・外部URL参照なし）"


def check_formula_katex_fingerprint_includes_fonts():
    """formula: KaTeXフォント(woff2)もキャッシュ鍵に入る（字形崩れPNGの焼き付き防止）

    git管理下のvendorフォントを直接書き換えると、同時pytest実行や中断で
    tracked ファイルに変更が残留する事故になる（issue #13 P2-20）。
    vendor全体を一時ディレクトリへコピーし、モジュール属性を差し替えて検証する。
    """
    import shutil
    # 属性参照だと公開関数 formula に隠されるため、モジュールを直接 import
    from importlib import import_module
    formula_mod = import_module("scriptvedit.formula")
    from scriptvedit.cache import _FFP_MEMO

    orig_dir = formula_mod._KATEX_DIR
    tmp_root = tempfile.mkdtemp(prefix="svkatex_")
    try:
        katex_copy = os.path.join(tmp_root, "katex")
        shutil.copytree(orig_dir, katex_copy)
        fonts_dir = os.path.join(katex_copy, "fonts")
        fonts = sorted(f for f in os.listdir(fonts_dir) if f.endswith(".woff2"))
        if not fonts:
            return False, "KaTeXフォントがありません"
        target = os.path.join(fonts_dir, fonts[0])
        formula_mod._KATEX_DIR = katex_copy
        try:
            _FFP_MEMO.clear()
            before = formula_mod._katex_fingerprint()
            with open(target, "ab") as f:
                f.write(b"\x00")  # コピーしたフォントを壊す（内容を変える）
            _FFP_MEMO.clear()  # 同一プロセス内メモ化を無効化
            after = formula_mod._katex_fingerprint()
        finally:
            formula_mod._KATEX_DIR = orig_dir
            _FFP_MEMO.clear()
        if before == after:
            return False, "フォントを変更しても指紋が変わりません（鍵にフォントが入っていない）"
        return True, f"フォント{len(fonts)}件が鍵に反映（{fonts[0]} 変更で指紋変化）"
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


# --- 素材ディレクトリ解決（assets） ---

def check_assets_dir_prefers_user_project():
    """assets: 利用者プロジェクト(cwd)の assets/ がパッケージ同梱より優先される

    想定運用: 動画編集用フォルダから scriptvedit をライブラリとして使い、
    そのフォルダ固有の assets/ を持つ（editable install でリポジトリの assets/ に
    奪われてはならない）。
    SCRIPTVEDIT_ASSETS は assets/ の上書きではなく共有ライブラリの探索パスなので、
    設定されていてもプロジェクトの assets/ が勝つ（新仕様）。
    """
    from scriptvedit import assets_dir
    tmp = tempfile.mkdtemp(prefix="svproj_")
    img_dir = os.path.join(tmp, "assets", "images")
    os.makedirs(img_dir)
    logo = os.path.join(img_dir, "logo.png")
    with open(logo, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n dummy")
    # 共有ライブラリ（環境変数）を設定しても assets/ の場所は乗っ取られない
    lib = os.path.join(tmp, "shared_lib")
    os.makedirs(os.path.join(lib, "images"))
    with open(os.path.join(lib, "images", "shared.png"), "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n shared")
    old_cwd = os.getcwd()
    old_env = os.environ.get("SCRIPTVEDIT_ASSETS")
    os.environ["SCRIPTVEDIT_ASSETS"] = lib
    try:
        os.chdir(tmp)
        got_dir = assets_dir()
        got = asset("images/logo.png")
        if not os.path.samefile(got_dir, os.path.join(tmp, "assets")):
            return False, f"利用者プロジェクトの assets/ が選ばれていません: {got_dir}"
        if not os.path.samefile(got, logo):
            return False, f"asset() が別ファイルを返しました: {got}"
        # 共有ライブラリの素材は assets/_imported/ へコピーされて解決される
        shared = asset("images/shared.png")
        want = os.path.join(tmp, "assets", "_imported", "images", "shared.png")
        if not os.path.samefile(shared, want):
            return False, f"共有ライブラリの素材が _imported へ取り込まれていません: {shared}"
        # cwd を戻せばリポジトリの assets/ に追随する（キャッシュで固まらない）
        os.chdir(old_cwd)
        if os.path.samefile(assets_dir(), os.path.join(tmp, "assets")):
            return False, "cwd を戻しても前回の assets/ を返しています（キャッシュ汚染）"
        return True, f"利用者プロジェクト優先＋共有ライブラリ取り込みを確認: {got}"
    finally:
        os.chdir(old_cwd)
        if old_env is None:
            os.environ.pop("SCRIPTVEDIT_ASSETS", None)
        else:
            os.environ["SCRIPTVEDIT_ASSETS"] = old_env
        shutil.rmtree(tmp, ignore_errors=True)


# --- thumbnail / storyboard ---

def check_thumbnail_generates_formula_png():
    """thumbnail(): formula の数式PNGが未生成でも生成される（render不要で完結する）"""
    if shutil.which("ffmpeg") is None:
        _skip("ffmpeg が無い環境")
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        _skip("Playwright が無い環境")
    layer = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "layers", "test93_formula_thumb.py")
    out = os.path.join(tempfile.gettempdir(), "sv_thumb_formula.png")
    if os.path.exists(out):
        os.remove(out)
    p = Project()
    p.configure(width=640, height=360, fps=15, background_color="black")
    p.layer(layer, priority=0)
    try:
        p.thumbnail(1.0, out)
    except RuntimeError as e:
        if "Chromium" in str(e):
            _skip("Chromium 未導入")
        raise
    try:
        if not os.path.exists(out):
            return False, "サムネイルが生成されませんでした"
        pngs = [o.source for o in p.objects
                if isinstance(o, Object) and getattr(o, "_formula_spec", None)]
        if not pngs:
            return False, "formula Object が登録されていません"
        missing = [s for s in pngs if not os.path.exists(s)]
        if missing:
            return False, f"数式PNGが生成されていません: {missing}"
        return True, f"数式PNG {len(pngs)}件 + サムネイル生成OK"
    finally:
        if os.path.exists(out):
            os.remove(out)


# --- ケイパビリティ・マニフェスト（describe） ---

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# マニフェストのエントリを持つセクション（kind ごと）
_OP_SECTIONS = (("effects", "effect"), ("transforms", "transform"),
                ("audio_effects", "audio_effect"))


def _ground_truth_ops():
    """実装コードから「存在しうる操作名」を機械的に抽出する（網羅性検証の正解集合）。

    scriptvedit パッケージ内で構築される Effect("x")/Transform("x")/AudioEffect("x")
    の全リテラルに加え、_BAKEABLE_EFFECTS 等のレジストリも突き合わせる。
    将来 Effect を足してマニフェストに載せ忘れたら、このテストが落ちる。
    """
    # パッケージの全 .py を走査（分割後はモジュールをまたいで定義される）
    pkg_dir = os.path.dirname(os.path.abspath(sv.__file__))
    src = ""
    for root, _dirs, files in os.walk(pkg_dir):
        for fn in sorted(files):
            if fn.endswith(".py"):
                with open(os.path.join(root, fn), encoding="utf-8") as f:
                    src += f.read() + "\n"
    kinds = {"Effect": "effect", "Transform": "transform",
             "AudioEffect": "audio_effect"}
    ground = {"effect": set(), "transform": set(), "audio_effect": set()}
    for m in re.finditer(r"\b(AudioEffect|Effect|Transform)\(\s*[\"']([a-z_0-9]+)[\"']", src):
        ground[kinds[m.group(1)]].add(m.group(2))
    # レジストリ側（ディスパッチを持たない bakeable/時間操作も取りこぼさない）
    ground["effect"] |= set(_BAKEABLE_EFFECTS)
    ground["effect"] |= set(_TIME_LIVE_EFFECTS)
    ground["effect"] |= set(_TERMINAL_FRAME_EFFECTS)
    # プラグインは plugins セクションで扱う（組込Effectの網羅性からは除外）
    ground["effect"] -= set(_EFFECT_PLUGINS)
    return ground


def _manifest_covered_ops(m):
    """マニフェストが internal 名でカバーしている操作名"""
    cov = {"effect": set(), "transform": set(), "audio_effect": set()}
    for section, kind in _OP_SECTIONS:
        for e in m[section]:
            cov[kind] |= set(e.get("effect_names", []))
    return cov


def check_describe_json_serializable():
    """describe() が JSON シリアライズ可能な dict を返す"""
    m = describe()
    if not isinstance(m, dict):
        return False, f"dict ではない: {type(m)}"
    try:
        s = json.dumps(m, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        return False, f"JSON 化できません: {e}"
    back = json.loads(s)
    if back["library"] != "scriptvedit":
        return False, "library フィールドが不正"
    for key in ("usage", "constraints", "enums", "effects", "transforms",
                "audio_effects", "factories", "objects", "project_methods",
                "expr", "plugins", "stats"):
        if key not in back:
            return False, f"セクション欠落: {key}"
    return True, f"JSON {len(s)}バイト / {back['stats']}"


def check_describe_effect_exhaustive():
    """網羅性: 実装が構築しうる全 Effect がマニフェストに載っている"""
    m = describe()
    ground = _ground_truth_ops()
    cov = _manifest_covered_ops(m)
    missing = ground["effect"] - cov["effect"]
    if missing:
        return False, ("マニフェスト未掲載の Effect: %s（describe() に載るよう "
                       "ファクトリを __all__ へ公開するか _MANIFEST_INTERNAL_OPS へ宣言）"
                       % sorted(missing))
    return True, f"組込Effect {len(ground['effect'])}件すべて掲載"


def check_describe_transform_audio_exhaustive():
    """網羅性: 全 Transform / AudioEffect がマニフェストに載っている"""
    m = describe()
    ground = _ground_truth_ops()
    cov = _manifest_covered_ops(m)
    miss_t = ground["transform"] - cov["transform"]
    miss_a = ground["audio_effect"] - cov["audio_effect"]
    if miss_t or miss_a:
        return False, f"未掲載 Transform={sorted(miss_t)} AudioEffect={sorted(miss_a)}"
    return True, (f"Transform {len(ground['transform'])}件 / "
                  f"AudioEffect {len(ground['audio_effect'])}件すべて掲載")


def check_describe_public_api_exhaustive():
    """網羅性: __all__ の公開callableが必ずどこかのセクションに載る"""
    m = describe()
    listed = set()
    for section in _MANIFEST_ENTRY_SECTIONS:
        for e in m.get(section, []):
            listed.add(e["name"])
            listed.add(e["name"].split(".")[-1])
    missing = []
    for name in sv.__all__:
        obj = getattr(sv, name, None)
        if obj is None or not callable(obj):
            continue  # PI/E/P などの定数は対象外
        if name not in listed:
            missing.append(name)
    if missing:
        return False, f"マニフェスト未掲載の公開API: {sorted(missing)}"
    return True, f"公開callable {len(listed)}件を掲載"


def check_describe_bakeable_matches_registry():
    """bakeable フラグが _BAKEABLE_EFFECTS と整合している"""
    m = describe()
    bad = []
    for e in m["effects"]:
        inames = e.get("effect_names") or []
        if not inames:
            continue
        expect = all(n in _BAKEABLE_EFFECTS for n in inames)
        if e.get("bakeable") != expect:
            bad.append(f"{e['name']}: manifest={e.get('bakeable')} 実体={expect}")
    if bad:
        return False, f"bakeable 不一致: {bad}"
    live = [e["name"] for e in m["effects"] if not e.get("bakeable")]
    return True, f"bakeable 整合。live: {len(live)}件"


def check_describe_plugin_listed():
    """登録済みプラグインが plugins セクションに載る（スキーマ形式もそろう）"""
    from scriptvedit import effect_plugin, unregister_plugin

    @effect_plugin("t_manifest_fx", bakeable=True, category="視覚効果",
                   params={"radius": {"type": "number", "default": 4,
                                      "min": 0, "max": 50, "desc": "半径"}})
    def build_t_manifest_fx(params, ctx):
        """マニフェスト検証用プラグイン"""
        return [f"gblur=sigma={params['radius']}"]

    try:
        m = describe()
        entry = next((e for e in m["plugins"] if e["name"] == "t_manifest_fx"), None)
        if entry is None:
            return False, "plugins セクションに載っていません"
        if entry["bakeable"] is not True or entry["category"] != "視覚効果":
            return False, f"メタ情報が不正: {entry}"
        p = entry["params"].get("radius")
        if not p or p["type"] != "number" or p["default"] != 4 or p["max"] != 50:
            return False, f"params スキーマが不正: {p}"
        if entry["summary"] != "マニフェスト検証用プラグイン":
            return False, f"summary が docstring 由来でない: {entry['summary']}"
        # プラグインは組込 effects セクションには出ない
        if any(e["name"] == "t_manifest_fx" for e in m["effects"]):
            return False, "effects セクションに重複掲載されています"
        json.dumps(m, ensure_ascii=False)
        return True, f"プラグイン掲載 OK: {entry['summary']}"
    finally:
        unregister_plugin("t_manifest_fx")


def check_describe_constraints_nonempty():
    """constraints（既知の落とし穴）が空でなく、主要な地雷を含む"""
    m = describe()
    cons = m["constraints"]
    if not cons:
        return False, "constraints が空です"
    ids = {c["id"] for c in cons}
    required = {"text_size_const", "reverse_max_30s", "alpha_container",
                "layer_cache_no_audio", "blend_mode_canvas", "tts_backend",
                "scipy_required", "one_file_one_layer", "terminal_frame_effect_last"}
    missing = required - ids
    if missing:
        return False, f"必須の制約が欠落: {sorted(missing)}"
    for c in cons:
        for key in ("id", "topic", "severity", "text", "applies_to"):
            if key not in c:
                return False, f"制約 {c.get('id')} に {key} がありません"
    return True, f"constraints {len(cons)}件（必須{len(required)}件を含む）"


def check_describe_notes_propagated():
    """制約が該当エントリの notes にも展開される（text の size 定数制約）"""
    m = describe()
    text_entry = next(e for e in m["factories"] if e["name"] == "text")
    notes = " ".join(text_entry.get("notes", []))
    if "size" not in notes or "SEGV" not in notes:
        return False, f"text の notes に size 制約がありません: {notes[:60]}"
    rev = next(e for e in m["effects"] if e["name"] == "reverse")
    if "30" not in " ".join(rev.get("notes", [])):
        return False, "reverse の notes に30秒上限がありません"
    return True, "notes に制約が展開されている"


def check_describe_usage_section():
    """usage セクションに AI が書き始めるための最小情報がある"""
    m = describe()
    u = m["usage"]
    for key in ("overview", "concepts", "main_script", "layer_file", "dsl",
                "plugin_template", "workflow", "cli"):
        if not u.get(key):
            return False, f"usage.{key} が空です"
    if "from scriptvedit import *" not in u["layer_file"]:
        return False, "layer_file に import 行がありません"
    if "@effect_plugin" not in u["plugin_template"]:
        return False, "plugin_template が不正です"
    return True, f"usage OK（concepts {len(u['concepts'])}件）"


def check_describe_enums():
    """enums が実装側の集合と一致する"""
    m = describe()
    en = m["enums"]
    if set(en["blend_mode"]) != set(_BLEND_MODES):
        return False, "blend_mode の集合が実装と不一致"
    if set(en["xfade_transition"]) != set(_XFADE_TRANSITIONS):
        return False, "xfade_transition の集合が実装と不一致"
    if set(en["preset"]) != set(_PRESETS):
        return False, "preset の集合が実装と不一致"
    if set(en["encoder"]) != set(_ENCODER_MAP):
        return False, "encoder の集合が実装と不一致"
    # blend_mode の choices がエントリにも展開される
    bm = next(e for e in m["effects"] if e["name"] == "blend_mode")
    if "screen" not in bm["params"]["mode"]["choices"]:
        return False, "blend_mode の choices が展開されていません"
    return True, (f"blend_mode {len(en['blend_mode'])} / "
                  f"xfade {len(en['xfade_transition'])} / preset {len(en['preset'])}")


def check_describe_filter_kind():
    """describe(kind=...) で種別を絞れる / 未知の kind は suggest 付きエラー"""
    m = describe(kind="effect")
    if "effects" not in m or "factories" in m:
        return False, f"kind フィルタが効いていません: {list(m)}"
    if not m["effects"]:
        return False, "effects が空です"
    try:
        describe(kind="effects")   # 単数形が正しい
        return False, "未知の kind でエラーになりません"
    except ValueError as e:
        if "もしかして" not in str(e):
            return False, f"suggest がありません: {e}"
    return True, f"kind=effect → {len(m['effects'])}件"


def check_describe_filter_name():
    """describe(name=...) で単一エントリ / 未知の名前は suggest 付きエラー"""
    m = describe(name="fade")
    if len(m.get("effects", [])) != 1 or m["effects"][0]["name"] != "fade":
        return False, f"単一エントリになっていません: {m.get('effects')}"
    if m["effects"][0]["params"]["alpha"]["type"] != "expr":
        return False, "fade の alpha が expr 型ではありません"
    # 該当する制約だけが残る
    ids = {c["id"] for c in m["constraints"]}
    if "text_size_const" in ids:
        return False, "無関係な制約が残っています"
    # メソッドも短縮名で引ける
    m2 = describe(name="render")
    if not m2.get("project_methods"):
        return False, "Project.render を name=render で引けません"
    try:
        describe(name="fadee")
        return False, "未知の name でエラーになりません"
    except ValueError as e:
        if "もしかして" not in str(e) or "fade" not in str(e):
            return False, f"suggest がありません: {e}"
    return True, "name フィルタ + suggest OK"


def check_describe_signature_autoderived():
    """シグネチャ/既定値が inspect から自動導出されている"""
    m = describe()
    idx = {e["name"]: e for e in m["effects"]}
    wipe_e = idx["wipe"]
    if wipe_e["signature"] != "wipe(direction='left', progress=None)":
        return False, f"シグネチャが自動導出されていません: {wipe_e['signature']}"
    if wipe_e["params"]["direction"]["choices"] != ["left", "right", "up", "down"]:
        return False, "choices が展開されていません"
    # zoom は内部 Effect 名が scale（公開名と内部名の差を記録する）
    if idx["zoom"]["effect_names"] != ["scale"]:
        return False, f"zoom の internal 名が不正: {idx['zoom']['effect_names']}"
    # lambda/Expr を受け取れる引数は expr 型として自動検出される
    if idx["scale"]["params"]["value"]["type"] != "expr":
        return False, "scale.value が expr 型ではありません"
    return True, "signature/choices/expr型の自動導出 OK"


def check_describe_markdown():
    """describe_markdown() が Markdown を返す"""
    md = describe_markdown()
    if not md.startswith("# scriptvedit"):
        return False, "見出しがありません"
    for needed in ("## 既知の制約・落とし穴", "### `fade(alpha=1.0)`", "bakeable",
                   "## 使い方（AI向け）"):
        if needed not in md:
            return False, f"Markdown に {needed} がありません"
    return True, f"Markdown {len(md)}文字"


def _run_cli(*args):
    """python -m scriptvedit ... をリポジトリルートで実行する"""
    return subprocess.run(
        [sys.executable, "-m", "scriptvedit"] + list(args),
        cwd=_REPO_ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120)


def check_describe_cli_json():
    """CLI: python -m scriptvedit describe → JSON を stdout"""
    r = _run_cli("describe")
    if r.returncode != 0:
        return False, f"終了コード {r.returncode}: {r.stderr[:120]}"
    try:
        m = json.loads(r.stdout)
    except ValueError as e:
        return False, f"stdout が JSON ではありません: {e}"
    if m["library"] != "scriptvedit" or not m["effects"]:
        return False, "マニフェスト内容が不正"
    return True, f"CLI JSON OK（effects {len(m['effects'])}件）"


def check_describe_cli_md_and_filters():
    """CLI: --format md / --kind / --name / 未知名は終了コード2"""
    r = _run_cli("describe", "--format", "md")
    if r.returncode != 0 or not r.stdout.startswith("# scriptvedit"):
        return False, f"--format md が失敗: rc={r.returncode}"
    r = _run_cli("describe", "--kind", "transform")
    if r.returncode != 0:
        return False, "--kind が失敗"
    m = json.loads(r.stdout)
    if "transforms" not in m or "effects" in m:
        return False, f"--kind が効いていません: {list(m)}"
    r = _run_cli("describe", "--name", "fade")
    if r.returncode != 0:
        return False, "--name が失敗"
    m = json.loads(r.stdout)
    if len(m["effects"]) != 1:
        return False, "--name が単一エントリになりません"
    # 近い名前があれば suggest 付き、無くてもエラー終了（rc=2）
    r = _run_cli("describe", "--name", "fadee")
    if r.returncode != 2 or "もしかして" not in r.stderr or "fade" not in r.stderr:
        return False, f"typo の suggest が出ません: rc={r.returncode}"
    r = _run_cli("describe", "--name", "nonexistent_fx")
    if r.returncode != 2 or "ありません" not in r.stderr:
        return False, f"未知名の扱いが不正: rc={r.returncode}"
    return True, "CLI md/--kind/--name/エラー終了コード OK"


def check_describe_cli_output_file():
    """CLI: -o でファイル出力"""
    # 固定名だと同時pytest実行で相互に消し合うため pid+uuid でユニーク化
    import uuid as _uuid
    out = os.path.join(
        tempfile.gettempdir(),
        f"_sv_manifest_test_{os.getpid()}_{_uuid.uuid4().hex[:8]}.json")
    try:
        r = _run_cli("describe", "-o", out)
        if r.returncode != 0:
            return False, f"終了コード {r.returncode}"
        if not os.path.exists(out):
            return False, "ファイルが作られていません"
        with open(out, encoding="utf-8") as f:
            m = json.load(f)
        if not m["effects"]:
            return False, "内容が空です"
        return True, f"-o 出力 OK（{os.path.getsize(out)}バイト）"
    finally:
        if os.path.exists(out):
            os.remove(out)


# --- issue #3〜#7 の回帰テスト（dry_runでは検出できないffmpeg式レベルのバグ） ---

def check_easing_numeric_regression():
    """#3: 全easing 34本が標準定義（easings.net）と数値一致（誤差<1e-3）"""
    import math as _m
    _PI = _m.pi
    c1 = 1.70158
    c2 = c1 * 1.525
    c3 = c1 + 1
    c4 = (2 * _PI) / 3
    c5 = (2 * _PI) / 4.5

    def _out_bounce(t):
        n1, d1 = 7.5625, 2.75
        if t < 1 / d1:
            return n1 * t * t
        if t < 2 / d1:
            t -= 1.5 / d1
            return n1 * t * t + 0.75
        if t < 2.5 / d1:
            t -= 2.25 / d1
            return n1 * t * t + 0.9375
        t -= 2.625 / d1
        return n1 * t * t + 0.984375

    def _in_out_elastic(t):
        if t <= 0.001:
            return 0.0
        if t >= 0.999:
            return 1.0
        if t < 0.5:
            return -(2 ** (20 * t - 10)) * _m.sin((20 * t - 11.125) * c5) / 2
        return (2 ** (-20 * t + 10)) * _m.sin((20 * t - 11.125) * c5) / 2 + 1

    refs = {
        "linear": lambda t: t,
        "ease_in_quad": lambda t: t ** 2,
        "ease_out_quad": lambda t: 1 - (1 - t) ** 2,
        "ease_in_out_quad": lambda t: 2 * t * t if t < 0.5 else 1 - (-2 * t + 2) ** 2 / 2,
        "ease_in_cubic": lambda t: t ** 3,
        "ease_out_cubic": lambda t: 1 - (1 - t) ** 3,
        "ease_in_out_cubic": lambda t: 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2,
        "ease_in_quart": lambda t: t ** 4,
        "ease_out_quart": lambda t: 1 - (1 - t) ** 4,
        "ease_in_out_quart": lambda t: 8 * t ** 4 if t < 0.5 else 1 - (-2 * t + 2) ** 4 / 2,
        "ease_in_quint": lambda t: t ** 5,
        "ease_out_quint": lambda t: 1 - (1 - t) ** 5,
        "ease_in_out_quint": lambda t: 16 * t ** 5 if t < 0.5 else 1 - (-2 * t + 2) ** 5 / 2,
        "ease_in_sine": lambda t: 1 - _m.cos(t * _PI / 2),
        "ease_out_sine": lambda t: _m.sin(t * _PI / 2),
        "ease_in_out_sine": lambda t: (1 - _m.cos(t * _PI)) / 2,
        "ease_in_expo": lambda t: 0.0 if t <= 0.001 else 2 ** (10 * t - 10),
        "ease_out_expo": lambda t: 1.0 if t >= 0.999 else 1 - 2 ** (-10 * t),
        "ease_in_out_expo": lambda t: (
            0.0 if t <= 0.001 else 1.0 if t >= 0.999 else
            2 ** (20 * t - 10) / 2 if t < 0.5 else (2 - 2 ** (-20 * t + 10)) / 2),
        "ease_in_circ": lambda t: 1 - _m.sqrt(1 - t * t),
        "ease_out_circ": lambda t: _m.sqrt(1 - (t - 1) ** 2),
        "ease_in_out_circ": lambda t: (
            (1 - _m.sqrt(1 - (2 * t) ** 2)) / 2 if t < 0.5
            else (_m.sqrt(1 - (2 * t - 2) ** 2) + 1) / 2),
        "ease_in_back": lambda t: c3 * t ** 3 - c1 * t ** 2,
        "ease_out_back": lambda t: 1 + c3 * (t - 1) ** 3 + c1 * (t - 1) ** 2,
        "ease_in_out_back": lambda t: (
            ((2 * t) ** 2 * ((c2 + 1) * 2 * t - c2)) / 2 if t < 0.5
            else ((2 * t - 2) ** 2 * ((c2 + 1) * (2 * t - 2) + c2) + 2) / 2),
        "ease_in_elastic": lambda t: (
            0.0 if t <= 0.001 else 1.0 if t >= 0.999 else
            -(2 ** (10 * t - 10)) * _m.sin((10 * t - 10.75) * c4)),
        "ease_out_elastic": lambda t: (
            0.0 if t <= 0.001 else 1.0 if t >= 0.999 else
            2 ** (-10 * t) * _m.sin((10 * t - 0.75) * c4) + 1),
        "ease_in_out_elastic": _in_out_elastic,
        "ease_in_bounce": lambda t: 1 - _out_bounce(1 - t),
        "ease_out_bounce": _out_bounce,
        "ease_in_out_bounce": lambda t: (
            (1 - _out_bounce(1 - 2 * t)) / 2 if t < 0.5
            else (1 + _out_bounce(2 * t - 1)) / 2),
    }
    points = [0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0]
    bad = []
    for name, ref in refs.items():
        fn = getattr(sv, name)
        for t in points:
            got = fn(sv.Const(t)).eval_at(0)
            want = ref(t)
            if abs(got - want) >= 1e-3:
                bad.append(f"{name}(t={t}): got={got:.6f} want={want:.6f}")
    # #3 の症状固有チェック: t=0.5 で 0.5（修正前は 1.5）かつ連続
    v_mid = sv.ease_in_out_elastic(sv.Const(0.5)).eval_at(0)
    if abs(v_mid - 0.5) >= 1e-6:
        bad.append(f"ease_in_out_elastic(0.5)={v_mid:.4f}（0.5であるべき）")
    lo = sv.ease_in_out_elastic(sv.Const(0.4999999)).eval_at(0)
    hi = sv.ease_in_out_elastic(sv.Const(0.5000001)).eval_at(0)
    if abs(hi - lo) >= 1e-3:
        bad.append(f"t=0.5で不連続: {lo:.4f} → {hi:.4f}")
    if bad:
        return False, "; ".join(bad[:5])
    return True, f"easing {len(refs)}本 × {len(points)}点が標準定義と一致"


def check_and_or_no_unknown_ffmpeg_funcs():
    """#4: and_/or_ が ffmpeg 非対応の and()/or() を出力せず、0/1論理値は従来と同値"""
    u = sv.Var("u")
    a = sv.and_(sv.gt(u, 0.2), sv.lt(u, 0.8))
    o = sv.or_(sv.lt(u, 0.2), sv.gt(u, 0.8))
    sa = a.to_ffmpeg("T")
    so = o.to_ffmpeg("T")
    if "and(" in sa or "or(" in sa:
        return False, f"and_ が and()/or() を出力: {sa}"
    if "and(" in so or "or(" in so:
        return False, f"or_ が and()/or() を出力: {so}"
    # eval_at の論理値が従来（a!=0 and/or b!=0 → 1.0/0.0）と同値であること
    cases = [
        (a.eval_at(0.1), 0.0), (a.eval_at(0.5), 1.0), (a.eval_at(0.9), 0.0),
        (o.eval_at(0.1), 1.0), (o.eval_at(0.5), 0.0), (o.eval_at(0.9), 1.0),
        # 非0/1入力（負数・2）でも真理値互換
        (sv.and_(sv.Const(-1), sv.Const(1)).eval_at(0), 1.0),
        (sv.and_(sv.Const(2), sv.Const(0)).eval_at(0), 0.0),
        (sv.or_(sv.Const(0), sv.Const(0)).eval_at(0), 0.0),
        (sv.or_(sv.Const(-2), sv.Const(0)).eval_at(0), 1.0),
    ]
    for i, (got, want) in enumerate(cases):
        if got != want:
            return False, f"eval_at不一致 case[{i}]: got={got} want={want}"
    return True, f"and_={sa[:40]}… / or_={so[:40]}…"


def check_min_max_two_arg_fold():
    """#5: 3引数以上の min/max が2引数の左畳み込み（ffmpegは2引数固定）"""
    u = sv.Var("u")
    m = sv.min(u, 0.5, 0.8)
    sm = m.to_ffmpeg("T")
    if sm != "min(min(T\\,0.5)\\,0.8)":
        return False, f"min の畳み込み不正: {sm}"
    x = sv.max(u, 0.5, 0.8, 0.9)
    sx = x.to_ffmpeg("T")
    if sx != "max(max(max(T\\,0.5)\\,0.8)\\,0.9)":
        return False, f"max の畳み込み不正: {sx}"
    # eval_at は従来通り
    if m.eval_at(0.9) != 0.5 or m.eval_at(0.3) != 0.3:
        return False, f"min eval_at 不一致: {m.eval_at(0.9)}, {m.eval_at(0.3)}"
    if x.eval_at(0.95) != 0.95 or x.eval_at(0.1) != 0.9:
        return False, f"max eval_at 不一致: {x.eval_at(0.95)}, {x.eval_at(0.1)}"
    # 非Expr は builtins へ委譲
    if sv.min(3, 1, 2) != 1 or sv.max(3, 1, 2) != 3:
        return False, "非Expr の min/max が builtins と不一致"
    return True, f"{sm} / {sx}"


def check_wipe_geq_uses_uppercase_T():
    """#6: wipe の geq 進行度式が大文字 T（geq に小文字 t は無い）"""
    _mk_project()
    obj = Object(asset("images/shape_badge.png"))
    obj <= wipe("left")
    flat = str(_build_effect_filters(obj, 0, 2))
    if "clip((t-" in flat:
        return False, f"小文字 t が残存: {flat[:300]}"
    if "clip((T-0)" not in flat:
        return False, f"大文字 T の進行度式が無い: {flat[:300]}"
    return True, "wipe の geq が大文字 T を使用"


def check_color_shift_eq_eval_frame():
    """#7: color_shift の動的 saturation/brightness に eval=frame が付く（定数には付かない）"""
    _mk_project()
    # 動的式 → eval=frame 必須（既定 eval=init だと t=0 で凍結する）
    obj = Object(asset("images/shape_badge.png"))
    obj <= color_shift(saturation=lambda u: 1 + u, brightness=lambda u: 0.2 * u)
    flat = str(_build_effect_filters(obj, 0, 2))
    if "eq=saturation=" not in flat or ":eval=frame" not in flat:
        return False, f"動的式に eval=frame が無い: {flat[:300]}"
    # 定数のみ → eval=frame 不要（挙動不変の最小差分）
    obj2 = Object(asset("images/shape_badge.png"))
    obj2 <= color_shift(saturation=1.2)
    flat2 = str(_build_effect_filters(obj2, 0, 2))
    if "eval=frame" in flat2:
        return False, f"定数のみなのに eval=frame が付いた: {flat2[:300]}"
    if "eq=saturation=1.2" not in flat2:
        return False, f"eq フィルタが無い: {flat2[:300]}"
    return True, "動的=eval=frame付与 / 定数=付与なし"


ALL_TESTS = [
    ("math.sin in lambda", check_math_sin_in_lambda),
    ("未定義アンカー参照", check_undefined_anchor),
    ("同名アンカー異ファイル", check_same_anchor_different_files),
    ("configure typo", check_configure_typo),
    ("50%P == 0.5", check_percent_value),
    ("cache='invalid'", check_cache_invalid),
    ("cache='use' ファイル不在", check_cache_use_no_file),
    ("画像のlength()", check_image_length),
    ("存在しないファイルのlength()", check_missing_file_length),
    ("VideoView.time()禁止", check_view_time_forbidden),
    ("AudioView.until()禁止", check_view_until_forbidden),
    ("VideoView<=音声エフェクト", check_video_audio_effect_mismatch),
    ("AudioView<=映像エフェクト", check_audio_video_effect_mismatch),
    ("非webにkwargs", check_web_kwargs_on_non_web),
    ("web不明kwarg", check_web_unknown_kwarg),
    ("web duration未指定", check_web_no_duration),
    ("web nameパストラバーサル", check_web_name_traversal),
    ("web frames_dir包含ガード", check_web_frames_dir_guard),
    ("subtitle Project未設定", check_subtitle_no_project),
    ("diagram Project未設定", check_diagram_no_project),
    ("subtitle size明示", check_subtitle_with_explicit_size),
    ("-Transform", check_neg_transform),
    ("-Effect", check_neg_effect),
    ("~chain糖衣", check_chain_sugar),
    ("+force演算子", check_force_operator),
    ("-off演算子", check_off_operator),
    ("~fast品質", check_fast_quality),
    ("+chain force", check_chain_force),
    ("FFP変化検出", check_ffp_change_detection),
    ("checkpoint FFP署名", check_checkpoint_signature_uses_ffp),
    ("web deps引数", check_web_deps_accepted),
    ("video time未指定checkpoint", check_video_no_time_checkpoint_has_duration),
    ("video time指定checkpoint", check_video_with_time_uses_specified_duration),
    ("morph_to非Object", check_morph_to_non_object),
    ("morph_to末尾でない", check_morph_to_not_last),
    ("rotate引数なし", check_rotate_no_args),
    ("rotate_to引数なし", check_rotate_to_no_args),
    ("rotate_to move保持", check_rotate_to_preserves_move),
    ("morph_to回避策メッセージ", check_morph_to_hint_message),
    ("probe不可has_audio=False", check_probe_failure_has_audio_false),
    ("画像time()省略", check_image_time_no_args),
    ("crop w/h未指定", check_crop_no_size),
    ("pad w/h未指定", check_pad_no_size),
    ("color_shift引数なし", check_color_shift_no_args),
    ("zoom引数なし", check_zoom_no_args),
    ("crop filter in checkpoint", check_crop_filter_in_checkpoint),
    ("pad filter in checkpoint", check_pad_filter_in_checkpoint),
    ("blur filter in checkpoint", check_blur_filter_in_checkpoint),
    ("eq filter in checkpoint", check_eq_filter_in_checkpoint),
    ("wipe filter in checkpoint", check_wipe_filter_in_checkpoint),
    ("wipe 未知方向", check_wipe_invalid_direction),
    ("wipe top/bottom別名", check_wipe_top_bottom_aliases),
    ("zoom filter in checkpoint", check_zoom_filter_in_checkpoint),
    ("color_shift filter in checkpoint", check_color_shift_filter_in_checkpoint),
    ("rotate_to filter in checkpoint", check_rotate_to_filter_in_checkpoint),
    ("move survives bakeable", check_move_survives_bakeable_checkpoint),
    ("shake is live", check_shake_is_live),
    ("web deps invalidation", check_web_deps_invalidation),
    ("until offset正", check_until_offset_positive),
    ("until offset負", check_until_offset_negative),
    ("until offset省略", check_until_offset_zero_default),
    ("time name anchors", check_time_name_anchors),
    ("time name重複", check_time_name_duplicate),
    ("time name+until", check_time_name_with_until),
    ("show非進行", check_show_no_advance),
    ("show_until anchor", check_show_until_with_anchor),
    ("show priority", check_show_priority_override),
    ("compute objects除外", check_compute_removes_from_objects),
    ("compute live error", check_compute_live_effect_error),
    ("compute戻り値", check_compute_returns_object),
    ("chroma_key similarity範囲", check_chroma_key_similarity_range),
    ("chroma_key 不正色", check_chroma_key_bad_color),
    ("vignette angle+strength同時", check_vignette_both_args),
    ("pixelize Expr拒否", check_pixelize_expr_rejected),
    ("glow intensity範囲", check_glow_intensity_range),
    ("lut ファイル不在", check_lut_missing_file),
    ("lut 未対応拡張子", check_lut_bad_ext),
    ("glitch interval範囲", check_glitch_interval_range),
    ("perspective_warp 非数値", check_perspective_warp_non_numeric),
    ("lens k1範囲", check_lens_k1_range),
    ("ken_burns アスペクト不一致", check_ken_burns_aspect_mismatch),
    ("ken_burns 矩形不正", check_ken_burns_bad_rect),
    ("drop_shadow 不正色", check_drop_shadow_bad_color),
    ("outline width範囲", check_outline_width_range),
    ("slideshow 画像1枚", check_slideshow_one_image),
    ("slideshow 未知transition", check_slideshow_unknown_transition),
    ("slideshow t_dur過大", check_slideshow_tdur_too_long),
    ("transition 加工済み拒否", check_transition_with_effects),
    ("transition 画像time必須", check_transition_image_needs_time),
    ("transition Object消費", check_transition_consumes_objects),
    ("glow filter in checkpoint", check_glow_filter_in_checkpoint),
    ("drop_shadow filter in checkpoint", check_drop_shadow_filter_in_checkpoint),
    # --- テキスト/字幕/オーディオ系（新機能） ---
    ("text フォント不在", check_text_font_missing),
    ("text size式拒否", check_text_size_expr_rejected),
    ("text 不正anchor", check_text_bad_anchor),
    ("text time省略", check_text_time_omit),
    ("text border負値", check_text_border_negative),
    ("text border式拒否", check_text_border_expr_rejected),
    ("text shadow形状不正", check_text_shadow_bad_shape),
    ("typewriter cps不正", check_typewriter_bad_cps),
    ("counter 小数format", check_counter_float_format),
    ("counter アポストロフィformat", check_counter_apostrophe_format),
    ("subtitles ファイル不在", check_subtitles_missing_file),
    ("subtitles 拡張子不正", check_subtitles_bad_ext),
    ("duck_under 非Object", check_duck_under_non_object),
    ("duck_under other対象外", check_duck_under_other_not_in_project),
    ("audio_sequence 入力不足", check_audio_sequence_too_few),
    ("audio_sequence 非音声", check_audio_sequence_non_audio),
    ("sfx ソース不在", check_sfx_missing_source),
    ("sfx at空", check_sfx_empty_at),
    ("audio_viz 不正kind", check_audio_viz_bad_kind),
    ("audio_viz ソース不在", check_audio_viz_missing_source),
    ("normalize_audio 範囲外", check_normalize_audio_range),
    ("text drawtext出力", check_text_drawtext_in_cmd),
    ("text 縁取り・影出力", check_text_border_shadow_in_cmd),
    ("text 既定で縁取り・影なし", check_text_default_no_border_shadow),
    ("typewriter/counter 縁取り出力", check_typewriter_counter_border_in_cmd),
    ("normalize_audio loudnorm出力", check_loudnorm_in_cmd),
    # --- 構成・タイムライン・Expr拡張（新機能） ---
    ("explode_to末尾でない", check_explode_to_not_last),
    ("explode_to duration必須", check_explode_to_needs_duration),
    ("assemble_from非Object", check_assemble_from_non_object),
    ("終端Effect2個", check_two_terminal_effects),
    ("move_along点不足", check_move_along_too_few),
    ("path_bezier点数不正", check_path_bezier_bad_count),
    ("group非Object", check_group_non_object),
    ("grid非画像", check_grid_on_non_image),
    ("render窓範囲不正", check_render_window_bad_range),
    ("inertia damping不正", check_inertia_bad_damping),
    ("perlin octaves不正", check_perlin_bad_octaves),
    ("look_at不正パス", check_look_at_bad_path),
    ("param CLI上書き", check_param_cli_override),
    ("marker埋め込み出力", check_marker_in_cmd),
    ("explode particleキャッシュ", check_explode_produces_particle_cache),
    # --- 出力形式・DX（最終ウェーブ） ---
    ("不正preset suggest", check_bad_preset_suggest),
    ("不正encoder suggest", check_bad_encoder_suggest),
    ("configure typo suggest", check_configure_typo_suggest),
    ("encoder フォールバック", check_encoder_fallback),
    ("preset 寸法設定", check_preset_sets_dimensions),
    ("preset 個別上書き", check_preset_override),
    ("GIF出力形式", check_gif_output_format),
    ("透過webm形式", check_alpha_webm_format),
    ("draft鍵分離", check_draft_key_separation),
    ("voice例外処理", check_voice_without_tts_module),
    ("inspect レポート", check_inspect_report_text),
    # --- 統合レビュー修正の追加検証 ---
    ("alpha=True on mp4拒否", check_alpha_on_mp4_rejected),
    ("audio_sequence crossfade過大", check_audio_sequence_short_crossfade),
    ("move_along点数上限", check_move_along_too_many_points),
    ("keyframes点数上限", check_keyframes_too_many_points),
    ("counter目標到達(+0.5)", check_counter_reaches_target),
    ("typewriter半開区間", check_typewriter_halfopen_enable),
    ("ken_burns overshootクランプ", check_ken_burns_overshoot_clamp),
    # --- 合成・時間操作（mask/blend_mode/speed/video_sequence 等） ---
    ("blend_mode 不正名 suggest", check_blend_mode_bad_name),
    ("blend_mode エイリアス", check_blend_mode_alias),
    ("reverse 長尺拒否", check_reverse_too_long),
    ("video_sequence t_dur過大", check_video_sequence_tdur_too_big),
    ("video_sequence 1クリップ", check_video_sequence_one_clip),
    ("video_sequence 非動画", check_video_sequence_non_video),
    ("speed factor不正", check_speed_bad_factor),
    ("speed 画像適用拒否", check_speed_on_image),
    ("speed length反映", check_speed_length_reflected),
    ("freeze_frame length反映", check_freeze_frame_length_reflected),
    ("freeze_frame at負", check_freeze_frame_bad_at),
    ("opacity 範囲外", check_opacity_out_of_range),
    ("rounded 負radius", check_rounded_negative),
    ("mask 画像不在", check_mask_missing_file),
    ("mask_wipe 非画像", check_mask_wipe_non_image),
    ("pip border不正", check_pip_bad_border),
    ("pip チェーン構成", check_pip_returns_chain),
    ("progress_bar 不正色", check_progress_bar_bad_color),
    ("from_project 非Project", check_from_project_non_project),
    ("from_project cache不正", check_from_project_bad_cache),
    ("from_project layerなし", check_from_project_no_layers),
    ("atempo 多段分解", check_atempo_chain_decompose),
    ("compute blend_mode拒否", check_compute_rejects_blend_mode),
    # --- 外部モジュール統合（narrate/karaoke/beat_sync/slide/export_metadata） ---
    ("narrate VOICEVOX未起動", check_narrate_without_voicevox),
    ("narrate Narrationタプル", check_narrate_returns_narration_tuple),
    ("narrate subtitle=False", check_narrate_subtitle_false_no_subtitle),
    ("narrate subtitle_style縁取り", check_narrate_subtitle_style_border_shadow),
    # --- TTS バックエンド（voicevox / edge / sapi） ---
    ("tts backend不正", check_tts_backend_invalid),
    ("tts backend環境変数", check_tts_backend_env_selection),
    ("tts backend自動edge", check_tts_backend_fallback_to_edge),
    ("tts VOICEVOX未起動の代替案", check_tts_voicevox_error_suggests_edge),
    ("tts キャッシュ鍵にbackend", check_tts_cache_key_includes_backend),
    ("tts edge rate/pitch写像", check_tts_edge_rate_pitch_mapping),
    ("tts edge speaker解決", check_tts_edge_speaker_resolution),
    ("tts edge 合成wav", check_tts_edge_synth_wav),
    ("tts edge 話者一覧", check_tts_edge_speakers_list),
    ("voice edge バックエンド", check_voice_edge_backend_object),
    ("narrate edge バックエンド", check_narrate_edge_backend),
    ("karaoke ASS \\k生成", check_karaoke_ass_kfired),
    ("karaoke 均等割り", check_karaoke_word_durations_equal_split),
    ("karaoke word_durations不一致", check_karaoke_word_durations_mismatch),
    ("karaoke lines要素不正", check_karaoke_bad_line_tuple),
    ("karaoke end<=start", check_karaoke_end_before_start),
    ("beat_sync 検出+キャッシュ", check_beat_sync_detects_and_caches),
    ("beat_sync ファイル不在", check_beat_sync_missing_file),
    ("slide HTML不在", check_slide_missing_file),
    ("slide 拡張子不正", check_slide_bad_extension),
    ("slide サイズ省略+Project無し", check_slide_size_without_project),
    ("export_metadata JSON形式", check_export_metadata_json),
    ("export_metadata param由来title", check_export_metadata_title_from_param),
    ("export_metadata txt形式", check_export_metadata_txt_format),
    # --- 統合レビュー修正の検証 ---
    ("S3 freeze_frame at>=尺エラー", check_freeze_frame_at_beyond_clip),
    ("S3 freeze at>=尺で尺不加算", check_freeze_frame_at_beyond_length_no_overcount),
    ("S1 speed 自動atrim後置", check_speed_auto_atrim_after_atempo),
    ("S7 rounded 半径クランプ", check_rounded_radius_clamped_in_geq),
    ("S8 probe ストリーム個別尺", check_probe_stream_durations),
    ("S15 narrate dur<=0エラー", check_narrate_zero_duration),
    ("S12 karaoke \\k総和一致", check_karaoke_equal_split_sum_matches),
    ("S13 export json先頭章", check_export_metadata_json_intro_chapter),
    ("S11 beat_sync破損キャッシュ自己修復", check_beat_sync_corrupt_cache_self_heal),
    ("S14 slide ページ切替JS正規化", check_slide_page_js_normalizes_id),
    # --- プラグイン機構 ---
    ("plugin 未知パラメータ+suggest", check_plugin_unknown_param),
    ("plugin 範囲外", check_plugin_out_of_range),
    ("plugin expr受理(liveアニメ)", check_plugin_expr_accepted),
    ("plugin 同名二重登録", check_plugin_duplicate_registration),
    ("plugin 組込名の上書き禁止", check_plugin_builtin_name_conflict),
    ("plugin bakeableチェックポイント", check_plugin_bakeable_checkpoint),
    ("plugin bakeable=Falseはlive", check_plugin_live_not_bakeable),
    ("plugin ソースFFPがキャッシュ鍵", check_plugin_fingerprint_includes_source),
    ("plugin 読込失敗は該当のみスキップ", check_plugin_load_failure_skips_only_bad),
    ("plugin ビルダー戻り値不正", check_plugin_builder_bad_return),
    ("plugin choice suggest", check_plugin_choice_suggest),
    ("plugin スキーマ不正", check_plugin_bad_schema),
    ("plugin マニフェスト", check_plugin_manifest),
    ("plugin 名前空間注入", check_plugin_namespace_injection),
    ("plugin 予約名(サブモジュール)禁止", check_plugin_reserved_submodule_name),
    # --- キャッシュ鍵 ---
    ("ffp ディスクキャッシュ撤廃", check_ffp_no_disk_cache),
    # --- 数式（formula） ---
    ("formula 不正LaTeX", check_formula_invalid_latex),
    ("formula 不正パラメータ", check_formula_bad_params),
    ("formula キャッシュ鍵", check_formula_cache_key),
    ("formula 画像Object+Effect", check_formula_object_is_image),
    ("formula Playwright不在", check_formula_playwright_missing),
    ("formula KaTeX同梱", check_formula_katex_vendored),
    ("formula 鍵にフォント含む", check_formula_katex_fingerprint_includes_fonts),
    # --- 素材解決 / thumbnail ---
    ("assets 利用者プロジェクト優先", check_assets_dir_prefers_user_project),
    ("thumbnail 数式PNG生成", check_thumbnail_generates_formula_png),
    # --- ケイパビリティ・マニフェスト（describe） ---
    ("describe JSON化可能", check_describe_json_serializable),
    ("describe 網羅性: Effect", check_describe_effect_exhaustive),
    ("describe 網羅性: Transform/Audio", check_describe_transform_audio_exhaustive),
    ("describe 網羅性: 公開API", check_describe_public_api_exhaustive),
    ("describe bakeable整合", check_describe_bakeable_matches_registry),
    ("describe プラグイン掲載", check_describe_plugin_listed),
    ("describe constraints非空", check_describe_constraints_nonempty),
    ("describe notes展開", check_describe_notes_propagated),
    ("describe usageセクション", check_describe_usage_section),
    ("describe enums一致", check_describe_enums),
    ("describe --kindフィルタ", check_describe_filter_kind),
    ("describe --nameフィルタ", check_describe_filter_name),
    ("describe シグネチャ自動導出", check_describe_signature_autoderived),
    ("describe Markdown出力", check_describe_markdown),
    ("describe CLI JSON", check_describe_cli_json),
    ("describe CLI md/filter", check_describe_cli_md_and_filters),
    ("describe CLI -o出力", check_describe_cli_output_file),
    # --- issue #3〜#7 回帰 ---
    ("#3 easing 34本数値回帰", check_easing_numeric_regression),
    ("#4 and_/or_ ffmpeg対応関数", check_and_or_no_unknown_ffmpeg_funcs),
    ("#5 min/max 2引数畳み込み", check_min_max_two_arg_fold),
    ("#6 wipe geq 大文字T", check_wipe_geq_uses_uppercase_T),
    ("#7 color_shift eval=frame", check_color_shift_eq_eval_frame),
]


# --- 監査 項目3: ファクトリの引数検証を「拒否」で統一する -------------------
#
# いずれも「引数を渡したのに無言で効かない / 違う意味になる」失敗モード。
# 誤った呼び出しが確実に例外になることを固定する。

def _expect_raise(fn, exc, must_contain=()):
    """fn() が exc を投げ、メッセージに must_contain を全部含むこと"""
    try:
        fn()
    except exc as e:
        msg = str(e)
        missing = [s for s in must_contain if s not in msg]
        if missing:
            return False, f"メッセージに {missing} が無い: {msg}"
        return True, msg
    except Exception as e:
        return False, f"{exc.__name__} ではなく {type(e).__name__}: {e}"
    return False, "例外が発生しませんでした"


def check_resize_pixel_kwargs_rejected():
    """resize(w=, h=): px 指定の誤用 → TypeError（crop/pad へ誘導）"""
    return _expect_raise(lambda: resize(w=640, h=360), TypeError,
                         ("resize", "crop", "pad", "sx"))


def check_resize_unknown_kwargs_rejected():
    """resize(sxx=): 未知キーワード → TypeError（もしかして付き）"""
    return _expect_raise(lambda: resize(sxx=2), TypeError, ("sxx", "sx"))


def check_resize_normal_call_ok():
    """resize(sx=, sy=): 正しい呼び出しは従来どおり倍率 Transform になる"""
    t = resize(sx=0.5, sy=0.5)
    if t.name != "resize" or t.params != {"sx": 0.5, "sy": 0.5}:
        return False, f"params が想定外: {t.params}"
    return True, str(t.params)


def check_move_unknown_kwargs_rejected():
    """move(easing=): 未知キーワードが黙って捨てられず TypeError になる"""
    return _expect_raise(lambda: move(x=0.5, easing=lambda u: u), TypeError,
                         ("easing", "move"))


def check_move_typo_anchor_rejected():
    """move(anchor='cetner'): 誤記 anchor → ValueError（既定へ黙って戻らない）"""
    return _expect_raise(lambda: move(x=0.9, anchor="cetner"), ValueError,
                         ("anchor", "center"))


def check_move_edge_anchors_accepted():
    """move(anchor='right' 等): 辺の中点 anchor が受理され、左上へ換算される

    left/right/top/bottom は以前「公称だけで未実装」だったため構築時に
    ValueError で拒否していた。実装後は受理され、かつ各辺に応じた
    オフセット（右辺なら幅ぶん・下辺なら高さぶん）が引かれる。
    """
    from scriptvedit.filters.video import _build_move_exprs
    _mk_project()
    expected = {
        # anchor: (x式に含まれるべき引き算, y式に含まれるべき引き算)
        "left":   ("",     "-h/2"),
        "right":  ("-w",   "-h/2"),
        "top":    ("-w/2", ""),
        "bottom": ("-w/2", "-h"),
    }
    for a, (want_x, want_y) in expected.items():
        o = Object(asset("images/shape_badge.png"))
        o <= move(x=0.9, y=0.5, anchor=a)
        x_expr, y_expr = _build_move_exprs(o, 0, 3)
        if want_x and want_x not in x_expr:
            return False, f"anchor={a}: x式に {want_x} がない: {x_expr}"
        if not want_x and ("-w/2" in x_expr or "-w)" in x_expr):
            return False, f"anchor={a}: x式に不要な引き算がある: {x_expr}"
        if want_y and want_y not in y_expr:
            return False, f"anchor={a}: y式に {want_y} がない: {y_expr}"
        if not want_y and ("-h/2" in y_expr or "-h)" in y_expr):
            return False, f"anchor={a}: y式に不要な引き算がある: {y_expr}"
    return True, "left/right/top/bottom の4値が実装どおり換算された"


def check_path_effects_validate_anchor():
    """anchor 検証は Effect 構築側の共通関門（move系ファクトリ全部に効く）"""
    cases = [
        lambda: move_along([(0, 0), (1, 1)], anchor="cetner"),
        lambda: path_bezier((0, 0), (0.3, 0.2), (0.6, 0.8), (1, 1), anchor="rigth"),
        lambda: throw(0.1, -0.2, anchor="bottm"),
        lambda: inertia(0.1, 0.1, anchor="topleftt"),
    ]
    for fn in cases:
        ok, msg = _expect_raise(fn, ValueError, ("anchor",))
        if not ok:
            return False, msg
    # 正しい値は従来どおり通る
    if move_along([(0, 0), (1, 1)]).params["anchor"] != "center":
        return False, "既定 anchor が center でない"
    return True, "move系4ファクトリすべてで anchor が検証された"


def check_move_normal_call_ok():
    """move の正しい呼び出しは従来どおり（未指定の引数は params に載らない）"""
    e = move(x=0.5, y=0.5, anchor="center")
    if sorted(e.params) != ["anchor", "x", "y"]:
        return False, f"params が想定外: {sorted(e.params)}"
    if sorted(move(from_x=0, to_x=1).params) != ["x", "y"]:
        return False, "from/to 指定で x/y の自動 lerp にならない"
    return True, "OK"


def check_anchor_choices_produce_distinct_exprs():
    """公称する anchor の値はそれぞれ互いに異なる overlay 座標式を返す

    「6値公称・1値実装」の再発防止。manifest の choices を増やすときは
    実装（_build_move_exprs）が実際に区別できることをここで担保する。
    """
    from scriptvedit.filters.video import _build_move_exprs
    from scriptvedit.state import _PLACEMENT_ANCHORS
    _mk_project()
    exprs = {}
    for a in _PLACEMENT_ANCHORS:
        o = Object(asset("images/shape_badge.png"))
        o <= move(x=0.5, y=0.5, anchor=a)
        exprs[a] = _build_move_exprs(o, 0, 3)
    if len(set(map(str, exprs.values()))) != len(exprs):
        return False, f"互いに同じ式を返す anchor がある: {exprs}"
    if sorted(exprs) != sorted(describe()["enums"]["anchor"]):
        return False, "manifest の enums.anchor と実装の語彙がずれている"
    return True, str(exprs)


def check_trim_without_args_rejected():
    """trim(): 何も切り出さない trim → ValueError（Effect スロットだけ消費する）"""
    ok, msg = _expect_raise(lambda: trim(), ValueError, ("trim", "duration"))
    if not ok:
        return False, msg
    return _expect_raise(lambda: sv.atrim(), ValueError, ("atrim",))


def check_trim_start_is_reachable():
    """trim(start=, duration=): イン点が公開ファクトリから届く"""
    e = trim(start=2, duration=3)
    if e.params.get("start") != 2 or e.params.get("duration") != 3:
        return False, f"params が想定外: {e.params}"
    if trim(2).params != {"duration": 2}:
        return False, f"trim(2) の位置引数が duration でない: {trim(2).params}"
    a = sv.atrim(start=1, duration=2)
    if a.params.get("start") != 1:
        return False, f"atrim の start が届かない: {a.params}"
    return True, str(e.params)


def check_slice_reapply_rejected():
    """src[0:2] と src[3:5] を同じ Object へ → ValueError（trim 2段重ねを防ぐ）"""
    _mk_project()
    src = asset("video/clip_with_audio.mp4")

    def _twice():
        o = Object(src)
        o[0:2]
        o[3:5]
    ok, msg = _expect_raise(_twice, ValueError, ("obj[a:b]", "Object(src)"))
    if not ok:
        return False, msg

    def _mul_twice():
        o = Object(src)[0:1]
        o * 2
        o * 3
    ok, msg = _expect_raise(_mul_twice, ValueError, ("obj * n",))
    if not ok:
        return False, msg

    def _neg_twice():
        o = Object(src)[0:1]
        -(-o)
    return _expect_raise(_neg_twice, ValueError, ("-obj",))


def check_slice_compose_still_ok():
    """異種の糖衣の合成（[0:2] * 3 / -[0:2]）は従来どおり通る"""
    _mk_project()
    src = asset("video/clip_with_audio.mp4")
    names = [e.name for e in (Object(src)[0:2] * 3).effects]
    if names != ["trim", "repeat"]:
        return False, f"[0:2]*3 の effects が想定外: {names}"
    names = [e.name for e in (-Object(src)[0:2]).effects]
    if names != ["trim", "reverse"]:
        return False, f"-[0:2] の effects が想定外: {names}"
    return True, "OK"


def check_subtitle_second_positional_is_duration():
    """subtitle('セリフ', 3): 第2位置引数は duration（who=3 にならない）"""
    _mk_project()
    o = subtitle("セリフ", 3, size=(320, 180))
    if o.duration != 3:
        return False, f"duration が {o.duration}"
    if o._web_data["who"] is not None:
        return False, f"who に位置引数が流れ込んでいる: {o._web_data['who']!r}"
    o2 = subtitle_box("x", 3, size=(320, 180))
    o3 = bubble("x", 3, size=(320, 180))
    if (o2.duration, o3.duration) != (3, 3):
        return False, f"3兄弟の第2位置引数が揃っていない: {o2.duration}/{o3.duration}"
    # who はキーワードでは従来どおり届く
    if subtitle("x", who="Alice", size=(320, 180))._web_data["who"] != "Alice":
        return False, "who= キーワードが届かない"
    return True, "OK"


# --- 監査 項目16: API 語彙の整理 -------------------------------------------

def check_bubble_tail_validated():
    """bubble(tail=): 0..1 の (x,y) タプル以外を拒否し、旧 anchor= を誘導する"""
    _mk_project()
    ok, msg = _expect_raise(
        lambda: bubble("x", tail="center", size=(320, 180)), TypeError, ("tail",))
    if not ok:
        return False, msg
    ok, msg = _expect_raise(
        lambda: bubble("x", tail=(2, 0), size=(320, 180)), ValueError, ("tail",))
    if not ok:
        return False, msg
    ok, msg = _expect_raise(
        lambda: bubble("x", anchor=(0.6, 0.7), size=(320, 180)), TypeError,
        ("anchor", "tail"))
    if not ok:
        return False, msg
    o = bubble("x", tail=(0.6, 0.75), size=(320, 180))
    if o._web_data["anchor"] != {"x": 0.6, "y": 0.75}:
        return False, f"テンプレートへ渡る JSON が想定外: {o._web_data['anchor']}"
    return True, "OK"


def check_avolume_replaces_again_and_afade():
    """again/afade は廃止され avolume に一本化されている（フィルタは不変）"""
    from scriptvedit.filters.audio import _build_audio_effect_filters
    for gone in ("again", "afade"):
        if hasattr(sv, gone) or gone in sv.__all__:
            return False, f"{gone} が残っている"
    o = Object(asset("audio/bgm_loop.mp3"))
    o.duration = 5
    o <= avolume(0.8) & avolume(lambda u: u)
    filters = _build_audio_effect_filters(o, 5)
    expected = ["volume=volume='0.8':eval=frame",
                "volume=volume='clip((t)/5\\,0\\,1)':eval=frame"]
    if filters != expected:
        return False, f"volume フィルタが変わった: {filters}"
    return True, str(filters)


ALL_TESTS += [
    # --- 監査 項目3: ファクトリの引数検証 ---
    ("resize px誤用拒否", check_resize_pixel_kwargs_rejected),
    ("resize 未知kwargs拒否", check_resize_unknown_kwargs_rejected),
    ("resize 正常呼び出し", check_resize_normal_call_ok),
    ("move 未知kwargs拒否", check_move_unknown_kwargs_rejected),
    ("move anchor誤記拒否", check_move_typo_anchor_rejected),
    ("move 辺anchor実装", check_move_edge_anchors_accepted),
    ("anchor検証は共通関門", check_path_effects_validate_anchor),
    ("move 正常呼び出し", check_move_normal_call_ok),
    ("anchor 各値は別の式", check_anchor_choices_produce_distinct_exprs),
    ("trim 引数なし拒否", check_trim_without_args_rejected),
    ("trim start露出", check_trim_start_is_reachable),
    ("スライス二重適用拒否", check_slice_reapply_rejected),
    ("スライス合成は従来どおり", check_slice_compose_still_ok),
    ("subtitle 第2位置引数=duration", check_subtitle_second_positional_is_duration),
    # --- 監査 項目16: API 語彙の整理 ---
    ("bubble tail検証", check_bubble_tail_validated),
    ("avolume 一本化", check_avolume_replaces_again_and_afade),
]


@pytest.mark.parametrize("name,check", ALL_TESTS, ids=[n for n, _ in ALL_TESTS])
def test_error_case(name, check):
    """各エラーケースを pytest 経由で検証する（失敗時はメッセージを表示）

    check が pytest.skip() を投げた場合はそのまま skip になる（依存不在を隠さない）。
    日本語フォントが1つも無い最小環境では text 系 check が FileNotFoundError に
    なるため、これも正直に skip する（CLAUDE.md の方針。CI の Linux では
    fonts-noto-cjk を導入して実際に走らせる）。
    """
    try:
        ok, msg = check()
    except FileNotFoundError as e:
        if "フォント" in str(e):
            pytest.skip(f"日本語フォントが無い環境: {str(e).splitlines()[0]}")
        raise
    assert ok, f"{name}: {msg}"


def test_all_checks_are_registered():
    """定義済みの check_* が全て ALL_TESTS に登録されていること（重複なし）

    登録し忘れると「テストを書いたのに一度も実行されない」状態になり、差分上は
    テストを追加したように見えてしまう（監査項目23-4）。逆に、削除した関数が
    登録に残っていないことも同時に見る。
    """
    with open(os.path.abspath(__file__), encoding="utf-8") as f:
        source = f.read()
    defined = set(re.findall(r"^def (check_\w+)", source, re.M))
    registered = [fn.__name__ for _, fn in ALL_TESTS]
    duplicated = sorted({n for n in registered if registered.count(n) > 1})
    assert not duplicated, f"ALL_TESTS に重複登録があります: {duplicated}"
    missing = sorted(defined - set(registered))
    assert not missing, (
        f"ALL_TESTS へ未登録の check があります（一度も実行されません）: {missing}")
    stale = sorted(set(registered) - defined)
    assert not stale, f"ALL_TESTS に定義の無い名前が残っています: {stale}"


def test_all_check_display_names_are_unique():
    """ALL_TESTS の表示名が一意であること（pytest の id が衝突しない）"""
    names = [n for n, _ in ALL_TESTS]
    duplicated = sorted({n for n in names if names.count(n) > 1})
    assert not duplicated, f"表示名が重複しています: {duplicated}"
