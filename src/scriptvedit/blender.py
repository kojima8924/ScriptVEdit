# -*- coding: utf-8 -*-
"""Blender を別プロセスで呼ぶ、JSON 駆動の切り絵アニメーション。

bpy はこのモジュールには import しない。Blender が無い環境でも通常の
ScriptVEdit、マニフェストと dry_run は使える。実生成だけ外部 Blender が要る。
"""
import copy
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from scriptvedit.cache import _file_fingerprint, _sig_key
from scriptvedit.context import current_project
from scriptvedit.ffmpeg import _atomic_write_text, _run_ffmpeg, _unique_tmp_path
from scriptvedit.filters.video import _fps_fraction
from scriptvedit.state import _ARTIFACT_DIR, _ENGINE_VER
from scriptvedit.stillseq import (
    _commit_verified, _finalize_hold_object, _fps_text, _resolve_fps,
    _resolve_size, _round_frame, _sec_fraction,
)

_PUPPET_VERSION = "1"
_RUNTIME = Path(__file__).with_name("blender_runtime.py")
_NODE_FIELDS = {"id", "parent", "position", "pivot", "size", "image", "color",
                "shape", "rotation", "scale", "opacity", "z"}
_PROPERTIES = {"position", "rotation", "scale", "opacity"}


def _number(value, field, minimum=None, maximum=None):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or (minimum is not None and value < minimum)
            or (maximum is not None and value > maximum)):
        raise ValueError(f"puppet_clip: {field} の数値が不正です: {value!r}")
    return float(value)


def _pair(value, field, minimum=None, maximum=None):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"puppet_clip: {field} は数値2つの配列にしてください")
    return [_number(v, field, minimum, maximum) for v in value]


def _document(value, field):
    if isinstance(value, (str, os.PathLike)):
        path = Path(value).resolve()
        with path.open(encoding="utf-8-sig") as source:
            obj = json.load(source)
        return obj, path.parent, [str(path)]
    if not isinstance(value, dict):
        raise TypeError(f"puppet_clip: {field} は JSON ファイルか dict にしてください")
    return copy.deepcopy(value), Path.cwd(), []


def _check_fields(obj, allowed, field):
    if not isinstance(obj, dict):
        raise ValueError(f"puppet_clip: {field} は object にしてください")
    extra = set(obj) - allowed
    if extra:
        raise ValueError(f"puppet_clip: {field} に未対応のキーがあります: {sorted(extra)}")


