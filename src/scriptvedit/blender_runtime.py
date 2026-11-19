# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ScriptVEdit contributors
#
# この Blender 内部ワーカーに限り GNU GPL version 3 またはそれ以降で配布する。
# 無保証。ライセンス全文: data/licenses/GPL-3.0.txt
# ホスト側 blender.py と既存 ScriptVEdit コアは MIT のまま。
"""Blender 内だけで実行する切り絵レンダラ。通常の Python から bpy を import しない。"""
import argparse
import json
import math
import os
import sys
from bisect import bisect_right
from fractions import Fraction


def _value(track, seconds):
    """秒のキー列を補間する。各コマの値はこの1か所で決める。"""
    keys = track["keys"]
    right = bisect_right([k[0] for k in keys], seconds)
    if right == 0:
        return keys[0][1]
    if right == len(keys) or track["interpolation"] == "step":
        return keys[right - 1][1]
    start, a = keys[right - 1]
    end, b = keys[right]
    u = (seconds - start) / (end - start)
    if track["interpolation"] == "smooth":
        u = u * u * (3 - 2 * u)
    if isinstance(a, list):
        return [x + (y - x) * u for x, y in zip(a, b)]
    return a + (b - a) * u


def _linear(value):
    return value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4


def _material(bpy, node):
    material = bpy.data.materials.new(node["id"])
    material.use_nodes = True
    tree = material.node_tree
    tree.nodes.clear()
    output = tree.nodes.new("ShaderNodeOutputMaterial")
    emission = tree.nodes.new("ShaderNodeEmission")
    transparent = tree.nodes.new("ShaderNodeBsdfTransparent")
    mix = tree.nodes.new("ShaderNodeMixShader")
    alpha = tree.nodes.new("ShaderNodeMath")
    alpha.operation = "MULTIPLY"
    alpha.inputs[1].default_value = node["opacity"]
    if "image" in node:
        texture = tree.nodes.new("ShaderNodeTexImage")
        texture.image = bpy.data.images.load(node["image"], check_existing=True)
        texture.image.alpha_mode = "STRAIGHT"
        texture.image.colorspace_settings.name = "sRGB"
        texture.interpolation = "Linear"
        tree.links.new(texture.outputs["Color"], emission.inputs["Color"])
        tree.links.new(texture.outputs["Alpha"], alpha.inputs[0])
    else:
        rgba = node["color"].lstrip("#")
        rgb = [_linear(int(rgba[i:i + 2], 16) / 255) for i in (0, 2, 4)]
        emission.inputs["Color"].default_value = (*rgb, 1)
        alpha.inputs[0].default_value = int(rgba[6:8], 16) / 255 if len(rgba) == 8 else 1
    tree.links.new(alpha.outputs[0], mix.inputs[0])
    tree.links.new(transparent.outputs[0], mix.inputs[1])
    tree.links.new(emission.outputs[0], mix.inputs[2])
    tree.links.new(mix.outputs[0], output.inputs["Surface"])
    return material, alpha.inputs[1]


def _mesh(bpy, node, parent):
    width, height = node["size"]
    px, py = node["pivot"]
    if node["shape"] == "ellipse":
        points = [(width * (.5 + .5 * math.cos(i * math.tau / 64) - px),
                   -height * (.5 + .5 * math.sin(i * math.tau / 64) - py), 0)
                  for i in range(64)]
    else:
        points = [(-width * px, height * py, 0),
                  (width * (1 - px), height * py, 0),
                  (width * (1 - px), -height * (1 - py), 0),
                  (-width * px, -height * (1 - py), 0)]
    mesh = bpy.data.meshes.new(node["id"] + "_mesh")
    # 時計回りの画面座標を、カメラへ向く面へ反転する。
    mesh.from_pydata(points, [], [tuple(reversed(range(len(points))))])
    mesh.update()
    uv = mesh.uv_layers.new(name="UVMap")
    for polygon in mesh.polygons:
        for loop in polygon.loop_indices:
            vertex = mesh.vertices[mesh.loops[loop].vertex_index].co
            uv.data[loop].uv = (vertex.x / width + px, vertex.y / height + 1 - py)
    obj = bpy.data.objects.new(node["id"] + "_art", mesh)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    material, alpha = _material(bpy, node)
    obj.data.materials.append(material)
    return alpha


