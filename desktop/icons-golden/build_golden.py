# -*- coding: utf-8 -*-
"""几何比例变体 (黄金/斐波那契规格) —— 纯色剪影, 所有尺寸来自同一套数学规格。

规格 (96 网格, 中心 48):
  度量序列(Fibonacci): 3, 5, 8, 13, 21, 34, 55
  光学 keyline:  圆 d=68 · 方 60 · 竖矩 52x68 · 横矩 68x52   (圆形比方形外扩 13%, 视觉等重)
  线宽/缝隙:     主 8 · 细 5 · 发丝 3
  圆角:          大 21 · 中 13 · 小 8 · 细 5
  角度:          仅用 45° / 60° / 120°
  光学补偿:      圆与尖端外扩 1.5 单位(避免视觉缩水)
引擎复用 ../icons/build_icons.py。
"""
import math, os, sys
from PIL import Image, ImageDraw, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "icons"))
import build_icons as E

WHITE, HOLE = E.WHITE, E.HOLE()

# 每个图标的 bbox 目标(keyline 系统): 圆 d68 / 方 60 / 横矩 68x52 —— 构造后自动配形+居中
KEYLINE_FIT = {"play": (58.0, 58.0), "next": (68.0, 68.0), "read": (66.0, 48.0),
               "like": (58.0, 58.0), "dislike": (58.0, 58.0), "block": (60.0, 60.0)}
_FIT_CACHE, _FIT_SCALE = {}, {}

def export_shapes(name):
    """对外(构建/自检共用)的几何: 已按 keyline 配形并居中"""
    if name not in _FIT_CACHE:
        shp, sc = fit_to_keyline(name)
        _FIT_CACHE[name] = shp; _FIT_SCALE[name] = sc
    return _FIT_CACHE[name]

def translate(shapes, dx, dy):
    out=[]
    for sh in shapes:
        n = dict(sh)
        if n["k"] == "rrect": n["x"] += dx; n["y"] += dy
        elif n["k"] == "circ": n["cx"] += dx; n["cy"] += dy
        elif n["k"] in ("poly","stroke"):
            n["pts"] = [(p[0]+dx, p[1]+dy) for p in n["pts"]]
            if n.get("arc"):
                a = n["arc"]; n["arc"] = (a[0]+dx, a[1]+dy, a[2], a[3], a[4])
        if n.get("rot"):
            a, rx, ry = n["rot"]; n["rot"] = (a, rx+dx, ry+dy)
        out.append(n)
    return out

def fit_to_keyline(name):
    """渲染 -> 量 bbox -> 等比缩放到 keyline 目标 -> 平移到画布中心。返回 (shapes, scale)"""
    shp = SHAPES[name]()
    old = E.shapes_for
    E.shapes_for = lambda _n: shp
    try:
        bb = E.render(name).getchannel("A").getbbox()
    finally:
        E.shapes_for = old
    x0, y0, x1, y1 = [v/8.0 for v in bb]
    tw, th = KEYLINE_FIT[name]
    s = min(tw/max(1e-6, x1-x0), th/max(1e-6, y1-y0))
    cx, cy = (x0+x1)/2.0, (y0+y1)/2.0
    scaled = E.xform(shp, s)
    return translate(scaled, -(cx-48)*s, -(cy-48)*s), s
NAMES = {"play":"播放", "next":"换一条", "read":"阅读", "like":"喜欢", "dislike":"不喜欢", "block":"屏蔽"}
ORDER = ["play","next","read","like","dislike","block"]

# ---------------------------------------------------------------- 规格常量
S3, S5, S8, S13, S21, S34, S55 = 3, 5, 8, 13, 21, 34, 55
STROKE, GAP, HAIR = S8, S5, S3
R_L, R_M, R_S, R_XS = S21, S13, S8, S5
K_CIRCLE = 34.0          # 圆 keyline 半径
K_SQUARE = 30.0          # 方 keyline 半边长
OVERSHOOT = 1.5          # 光学外扩
SQRT3_2 = math.sqrt(3)/2

# ---------------------------------------------------------------- 六个图标
def ico_play():
    """等边三角形(内角 60°): 边长 66, 高 = 66*√3/2 = 57.2, 圆角 13, 尖端外扩 1.5"""
    side = 66.0
    h = side * SQRT3_2                       # 57.2 -> 竖矩 keyline 52x68 内
    x0 = 48 - h/2 - 1.0                      # 微左移 1: 三角视觉重心偏左
    y0, y1 = 48 - side/2, 48 + side/2
    tip = (x0 + h + OVERSHOOT, 48)
    return [E.poly([(x0, y0), tip, (x0, y1)], R_S, WHITE)]