def _normalize_rig(rig):
    data, base, deps = _document(rig, "rig")
    _check_fields(data, {"schema", "canvas", "nodes"}, "rig")
    if data.get("schema", 1) != 1:
        raise ValueError("puppet_clip: rig.schema は 1 にしてください")
    canvas = list(_resolve_size("puppet_clip", data.get("canvas", [1280, 720])))
    nodes = data.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise ValueError("puppet_clip: rig.nodes は空でない配列にしてください")
    normalized, ids = [], set()
    for i, node in enumerate(nodes):
        field = f"nodes[{i}]"
        _check_fields(node, _NODE_FIELDS, field)
        name = node.get("id")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]*", name):
            raise ValueError(f"puppet_clip: {field}.id は英数字・_・- の識別子にしてください")
        if name in ids:
            raise ValueError(f"puppet_clip: node.id が重複しています: {name}")
        ids.add(name)
        n = {
            "id": name,
            "parent": node.get("parent"),
            "position": _pair(node.get("position", [0, 0]), field + ".position"),
            "pivot": _pair(node.get("pivot", [.5, .5]), field + ".pivot", 0, 1),
            "size": _pair(node.get("size", [100, 100]), field + ".size", .000001),
            "rotation": _number(node.get("rotation", 0), field + ".rotation"),
            "scale": _pair(node.get("scale", [1, 1]), field + ".scale"),
            "opacity": _number(node.get("opacity", 1), field + ".opacity", 0, 1),
            "z": _number(node.get("z", 0), field + ".z"),
        }
        if n["parent"] is not None and not isinstance(n["parent"], str):
            raise ValueError(f"puppet_clip: {field}.parent は node.id にしてください")
        if "image" in node and "color" in node:
            raise ValueError(f"puppet_clip: {field} の image と color はどちらか一方です")
        if "image" in node:
            if not isinstance(node["image"], str) or not node["image"]:
                raise ValueError(f"puppet_clip: {field}.image は画像パスにしてください")
            path = (base / node["image"]).resolve()
            if not path.is_file():
                raise FileNotFoundError(f"puppet_clip: 画像が見つかりません: {path}")
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".tga", ".bmp"}:
                raise ValueError(f"puppet_clip: image はラスター画像にしてください: {path}")
            n["image"] = str(path)
            deps.append(str(path))
        elif "color" in node:
            color = node["color"]
            if not isinstance(color, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", color):
                raise ValueError(f"puppet_clip: {field}.color は #RRGGBB か #RRGGBBAA です")
            n["color"] = color.lower()
        shape = node.get("shape", "rect")
        if shape not in ("rect", "ellipse"):
            raise ValueError(f"puppet_clip: {field}.shape は rect / ellipse です")
        if "image" in n and shape != "rect":
            raise ValueError("puppet_clip: 画像ノードは shape=rect を使用します")
        n["shape"] = shape
        normalized.append(n)
    parents = {n["id"]: n["parent"] for n in normalized}
    for name in ids:
        chain = set()
        while name is not None:
            if name not in parents:
                raise ValueError(f"puppet_clip: 存在しない parent です: {name}")
            if name in chain:
                raise ValueError(f"puppet_clip: parent が循環しています: {name}")
            chain.add(name)
            name = parents[name]
    return {"schema": 1, "canvas": canvas, "nodes": normalized}, deps


def _normalize_motion(motion, ids, duration):
    if motion is None:
        data, deps = {}, []
    else:
        data, _base, deps = _document(motion, "motion")
    _check_fields(data, {"schema", "duration", "tracks"}, "motion")
    if data.get("schema", 1) != 1:
        raise ValueError("puppet_clip: motion.schema は 1 にしてください")
    if duration is None:
        duration = data.get("duration")
    duration = _number(duration, "duration", .000001)
    tracks = data.get("tracks", [])
    if not isinstance(tracks, list):
        raise ValueError("puppet_clip: motion.tracks は配列にしてください")
    normalized, seen = [], set()
    for i, track in enumerate(tracks):
        field = f"tracks[{i}]"
        _check_fields(track, {"node", "property", "keys", "interpolation"}, field)
        node, prop = track.get("node"), track.get("property")
        if node not in ids or prop not in _PROPERTIES:
            raise ValueError(f"puppet_clip: {field} の node / property が不正です")
        if (node, prop) in seen:
            raise ValueError(f"puppet_clip: 同一プロパティの track が重複しています: {node}.{prop}")
        seen.add((node, prop))
        mode = track.get("interpolation", "smooth")
        if mode not in ("linear", "smooth", "step"):
            raise ValueError(f"puppet_clip: {field}.interpolation は linear / smooth / step です")
        keys = track.get("keys")
        if not isinstance(keys, list) or not keys:
            raise ValueError(f"puppet_clip: {field}.keys は空でない配列にしてください")
        output, previous = [], -1.0
        for key in keys:
            if not isinstance(key, (list, tuple)) or len(key) != 2:
                raise ValueError(f"puppet_clip: {field}.keys は [秒, 値] の配列です")
            when = _number(key[0], field + ".keys.time", 0)
            if when <= previous:
                raise ValueError(f"puppet_clip: {field}.keys の秒は重複しない昇順にしてください")
            previous = when
            value = (_pair(key[1], field + ".keys.value") if prop in ("position", "scale")
                     else _number(key[1], field + ".keys.value", 0 if prop == "opacity" else None,
                                  1 if prop == "opacity" else None))
            output.append([when, value])
        normalized.append({"node": node, "property": prop, "keys": output,
                           "interpolation": mode})
    return {"tracks": normalized}, duration, deps


def _renderer_identity(executable, renderer_id):
    """dry_run で Blender を起動せず、実行ファイルの内容で世代を区別する。"""
    if not isinstance(executable, (str, os.PathLike)) or not os.fspath(executable):
        raise ValueError("puppet_clip: blender は実行ファイルのパスにしてください")
    if renderer_id is not None and not isinstance(renderer_id, str):
        raise ValueError("puppet_clip: renderer_id は文字列にしてください")
    resolved = shutil.which(os.fspath(executable))
    identity = _file_fingerprint(resolved) if resolved else "unavailable"
    return resolved, {"binary": identity, "build": renderer_id or ""}


def _render_batch(job, executable, cache_path, encode_cmd, timeout):
    if executable is None:
        raise FileNotFoundError(
            "puppet_clip: Blender が見つかりません。Blender をインストールして PATH に追加するか、"
            "blender= に実行ファイルを指定してください。通常の ScriptVEdit と dry_run は使用できます。")
    out_dir = os.path.dirname(cache_path)
    tmp_path = _unique_tmp_path(cache_path)
    try:
        with tempfile.TemporaryDirectory(prefix="puppet_", dir=out_dir) as work:
            job_path = os.path.join(work, "job.json")
            frames_dir = os.path.join(work, "frames")
            os.mkdir(frames_dir)
            _atomic_write_text(job_path, json.dumps(job, ensure_ascii=False, sort_keys=True))
            log_path = os.path.join(work, "blender.log")
            cmd = [executable, "--background", "--factory-startup", "--disable-autoexec",
                   "--python-exit-code", "1", "--python", str(_RUNTIME), "--",
                   "--job", job_path, "--output", frames_dir]
            with open(log_path, "wb") as log:
                try:
                    result = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=log,
                                            stderr=subprocess.STDOUT, timeout=timeout, check=False)
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError(f"puppet_clip: Blender が {timeout} 秒で終了しませんでした") from exc
            if result.returncode:
                with open(log_path, "rb") as log:
                    log.seek(max(0, os.path.getsize(log_path) - 16000))
                    tail = log.read().decode("utf-8", "replace")
                raise RuntimeError(f"puppet_clip: Blender が失敗しました（終了コード {result.returncode}）\n{tail}")
            expected = [os.path.join(frames_dir, f"{i:06d}.png") for i in range(1, job["n_frames"] + 1)]
            if any(not os.path.isfile(p) or os.path.getsize(p) < 24 for p in expected):
                raise RuntimeError("puppet_clip: Blender の出力フレームが不足しています。キャッシュには書きません")
            run_cmd = [tmp_path if item == cache_path else item for item in encode_cmd]
            run_cmd[run_cmd.index("-i") + 1] = os.path.join(frames_dir, "%06d.png")
            _run_ffmpeg(run_cmd, timeout=timeout, context="Blender puppet の RGBA 符号化", echo=False)
            _commit_verified(tmp_path, cache_path, job["n_frames"], "Blender puppet")
    finally:
        try:
            os.remove(tmp_path)
        except FileNotFoundError:
            pass


