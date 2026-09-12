# -*- coding: utf-8 -*-
"""几何比例变体的 SVG 自检(复用 ../icons/check_svg.py 的独立光栅化实现)。"""
import os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import build_golden as S
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "icons"))
import check_svg as base

E = base.bi
E.NAMES, E.ICON_ORDER, E.ROT180 = S.NAMES, S.ORDER, {"dislike"}
E.PAL = {n: ("#ffffff", "#ffffff") for n in S.ORDER}
E.shapes_for = lambda n: S.export_shapes(n)     # 与构建使用同一套(已配形)几何
sys.exit(base.main(icon_dir=HERE, engine=E))