def _set_value(obj, alpha, prop, value, frame):
    if prop == "position":
        obj.location.x, obj.location.y = value[0], -value[1]
        obj.keyframe_insert(data_path="location", frame=frame)
    elif prop == "rotation":
        obj.rotation_euler.z = -math.radians(value)
        obj.keyframe_insert(data_path="rotation_euler", frame=frame)
    elif prop == "scale":
        obj.scale.x, obj.scale.y = value
        obj.keyframe_insert(data_path="scale", frame=frame)
    elif alpha is not None:
        alpha.default_value = value
        alpha.keyframe_insert(data_path="default_value", frame=frame)


def _render(job, output):
    import bpy

    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = job["samples"]
    scene.cycles.seed = job["seed"]
    scene.cycles.use_animated_seed = False
    scene.cycles.use_denoising = False
    scene.cycles.use_adaptive_sampling = False
    scene.cycles.transparent_max_bounces = max(32, len(job["rig"]["nodes"]) + 1)
    scene.render.film_transparent = True
    scene.render.dither_intensity = 0
    scene.render.use_persistent_data = True
    scene.render.resolution_x, scene.render.resolution_y = job["size"]
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.color_depth = "8"
    scene.render.image_settings.compression = 20
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.view_settings.exposure = 0
    scene.view_settings.gamma = 1
    scene.render.use_file_extension = True
    scene.render.filepath = os.path.join(output, "######")
    scene.frame_start, scene.frame_end = 1, job["n_frames"]
    fps = Fraction(job["fps"])
    scene.render.fps = round(float(fps))
    scene.render.fps_base = scene.render.fps / float(fps)
    width, height = job["rig"]["canvas"]
    camera = bpy.data.cameras.new("PuppetCamera")
    camera.type = "ORTHO"
    camera.sensor_fit = "HORIZONTAL"
    # 異なる出力比率でも canvas 全体を収め、余白は透明にする。
    aspect = job["size"][0] / job["size"][1]
    camera.ortho_scale = max(width, height * aspect)
    camera.clip_end = 100000
    cam = bpy.data.objects.new("PuppetCamera", camera)
    bpy.context.collection.objects.link(cam)
    cam.location = (width / 2, -height / 2, 10000)
    scene.camera = cam
    objects, alphas = {}, {}
    for i, node in enumerate(job["rig"]["nodes"]):
        obj = bpy.data.objects.new(node["id"], None)
        bpy.context.collection.objects.link(obj)
        obj.location = (node["position"][0], -node["position"][1], node["z"] + i * .00001)
        obj.rotation_euler.z = -math.radians(node["rotation"])
        obj.scale = (*node["scale"], 1)
        objects[node["id"]] = obj
        alphas[node["id"]] = _mesh(bpy, node, obj) if "image" in node or "color" in node else None
    for node in job["rig"]["nodes"]:
        if node["parent"] is not None:
            objects[node["id"]].parent = objects[node["parent"]]
    opacity_tracks = {}
    for track in job["motion"]["tracks"]:
        if track["property"] == "opacity":
            opacity_tracks[track["node"]] = track
            continue
        for frame in range(1, job["n_frames"] + 1):
            value = _value(track, float(Fraction(frame - 1) / fps))
            _set_value(objects[track["node"]], alphas[track["node"]], track["property"], value, frame)
    nodes = {node["id"]: node for node in job["rig"]["nodes"]}
    # 親グループの透明度も子へ継承する。画像本来のアルファとはシェーダで乗算する。
    for name, alpha in alphas.items():
        if alpha is None:
            continue
        for frame in range(1, job["n_frames"] + 1):
            seconds = float(Fraction(frame - 1) / fps)
            current, value = name, 1.0
            while current is not None:
                node = nodes[current]
                value *= (_value(opacity_tracks[current], seconds) if current in opacity_tracks
                          else node["opacity"])
                current = node["parent"]
            _set_value(objects[name], alpha, "opacity", value, frame)
    bpy.ops.render.render(animation=True)


def main():
    parser = argparse.ArgumentParser(description="ScriptVEdit Blender puppet worker")
    parser.add_argument("--job", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    with open(args.job, encoding="utf-8") as source:
        job = json.load(source)
    _render(job, args.output)


if __name__ == "__main__":
    main()
