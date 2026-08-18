# -*- coding: utf-8 -*-
"""
토익 단어장 PDF -> words.json -> index.html 주입

실행:  python build.py

PDF 레이아웃(A4 595x842)이 컬럼 x좌표로 고정되어 있어 좌표 기반으로 뽑는다.
  x0 ~ 67  : 번호(좌)
  x0 ~ 100-176, size 12 Bold : 영단어
  x0 ~ 307 : 번호(우, 중복 인쇄)
  x0 ~ 329 : 품사1   x0 ~ 346 : 뜻1
  x0 ~ 432 : 품사2   x0 ~ 450 : 뜻2
머리말/꼬리말은 NanumBarunGothic 계열이라 폰트 이름으로 걸러진다.

필요 패키지:  pip install pymupdf
"""
import io
import json
import os
import re
import sys
from collections import Counter

import fitz  # PyMuPDF

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
PDF = os.path.join(HERE, "토익-단어장-PDF-for-PC-A4size-All.pdf")
JSON_OUT = os.path.join(HERE, "words.json")
HTML = os.path.join(HERE, "index.html")

POS_MAP = {
    "명": "명사", "동": "동사", "형": "형용사", "부": "부사",
    "전": "전치사", "접": "접속사", "대": "대명사", "감": "감탄사",
    "관": "관사", "조": "조동사",
}

COL_POS1 = (318, 344)
COL_MEAN1 = (344, 424)
COL_POS2 = (424, 447)
COL_MEAN2 = (447, 570)


def parse_pdf(path):
    doc = fitz.open(path)
    entries, problems = [], []

    for pno in range(doc.page_count):
        page = doc[pno]

        # 링크에서 정답 철자(slug)를 y좌표와 함께 확보 -> 철자 교차 검증용
        links = []
        for l in page.get_links():
            m = re.search(r"/voca/([^/?#]+)", l.get("uri") or "")
            if m:
                r = l["from"]
                links.append(((r.y0 + r.y1) / 2, m.group(1)))
        links.sort()

        # 본문 폰트(MalgunGothic)만 수집 -> 머리말/꼬리말 자동 제외
        spans = []
        for b in page.get_text("dict")["blocks"]:
            if b.get("type") != 0:
                continue
            for ln in b["lines"]:
                for sp in ln["spans"]:
                    t = sp["text"].strip()
                    if not t or not sp["font"].startswith("MalgunGothic"):
                        continue
                    spans.append({
                        "x": sp["bbox"][0], "y": sp["bbox"][1], "t": t,
                        "sz": sp["size"], "bold": "Bold" in sp["font"],
                    })

        # --- 행 기준점: 왼쪽 번호 컬럼 ------------------------------------
        # 표의 각 칸은 세로 '중앙정렬'이라, 여러 줄짜리 뜻은 첫 줄이 그 행의
        # 단어보다 위에 찍힌다. 따라서 y구간으로 자르면 윗 행이 가져가 버린다.
        # 대신 각 칸의 품사 마커가 그 칸의 세로 중심에 오는 성질을 이용한다.
        anchors = sorted(
            [(s["y"], int(s["t"])) for s in spans
             if not s["bold"] and s["x"] < 120 and s["t"].isdigit()],
            key=lambda a: a[0])
        if not anchors:
            continue

        def nearest(y, cands):
            """y에 가장 가까운 후보의 인덱스와 거리"""
            bi, bd = None, 1e9
            for i, cy in enumerate(cands):
                dd = abs(cy - y)
                if dd < bd:
                    bi, bd = i, dd
            return bi, bd

        anchor_ys = [a[0] for a in anchors]
        rows = [{"no": a[1], "y": a[0], "word": [], "means": [None, None]} for a in anchors]

        # 단어(굵은 글씨): 가장 가까운 행에 붙인다. 긴 단어가 두 줄로 잘려도 순서대로 이어진다.
        for s in sorted([s for s in spans if s["sz"] >= 11 and s["bold"] and s["x"] < 300],
                        key=lambda s: s["y"]):
            i, dist = nearest(s["y"] + 2.7, anchor_ys)
            if dist < 20:
                rows[i]["word"].append(s["t"])

        # 뜻: 품사 마커 -> 행, 뜻 줄 -> 가장 가까운 품사 마커
        for ci, (pcol, mcol) in enumerate(((COL_POS1, COL_MEAN1), (COL_POS2, COL_MEAN2))):
            marks = sorted([s for s in spans if pcol[0] <= s["x"] < pcol[1] and not s["bold"]],
                           key=lambda s: s["y"])
            if not marks:
                continue
            mark_ys = [s["y"] for s in marks]
            buckets = [[] for _ in marks]
            for s in sorted([s for s in spans if mcol[0] <= s["x"] < mcol[1] and not s["bold"]],
                            key=lambda s: s["y"]):
                i, _ = nearest(s["y"], mark_ys)
                buckets[i].append(s)

            for mi, mk in enumerate(marks):
                if not buckets[mi]:
                    continue
                ri, dist = nearest(mk["y"] - 0.8, anchor_ys)
                if dist > 6:
                    problems.append((pno + 1, rows[ri]["no"], "", f"품사마커 정렬 이상 dy={dist:.1f}"))
                text = ""
                for s in buckets[mi]:
                    nxt = s["t"][0]
                    # 한글은 줄바꿈이 단어 중간에서도 일어나므로 그냥 붙인다.
                    # 다만 앞줄이 닫는 괄호/쉼표/마침표로 끝났으면 확실한 어절 경계다.
                    if text and (
                        (text[-1].isalnum() and nxt.isalnum() and not re.search(r"[가-힣]$", text))
                        or (text[-1] in ")],.·" and nxt.isalnum())
                    ):
                        text += " "
                    text += s["t"]
                text = re.sub(r"\s+", " ", text).strip()
                key = mk["t"].strip("()")
                if text:
                    rows[ri]["means"][ci] = {"pos": POS_MAP.get(key, key), "ko": text}

        for r in rows:
            word = "".join(r["word"]).strip()
            means = [m for m in r["means"] if m]

            # 같은 y의 링크 slug와 철자 대조
            slug, best = None, 1e9
            for ly, ls in links:
                dy = abs(ly - (r["y"] + 3.5))
                if dy < best:
                    best, slug = dy, ls
            if best > 14:
                slug = None
            if slug and slug.replace("-", "").lower() != word.replace("-", "").replace(" ", "").lower():
                problems.append((pno + 1, r["no"], word, f"slug={slug}"))

            if not word or not means:
                problems.append((pno + 1, r["no"], word, "INCOMPLETE"))
                continue

            entries.append({"no": r["no"], "word": word, "means": means})

    return entries, problems