def ico_next():
    """环形箭头: 两段 120° 弧(半径 26) + 两处 60° 缺口 + 60° 楔形箭头(13x13)"""
    R = 29.0
    sp = []
    for a0, a1 in ((205, 325), (25, 145)):
        sp.append((E.arc_pts(48,48,R,a0,a1,step=2.0), a1))
    sh = []
    for pts, a_end in sp:
        sh.append(E.stroke(pts, 10.0, WHITE, arc=(48,48,R,a_end-120,a_end)))
    th = math.radians(325)
    for ang, sgn in ((325, 1), (145, 1)):
        t = math.radians(ang)
        p = (48+R*math.cos(t), 48+R*math.sin(t))
        tg = (-math.sin(t), math.cos(t)); nr = (math.cos(t), math.sin(t))
        tip  = (p[0]+tg[0]*16, p[1]+tg[1]*16)
        b    = (p[0]-tg[0]*S3,  p[1]-tg[1]*S3)
        sh.append(E.poly([tip, (b[0]+nr[0]*12.5, b[1]+nr[1]*12.5), (b[0]-nr[0]*12.5, b[1]-nr[1]*12.5)], S3, WHITE))
    return sh

def ico_read():
    """摊开的书: 书脊 8(缝 3) + 两页 26x40, 页外缘比脊缘高 4 -> 一眼不是"暂停"键
       总宽 26+3+8+3+26 = 66 (≈横矩 keyline 68)"""
    spine_w, spine_h = S8, 48.0
    page_w, page_h, page_h2 = 24.0, 44.0, 40.0
    gap = S5
    sh = [E.rrect(48-spine_w/2, 48-spine_h/2, spine_w, spine_h, R_XS, WHITE)]
    for sgn in (-1, 1):
        x_sp  = 48 + sgn*(spine_w/2 + gap)
        x_out = x_sp + sgn*page_w
        sh.append(E.poly([(x_out, 48-page_h/2), (x_sp, 48-page_h2/2),
                          (x_sp, 48+page_h2/2), (x_out, 48+page_h/2)], R_XS, WHITE))
    return sh

def ico_like():
    """大拇指: 袖口 13x34(缝 5) + 掌 47x34 + 拇指高 28/根宽 22, 虎口 60° V 角(r=5)"""
    pts = [(32,79),        # 掌左下
           (32,50),        # 掌左缘
           (34,45),        # 拇指根外缘
           (36,21),        # 拇指左缘
           (45,14),        # 拇指尖(外扩)
           (53,21),        # 拇指右缘
           (54,45),        # 虎口(凹顶点, 更深)
           (76,45),        # 掌上缘
           (76,79)]        # 掌右下 (45..79 = 34 Fibonacci)
    rad = [R_M, R_S, R_S, R_S, R_M, R_S, R_XS, R_S, R_M]
    cuff = E.rrect(14, 45, S13, S34, R_XS, WHITE)
    return [E.poly(pts, rad, WHITE), cuff]        # 尺寸交给 fit_to_keyline

def ico_block():
    """禁止: 正圆 d=68(keyline) + 45° 斜杠(宽 8, 贯通)"""
    body = E.circ(48, 48, K_SQUARE, WHITE)      # 用方 keyline(60)配平墨量: 实心圆天然比线性图标重
    bar  = E.stroke([(16,16),(80,80)], STROKE, HOLE)
    return [body, bar]

SHAPES = {"play":ico_play, "next":ico_next, "read":ico_read, "like":ico_like, "dislike":ico_like, "block":ico_block}

