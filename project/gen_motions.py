# -*- coding: utf-8 -*-
"""生成大幅可见的 Live2D 动作文件"""
import json, os

MOTIONS_DIR = 'static/models/my_teacher/motions'

def linear_curve(curve_id, points):
    """points: [(t0,v0), (t1,v1), ...] → 全线性插值曲线"""
    segs = [points[0][0], points[0][1]]
    for t, v in points[1:]:
        segs += [0, t, v]
    return {"Target": "Parameter", "Id": curve_id, "Segments": segs}

def build_motion(curves, duration, loop=False):
    total_points = 0
    total_segments = 0
    for c in curves:
        seg = c['Segments']
        # 起点 2 值 + 每段 3 值（Linear）
        total_points += 1 + (len(seg) - 2) // 3
        total_segments += (len(seg) - 2) // 3
    return {
        "Version": 3,
        "Meta": {
            "Duration": duration,
            "Fps": 30.0,
            "Loop": loop,
            "AreBeziersRestricted": True,
            "CurveCount": len(curves),
            "TotalSegmentCount": total_segments,
            "TotalPointCount": total_points,
            "UserDataCount": 0,
            "TotalUserDataSize": 0
        },
        "Curves": curves
    }

# ============ hello：大幅挥手打招呼（3.5s） ============
hello_curves = [
    # 头部左右大幅摆动（挥手感）
    linear_curve('ParamAngleX', [(0,0), (0.4,25), (0.9,-25), (1.4,25), (1.9,-25), (2.4,25), (2.9,-15), (3.5,0)]),
    # 身体左右倾斜配合
    linear_curve('ParamBodyAngleX', [(0,0), (0.4,10), (0.9,-10), (1.4,10), (1.9,-10), (2.4,10), (2.9,-6), (3.5,0)]),
    # 头发飘动
    linear_curve('ParamShotHair', [(0,0), (0.4,6), (0.9,-6), (1.4,6), (1.9,-6), (2.4,6), (2.9,-4), (3.5,0)]),
    # 微笑
    linear_curve('ParamMouthSmile', [(0,0.5), (0.3,6), (3.0,6), (3.5,0.5)]),
    # 睁眼
    linear_curve('ParamEyeLOpen', [(0,1), (3.5,1)]),
    linear_curve('ParamEyeROpen', [(0,1), (3.5,1)]),
]

# ============ think：大幅思考动作（3s） ============
think_curves = [
    # 低头沉思 + 侧头
    linear_curve('ParamAngleY', [(0,0), (0.5,-18), (1.0,-25), (2.0,-22), (2.5,-10), (3,0)]),
    linear_curve('ParamAngleX', [(0,0), (0.6,12), (1.2,18), (2.2,14), (2.8,5), (3,0)]),
    # 身体倾斜
    linear_curve('ParamBodyAngleX', [(0,0), (0.6,8), (1.2,12), (2.2,10), (2.8,4), (3,0)]),
    # 眼球转动（思考）
    linear_curve('ParamEyeBallX', [(0,0), (0.6,4), (1.2,6), (2.2,4), (2.8,2), (3,0)]),
    linear_curve('ParamEyeBallY', [(0,0), (0.6,3), (1.2,4), (2.2,3), (2.8,1), (3,0)]),
    # 皱眉思考
    linear_curve('ParamBrowLAngle', [(0,0), (0.5,6), (2.5,6), (3,0)]),
    linear_curve('ParamBrowRAngle', [(0,0), (0.5,6), (2.5,6), (3,0)]),
    # 睁眼
    linear_curve('ParamEyeLOpen', [(0,1), (3,1)]),
    linear_curve('ParamEyeROpen', [(0,1), (3,1)]),
]

# ============ speak：说话 + 拉黑板（3s，嘴巴开合3次） ============
speak_curves = [
    # 嘴巴开合（说话）
    linear_curve('ParamJawOpen', [(0,0), (0.3,8), (0.6,0), (0.9,8), (1.2,0), (1.5,8), (1.8,0), (2.1,8), (2.4,0), (2.7,4), (3,0)]),
    # 微笑
    linear_curve('ParamMouthSmile', [(0,2), (3,2)]),
    # 头部轻微摆动
    linear_curve('ParamAngleX', [(0,0), (0.8,6), (1.6,-6), (2.4,4), (3,0)]),
    # 身体配合
    linear_curve('ParamBodyAngleX', [(0,0), (0.8,4), (1.6,-4), (2.4,3), (3,0)]),
    # 睁眼
    linear_curve('ParamEyeLOpen', [(0,1), (3,1)]),
    linear_curve('ParamEyeROpen', [(0,1), (3,1)]),
]

motions = {
    'hello.motion3.json': build_motion(hello_curves, 3.5, loop=False),
    'Think_01.motion3.json': build_motion(think_curves, 3.0, loop=False),
    'Speak_01.motion3.json': build_motion(speak_curves, 3.0, loop=False),
}

# 备份原文件
backup_dir = os.path.join(MOTIONS_DIR, '_backup')
os.makedirs(backup_dir, exist_ok=True)
for fname in motions:
    src = os.path.join(MOTIONS_DIR, fname)
    if os.path.exists(src):
        dst = os.path.join(backup_dir, fname + '.bak')
        if not os.path.exists(dst):
            os.replace(src, dst)
            print(f'备份: {fname} → _backup/')

# 写入新动作
for fname, motion in motions.items():
    path = os.path.join(MOTIONS_DIR, fname)
    json.dump(motion, open(path, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
    print(f'生成: {fname} ({len(json.dumps(motion))}B)')

print('\n✅ 大幅动作已生成')