def puppet_clip(rig, motion=None, *, duration=None, fps=None, size=None,
                blender="blender", renderer_id=None, samples=8, seed=0, timeout=600):
    """Blender で切り絵リグを動かし、透過動画の Object を返す。

    rig: schema=1、canvas=[幅,高さ]、nodes の JSON ファイルか dict。
      画像パスは rig JSON の親ディレクトリ基準（dict は現在のディレクトリ基準）。
      ノードは id / parent / position / pivot / size / image または color / shape /
      rotation / scale / opacity / z。親子の移動・回転・拡大縮小を継承する。
    motion: duration と tracks の JSON ファイルか dict。track は node / property /
      keys=[[秒,値],...] / interpolation。property は position / rotation / scale /
      opacity、補間は smooth / linear / step。キーの外側は端の値を保持する。
    duration: 秒。省略時は motion.duration。最も近いフレーム数へ丸める。
    fps: 省略時は Project の fps。size: 出力寸法。省略時は rig.canvas。
    blender: 外部 Blender 実行ファイル。bpy を通常の Python には import しない。
    renderer_id: 配布ビルド・共有ライブラリ・OCIO 等の追加識別子。実行ファイルの
      内容指紋は常に鍵へ含むため、版が異なるバイナリのキャッシュは混ざらない。
    samples: Cycles のサンプル数。CPU / Standard sRGB / RGBA で固定する。
    seed: レンダの乱数シード。timeout: Blender と FFmpeg それぞれの上限秒。

    1素材につき Blender を1回だけ起動し、全コマを生成して可逆 qtrle MOV にまとめる。
    time() で尺を伸ばすと最後の絵を保持する。音声は無い。キャッシュはリグ・動き・
    画像内容・レンダ実装・Blender バイナリ・寸法・fps・samples・seed で決まる。
    dry_run では Blender 起動もファイル生成もせず、2段の予定コマンドを返す。
    """
    rig_data, deps = _normalize_rig(rig)
    motion_data, duration, motion_deps = _normalize_motion(
        motion, {n["id"] for n in rig_data["nodes"]}, duration)
    deps.extend(motion_deps)
    fps_frac = _fps_fraction(_resolve_fps("puppet_clip", fps))
    n_frames = max(1, _round_frame(_sec_fraction(duration), fps_frac))
    size = _resolve_size("puppet_clip", size if size is not None else rig_data["canvas"])
    if any(value < 4 or value > 65536 for value in size):
        raise ValueError("puppet_clip: size の各辺は Blender の制限に合わせて 4〜65536 px にしてください")
    for name, value, lo, hi in (("samples", samples, 1, 4096), ("seed", seed, 0, 2147483647)):
        if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
            raise ValueError(f"puppet_clip: {name} は {lo}〜{hi} の整数にしてください")
    timeout = _number(timeout, "timeout", .001)
    executable, identity = _renderer_identity(blender, renderer_id)
    job = {"rig": rig_data, "motion": motion_data, "n_frames": n_frames,
           "fps": _fps_text(fps_frac), "size": list(size), "samples": samples, "seed": seed}
    signature = copy.deepcopy(job)
    for node in signature["rig"]["nodes"]:
        if "image" in node:
            node["image"] = _file_fingerprint(node["image"])
    # ファイルの置き場所・JSON の書式・timeout は描画結果を変えないので鍵に入れない。
    key = _sig_key(["blender_puppet", _PUPPET_VERSION, _ENGINE_VER,
                    json.dumps(signature, sort_keys=True, separators=(",", ":")),
                    json.dumps(identity, sort_keys=True),
                    _file_fingerprint(__file__), _file_fingerprint(_RUNTIME)])
    cache_path = os.path.join(_ARTIFACT_DIR, "blender", key + ".mov")
    work = os.path.join("__cache__", "work", "blender", key)
    pattern = os.path.join(work, "frames", "%06d.png")
    blender_cmd = [os.fspath(blender), "--background", "--factory-startup", "--disable-autoexec",
                   "--python-exit-code", "1", "--python", str(_RUNTIME), "--",
                   "--job", os.path.join(work, "job.json"), "--output", os.path.join(work, "frames")]
    cmd = ["ffmpeg", "-y", "-framerate", _fps_text(fps_frac), "-start_number", "1",
           "-i", pattern, "-vf", "format=argb", "-frames:v", str(n_frames),
           "-c:v", "qtrle", "-g", "1000000", "-an", cache_path]
    proj = current_project()
    if proj is not None and getattr(proj, "_dry_run", False) and getattr(proj, "_mode", None) != "plan":
        proj._pending_compute_cmds[pattern] = blender_cmd
    deps.extend([str(_RUNTIME), __file__])
    obj = _finalize_hold_object(
        cache_path, cmd, deps, n_frames, fps_frac,
        lambda: _render_batch(job, executable, cache_path, cmd, timeout))
    obj._puppet = {"schema": 1, "frames": n_frames, "fps": _fps_text(fps_frac),
                   "size": list(size), "renderer": identity, "cache_key": key}
    return obj