# ---------------------------------------------------------------- 输出
def main():
    E.NAMES, E.ICON_ORDER, E.ROT180, E.HERE = NAMES, ORDER, {"dislike"}, HERE
    E.PAL = {n: ("#ffffff","#ffffff") for n in ORDER}
    os.makedirs(os.path.join(HERE,"png"), exist_ok=True)
    masters={}; ink={}; scales={}
    for n in ORDER:
        shp = export_shapes(n); scales[n] = _FIT_SCALE[n]
        E.shapes_for = (lambda s: (lambda _n: s))(shp)
        m = E.render(n); masters[n]=m
        a = m.getchannel("A").histogram()
        ink[n] = sum(i*c for i,c in enumerate(a))/255.0/(8*8)      # 折算回 96 网格的墨量(px^2)
        open(os.path.join(HERE, n+".svg"),"w",encoding="utf-8").write(E.svg_icon(n))
        _ = masters[n]
        for s in (24,48,96,192):
            m.resize((s,s), Image.LANCZOS).save(os.path.join(HERE,"png","%s-%d.png" % (n,s)))
    open(os.path.join(HERE,"icons.svg"),"w",encoding="utf-8").write(E.svg_sprite())
    build_preview(masters, ink)
    print("golden icons ->", HERE)
    print("  %-9s %8s  %-16s %s" % ("icon","ink px^2","bbox(96网格)","keyline"))
    for n in ORDER:
        bb = masters[n].getchannel("A").getbbox()
        x0,y0,x1,y1 = [round(v/8.0,1) for v in bb]
        ok = (x0 >= 13.5 and y0 >= 13.5 and x1 <= 82.5 and y1 <= 82.5)
        print("  %-9s %8.1f  (%4.1f,%4.1f)-(%4.1f,%4.1f)  %s  fit x%.3f" % (n, ink[n], x0,y0,x1,y1, "keyline内 OK" if ok else "超出!", scales[n]))

