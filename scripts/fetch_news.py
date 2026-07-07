#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ニュース・論文を収集してAI要約し news.json を生成するスクリプト（GitHub Actionsで毎日実行）"""
import json, os, re
from datetime import datetime, timezone
from urllib.parse import quote
import requests, feedparser

API_KEY = os.environ.get("OPENAI_API_KEY", "")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

def gnews(q):
    return f"https://news.google.com/rss/search?q={quote(q)}&hl=ja&gl=JP&ceid=JP:ja"

def arxiv(q, n):
    return (f"https://export.arxiv.org/api/query?search_query={quote(q)}"
            f"&sortBy=submittedDate&sortOrder=descending&max_results={n}")

CATEGORIES = [
    dict(id="ai_media", title="🤖 AI×メディア研究（ニュース・論文・行政）", count=10, feeds=[
        ("news",  gnews('生成AI (メディア OR 報道 OR ジャーナリズム) when:14d')),
        ("gov",   gnews('AI メディア site:go.jp when:60d')),
        ("paper", arxiv('(all:"generative AI" OR all:"large language model") AND (all:"journalism" OR all:"news media")', 8)),
    ]),
    dict(id="regional", title="🏘 地方創生×メディア", count=5, feeds=[
        ("news", gnews('地方創生 (メディア OR 情報発信 OR 放送) when:30d')),
        ("news", gnews('地域活性化 ローカルメディア when:30d')),
        ("gov",  gnews('地方創生 メディア site:go.jp when:90d')),
    ]),
    dict(id="media_biz", title="📺 NHK・テレビ制作・新聞社のメディア起業動向", count=5, feeds=[
        ("news", gnews('(NHK OR テレビ局 OR 民放) (新規事業 OR 起業 OR スタートアップ OR 子会社) when:30d')),
        ("news", gnews('新聞社 (新規事業 OR スタートアップ OR DX OR デジタル戦略) when:30d')),
    ]),
    dict(id="museum", title="🦕 自然博物館・科学博物館", count=5, feeds=[
        ("news",  gnews('(科学館 OR 自然史博物館 OR 科学博物館 OR 国立科学博物館) (展示 OR 研究 OR 企画展) when:30d')),
        ("paper", arxiv('all:"science museum" OR all:"natural history museum"', 4)),
    ]),
    dict(id="xr", title="🥽 VR・AR・プロジェクションマッピング", count=5, feeds=[
        ("news",  gnews('(VR OR AR OR プロジェクションマッピング) (展示 OR 研究 OR 体験 OR 教育) when:14d')),
        ("paper", arxiv('cat:cs.HC AND (all:"augmented reality" OR all:"virtual reality" OR all:"projection mapping")', 6)),
    ]),
]

def strip_html(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s or "")).strip()

def fetch_feed(ftype, url):
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0 (news-summary-bot)"})
        d = feedparser.parse(r.content)
    except Exception as e:
        print(f"  ! 取得失敗: {url[:70]} ({e})")
        return []
    is_arxiv = "arxiv" in url
    items = []
    for e in d.entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        date = datetime(*t[:6], tzinfo=timezone.utc).isoformat() if t else ""
        src = e.get("source")
        items.append(dict(
            type="paper" if is_arxiv else ftype,
            title=re.sub(r"\s+", " ", e.get("title", "")).strip(),
            link=e.get("link", ""),
            date=date,
            source=(src.get("title") if src else None) or ("arXiv" if is_arxiv else "Google News"),
            snippet=strip_html(e.get("summary", ""))[:600],
        ))
    return items

def summarize(items):
    """カテゴリ内の記事をまとめて1回のAPI呼び出しで日本語200字要約"""
    if not API_KEY or not items:
        return
    payload = [dict(n=i, title=it["title"], source=it["source"], text=it["snippet"])
               for i, it in enumerate(items)]
    prompt = (
        "あなたはニュース・学術論文の要約者です。以下の各記事について、内容を日本語で約200文字"
        "（180〜220文字）に要約してください。英語の論文は日本語に訳して要約してください。"
        "語尾を「だにゃ」とか「ですにゃ」、「そうだにゃん」など猫キャラクターのようにしてください。"
        "記事本文が短い場合はタイトルと出典から分かる範囲で簡潔にまとめ、憶測は避けてください。\n"
        '必ず次のJSON形式のみで出力: {"summaries":[{"n":0,"summary":"..."}]}\n\n'
        "記事一覧:\n" + json.dumps(payload, ensure_ascii=False)
    )
    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            json=dict(model=MODEL, messages=[{"role": "user", "content": prompt}],
                      response_format={"type": "json_object"}, temperature=0.3),
            timeout=180)
        r.raise_for_status()
        data = json.loads(r.json()["choices"][0]["message"]["content"])
        for o in data.get("summaries", []):
            n = o.get("n")
            if isinstance(n, int) and 0 <= n < len(items):
                items[n]["summary"] = str(o.get("summary", ""))
    except Exception as e:
        print(f"  ! 要約失敗: {e}")

def main():
    out = dict(updated=datetime.now(timezone.utc).isoformat(), categories=[])
    for cat in CATEGORIES:
        print(f"■ {cat['title']}")
        items, seen = [], set()
        for ftype, url in cat["feeds"]:
            for it in fetch_feed(ftype, url):
                key = re.sub(r"\s", "", it["title"])[:25]
                if key and key not in seen:
                    seen.add(key)
                    items.append(it)
        items.sort(key=lambda x: x["date"], reverse=True)
        # 論文・行政情報を優先的に混ぜる
        special = [i for i in items if i["type"] != "news"][: max(1, cat["count"] // 3)]
        news = [i for i in items if i["type"] == "news"]
        items = sorted(special + news, key=lambda x: x["date"], reverse=True)[: cat["count"]]
        print(f"  {len(items)}件取得 → 要約中...")
        summarize(items)
        out["categories"].append(dict(id=cat["id"], title=cat["title"], items=items))
    with open("news.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("✅ news.json を書き出しました")

if __name__ == "__main__":
    main()