def main():
    if not os.path.exists(PDF):
        sys.exit(f"PDF를 찾을 수 없습니다: {PDF}")

    entries, problems = parse_pdf(PDF)
    entries.sort(key=lambda e: e["no"])
    nos = [e["no"] for e in entries]

    print(f"추출: {len(entries)}단어  (번호 {min(nos)}~{max(nos)})")
    missing = sorted(set(range(1, max(nos) + 1)) - set(nos))
    print(f"  빠진 번호 {len(missing)}개 {missing[:15]}")
    print(f"  중복 번호 {len([n for n, c in Counter(nos).items() if c > 1])}개")
    print(f"  경고 {len(problems)}개 {problems[:5]}")

    # 줄바꿈된 뜻이 엉뚱한 행에 붙으면 괄호 짝이 깨지거나 조각으로 시작한다
    unbal = [(e["no"], e["word"], m["ko"]) for e in entries for m in e["means"]
             if m["ko"].count("(") != m["ko"].count(")")]
    frag = [(e["no"], e["word"], m["ko"]) for e in entries for m in e["means"]
            if re.match(r"^(다[,.\s]|하다|되다|나다|이다|고 |서 |며 )", m["ko"])]
    nopos = [(e["no"], e["word"]) for e in entries for m in e["means"] if not m["pos"]]
    print(f"  괄호 짝 깨짐 {len(unbal)}개 {unbal[:5]}")
    print(f"  조각으로 시작 {len(frag)}개 {frag[:5]}")
    print(f"  품사 비어있음 {len(nopos)}개 {nopos[:5]}")

    # 같은 단어가 다른 번호로 또 나오면 뜻을 합친다
    merged, order = {}, []
    for e in entries:
        k = e["word"].lower()
        if k in merged:
            seen = {(m["pos"], m["ko"]) for m in merged[k]["means"]}
            merged[k]["means"] += [m for m in e["means"] if (m["pos"], m["ko"]) not in seen]
        else:
            merged[k] = e
            order.append(k)

    out = [{"word": merged[k]["word"], "no": merged[k]["no"],
            "rank": (merged[k]["no"] - 1) // 100, "means": merged[k]["means"]}
           for k in order]
    out.sort(key=lambda e: e["no"])

    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
    print(f"저장: words.json  ({len(out)}단어, 랭크 0~{max(e['rank'] for e in out)})")

    # HTML에 주입
    with open(HTML, encoding="utf-8") as f:
        html = f.read()

    payload = json.dumps(out, ensure_ascii=False, separators=(",", ":"))
    new, n = re.subn(
        r"/\*WORDS_JSON_START\*/.*?/\*WORDS_JSON_END\*/",
        lambda _m: "/*WORDS_JSON_START*/" + payload + "/*WORDS_JSON_END*/",
        html, count=1, flags=re.S,
    )
    if not n:
        sys.exit("index.html에서 WORDS_JSON 마커를 찾지 못했습니다.")

    with open(HTML, "w", encoding="utf-8") as f:
        f.write(new)
    print(f"주입: index.html  ({len(new) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