def build_preview(masters, ink):
    W=1000; SIZES=[96,48,32,24,18]
    avg = sum(ink.values())/len(ink)
    img=Image.new("RGB",(W,2600), E.hx("#12151b")); d=ImageDraw.Draw(img)
    d.text((28,22),"Mirror路Sorter · 几何比例图标 (纯色 / 斐波那契规格)", font=E.font(24,True), fill=E.hx("#e8eaf0"))
    d.text((28,56),"度量 3·5·8·13·21·34·55  |  keyline 圆68·方60·竖矩52x68  |  角度 45°/60°/120°  |  圆与尖端外扩 1.5", font=E.font(13,True), fill=E.hx("#8a91a0"))
    d.text((28,76),"墨量(96 网格内的实心面积, 平均 %.0f): "%avg + "   ".join("%s %.0f"%(NAMES[n],ink[n]) for n in ORDER), font=E.font(11,True), fill=E.hx("#5d6675"))
    y=104
    for theme,bg,fg,lab in [("dark","#1a1d24","#e8eaf0","深色卡片 (--card #1a1d24)"),("light","#ffffff","#20242f","浅色卡片 (#ffffff)")]:
        ph=40+len(ORDER)*112
        d.rounded_rectangle([24,y,W-24,y+ph],radius=12,fill=E.hx(bg),outline=E.hx("#2a2e38"))
        d.text((44,y+14),lab,font=E.font(13,True),fill=E.hx("#8a91a0"))
        cx=210
        for s in SIZES:
            d.text((cx,y+14),"%dpx"%s,font=E.font(11),fill=E.hx("#8a91a0")); cx += (s+22) if s>40 else 54
        yy=y+44
        for r_ in ORDER:
            d.text((44,yy+38),NAMES[r_],font=E.font(15,True),fill=E.hx(fg))
            d.text((44,yy+58),r_,font=E.font(11),fill=E.hx("#8a91a0"))
            cx=210
            for s in SIZES:
                ic=masters[r_].resize((s,s),Image.LANCZOS)
                if theme=="light": ic=E.light_mode(ic,r_)
                img.paste(ic,(cx,yy+52-s//2),ic); cx += (s+22) if s>40 else 54
            yy+=112
        y+=ph+18
    # ---- 构造网格(keyline)面板 ----
    d.rounded_rectangle([24,y,W-24,y+186],radius=10,fill=E.hx("#1a1d24"),outline=E.hx("#2a2e38"))
    d.text((44,y+12),"构造: 96 网格 + keyline(圆 d68 / 方 60 / 竖矩 52x68) + 48 中轴   —— 实心圆与线性图标按 keyline 而非等面积配平视觉重量", font=E.font(12,True), fill=E.hx("#8a91a0"))
    for i,(nm,tag) in enumerate([("play","等边三角 内角60°"),("block","正圆 d60 + 45°斜杠"),("like","拇指 60°楔 + 掌心圆角13")]):
        ox = 60 + i*300; oy = y+44; S = 1.25
        d.rectangle([ox,oy,ox+96*S,oy+96*S], outline=E.hx("#2a2e38"))
        for g in range(0,97,8):
            x0 = ox+g*S
            d.line([(x0,oy),(x0,oy+96*S)], fill=E.hx("#20242f"))
            d.line([(ox,oy+g*S),(ox+96*S,oy+g*S)], fill=E.hx("#20242f"))
        cc = (ox+48*S, oy+48*S)
        d.ellipse([cc[0]-34*S,cc[1]-34*S,cc[0]+34*S,cc[1]+34*S], outline=E.hx("#3a4050"))
        d.rectangle([ox+18*S,oy+18*S,ox+78*S,oy+78*S], outline=E.hx("#3a4050"))
        ic = masters[nm].resize((int(96*S),int(96*S)), Image.LANCZOS)
        img.paste(ic,(int(ox),int(oy)),ic)
        d.text((ox,oy+96*S+6), "%s —— %s"%(NAMES[nm],tag), font=E.font(11,True), fill=E.hx("#5d6675"))
    y+=198
    d.text((28,y+6),"接入模拟: 卡片操作栏 (.acts) —— 左=默认态, 右=喜欢/不喜欢已激活(.on)", font=E.font(13,True), fill=E.hx("#8a91a0")); y+=34
    for variant in ["idle","on"]:
        d.rounded_rectangle([24,y,W-24,y+98],radius=10,fill=E.hx("#1a1d24"),outline=E.hx("#2a2e38"))
        x=44
        for key in ["play","read","next","like","dislike","block"]:
            d.rounded_rectangle([x,y+30,x+40,y+68],radius=6,fill=E.hx("#1a1d24"),
                outline=E.hx("#67c23a") if (variant=="on" and key=="like") else (E.hx("#f56c6c") if (variant=="on" and key=="dislike") else E.hx("#2a2e38")))
            ic=masters[key].resize((20,20),Image.LANCZOS); img.paste(ic,(x+10,y+39),ic); x+=46
        d.text((44,y+12),("默认态" if variant=="idle" else "激活态 (.on)"),font=E.font(12,True),fill=E.hx("#8a91a0")); y+=108
    d.rounded_rectangle([24,y,W-24,y+150],radius=10,fill=E.hx("#1a1d24"),outline=E.hx("#2a2e38"))
    d.text((44,y+14),"关系校验: 不喜欢 = 喜欢旋转 180° (同一几何)", font=E.font(13,True), fill=E.hx("#8a91a0"))
    lk=masters["like"].resize((96,96),Image.LANCZOS); rot=masters["like"].transpose(Image.ROTATE_180).resize((96,96),Image.LANCZOS)
    dis=masters["dislike"].resize((96,96),Image.LANCZOS)
    img.paste(lk,(80,y+40),lk); img.paste(rot,(320,y+40),rot); img.paste(dis,(600,y+40),dis)
    d.text((196,y+74),"→ 旋转180° →",font=E.font(13,True),fill=E.hx("#8a91a0"))
    same = masters["like"].transpose(Image.ROTATE_180).getchannel("A").tobytes()==masters["dislike"].getchannel("A").tobytes()
    d.text((452,y+74),"≡ 与不喜欢几何一致? "+("是" if same else "否"),font=E.font(13,True),fill=E.hx("#67c23a") if same else E.hx("#f56c6c"))
    for lab,x in (("喜欢",86),("喜欢.rot180",326),("不喜欢",606)):
        d.text((x,y+140),lab,font=E.font(11,True),fill=E.hx("#8a91a0"))
    y+=162
    img.crop((0,0,W,y+10)).save(os.path.join(HERE,"preview.png"))
    rows=""
    for n in ORDER:
        rows += (f'<tr><td class="lb">{NAMES[n]}<span>{n}</span></td>'
                 f'<td><img src="{n}.svg" width="96"><img src="{n}.svg" width="48"><img src="{n}.svg" width="24"><img src="{n}.svg" width="18"></td>'
                 f'<td class="lt"><img src="{n}.svg" width="96"><img src="{n}.svg" width="48"><img src="{n}.svg" width="24"><img src="{n}.svg" width="18"></td>'
                 f'<td><svg width="96" height="96" style="color:#8a91a0"><use href="icons.svg#ico-{n}"/></svg></td></tr>')
    open(os.path.join(HERE,"preview.html"),"w",encoding="utf-8").write(f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>几何比例图标预览</title>
<style>body{{background:#12151b;color:#e8eaf0;font:14px/1.5 "Segoe UI","Microsoft YaHei",sans-serif;padding:24px}}
table{{border-collapse:collapse}}td{{padding:10px 16px;border-bottom:1px solid #2a2e38;vertical-align:middle}}
td.lb{{font-weight:600;width:110px}}td.lb span{{display:block;color:#8a91a0;font-size:11px;font-weight:400}}
td.lt{{background:#fff;border-radius:8px}}img{{vertical-align:middle;margin-right:10px}}</style>
<h2>几何比例 · 斐波那契规格</h2><table>{rows}</table></body></html>''')

if __name__ == "__main__":
    main()
