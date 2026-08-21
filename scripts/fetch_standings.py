#!/usr/bin/env python3
"""
NPB順位・試合データ取得スクリプト
GitHub Actionsで毎日自動実行される

戦略:
  1. npb.jp/bis/eng から順位表をスクレイピング（GitHub Actionsサーバーからは通る）
  2. スクレイピング失敗時 → Claude APIで順位・試合データを両方生成
  3. 今日の試合スケジュールも Claude API で取得（スクレイピングより確実）

出力: data/standings.json
"""

import json, re, sys, os, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))

TEAM_JA = {
    'Hanshin Tigers':                 '阪神',
    'Yomiuri Giants':                 '巨人',
    'Yokohama DeNA BayStars':         'DeNA',
    'Yokohama BayStars':              'DeNA',
    'Tokyo Yakult Swallows':          'ヤクルト',
    'Hiroshima Toyo Carp':            '広島',
    'Chunichi Dragons':               '中日',
    'Fukuoka SoftBank Hawks':         'ソフトバンク',
    'Saitama Seibu Lions':            '西武',
    'Hokkaido Nippon-Ham Fighters':   '日本ハム',
    'Orix Buffaloes':                 'オリックス',
    'Chiba Lotte Marines':            'ロッテ',
    'Tohoku Rakuten Golden Eagles':   '楽天',
}

HEADERS_BROWSER = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml',
    'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
}


# ──────────────────────────────────────────
# スクレイピング関数
# ──────────────────────────────────────────

def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS_BROWSER)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    for enc in ('utf-8', 'shift_jis', 'euc-jp', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def parse_npb_standings(html: str) -> list:
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    teams = []
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        if len(cells) < 5:
            continue
        team_raw = cells[0]
        team_ja = TEAM_JA.get(team_raw)
        if not team_ja:
            continue
        try:
            g = int(cells[1]); w = int(cells[2]); l = int(cells[3]); d = int(cells[4])
            wr = round(w / (w + l), 3) if (w + l) > 0 else 0.0
            sv = w - l
        except (ValueError, IndexError):
            continue
        teams.append({'team': team_ja, 'g': g, 'w': w, 'l': l, 'd': d,
                      'wr': wr, 'sv': sv, 'magic': None})
    return teams


def calc_gb(teams: list) -> list:
    if not teams:
        return teams
    for i, t in enumerate(teams):
        if i == 0:
            t['gb'] = '-'
        else:
            gb = ((teams[0]['w'] - t['w']) + (t['l'] - teams[0]['l'])) / 2
            t['gb'] = str(int(gb)) if gb == int(gb) else str(round(gb, 1))
    return teams


def scrape_standings():
    """NPB公式英語版から順位をスクレイピング"""
    result = {}
    for key, url in [
        ('central', 'https://npb.jp/bis/eng/2026/stats/std_c.html'),
        ('pacific', 'https://npb.jp/bis/eng/2026/stats/std_p.html'),
    ]:
        html = fetch_html(url)
        teams = parse_npb_standings(html)
        if teams:
            result[key] = calc_gb(teams)
            print(f"  スクレイピング成功: {key} ({len(teams)} teams)")
        else:
            raise ValueError(f"No data parsed for {key}")
    return result


# ──────────────────────────────────────────
# Claude API フォールバック
# ──────────────────────────────────────────

def claude_api(prompt: str, api_key: str) -> str:
    body = json.dumps({
        'model': 'claude-sonnet-4-6',
        'max_tokens': 2000,
        'messages': [{'role': 'user', 'content': prompt}]
    }).encode('utf-8')
    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'x-api-key': api_key,
            'anthropic-version': '2023-06-01',
        },
        method='POST'
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    return data['content'][0]['text']


def fetch_via_claude(api_key: str, today_str: str) -> dict:
    """Claude APIで順位・試合データを両方取得"""
    prompt = f"""今日は{today_str}です。2026年NPBプロ野球の最新データをJSONのみで返してください。前置きや説明は不要です。

{{
  "central": [
    {{"team":"阪神","g":108,"w":61,"l":46,"d":1,"wr":0.570,"gb":"-","sv":15,"magic":null}},
    ...セ・リーグ全6チームを順位順で...
  ],
  "pacific": [
    {{"team":"ソフトバンク","g":109,"w":68,"l":40,"d":1,"wr":0.630,"gb":"-","sv":28,"magic":null}},
    ...パ・リーグ全6チームを順位順で...
  ],
  "today": [
    {{"away":"巨人","home":"ヤクルト","as":null,"hs":null,"status":"18:00〜 東京ドーム"}},
    ...{today_str}の全試合（スコアがあれば数値、未試合はnull）...
  ]
}}

magicは優勝マジック数値またはnull。{today_str}終了時点の実際のデータで。"""

    text = claude_api(prompt, api_key)
    clean = text.replace('```json', '').replace('```', '').strip()
    s = clean.index('{'); e = clean.rindex('}')
    return json.loads(clean[s:e+1])


# ──────────────────────────────────────────
# 試合スケジュール取得
# ──────────────────────────────────────────

def fetch_today_schedule(api_key: str, today_str: str) -> list:
    """今日の試合スケジュールをClaude APIで取得"""
    prompt = f"""今日は{today_str}です。2026年NPBプロ野球の本日の試合スケジュールをJSONのみで返してください。

[
  {{"away":"巨人","home":"ヤクルト","as":null,"hs":null,"status":"18:00〜 東京ドーム"}},
  ...本日の全試合...
]

試合がない球団は含めない。スコアがあれば数値、未試合はnull。球場名も含める。"""

    text = claude_api(prompt, api_key)
    clean = text.replace('```json', '').replace('```', '').strip()
    s = clean.index('['); e = clean.rindex(']')
    return json.loads(clean[s:e+1])


# ──────────────────────────────────────────
# 履歴管理
# ──────────────────────────────────────────

def load_existing(path: Path) -> dict:
    if path.exists():
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def build_history(existing: dict, league_key: str, new_teams: list, today_label: str) -> dict:
    hist = existing.get(league_key, {}).get('history', {'labels': [], 'wr': {}, 'sv': {}})
    labels = hist.get('labels', [])
    wr_map = hist.get('wr', {})
    sv_map = hist.get('sv', {})

    if labels and labels[-1] == today_label:
        for t in new_teams:
            if wr_map.get(t['team']): wr_map[t['team']][-1] = t['wr']
            if sv_map.get(t['team']): sv_map[t['team']][-1] = t['sv']
    else:
        labels.append(today_label)
        for t in new_teams:
            name = t['team']
            wr_map.setdefault(name, []).append(t['wr'])
            sv_map.setdefault(name, []).append(t['sv'])

    MAX = 20
    if len(labels) > MAX:
        labels = labels[-MAX:]
        for k in wr_map: wr_map[k] = wr_map[k][-MAX:]
        for k in sv_map: sv_map[k] = sv_map[k][-MAX:]

    return {'labels': labels, 'wr': wr_map, 'sv': sv_map}


# ──────────────────────────────────────────
# メイン
# ──────────────────────────────────────────

def main():
    out_path = Path(__file__).parent.parent / 'data' / 'standings.json'
    out_path.parent.mkdir(exist_ok=True)

    now = datetime.now(JST)
    today_label = f"{now.month}/{now.day}"
    today_str = f"{now.year}年{now.month}月{now.day}日"
    updated = now.strftime('%Y/%m/%d %H:%M')

    existing = load_existing(out_path)
    api_key = os.environ.get('ANTHROPIC_API_KEY', '')

    # ── 1. 順位データ取得 ──
    standings = {}
    try:
        print("▶ スクレイピングを試みます...")
        standings = scrape_standings()
    except Exception as e:
        print(f"  スクレイピング失敗: {e}")
        if api_key:
            print("▶ Claude APIにフォールバック...")
            try:
                data = fetch_via_claude(api_key, today_str)
                if data.get('central'):
                    standings['central'] = calc_gb(data['central'])
                if data.get('pacific'):
                    standings['pacific'] = calc_gb(data['pacific'])
                # 今日の試合もここで取れた場合は使う
                if data.get('today'):
                    standings['_today'] = data['today']
                print("  Claude API成功")
            except Exception as e2:
                print(f"  Claude APIも失敗: {e2}", file=sys.stderr)
        else:
            print("  ANTHROPIC_API_KEY が未設定。既存データを保持します。")

    # ── 2. 今日の試合スケジュール取得 ──
    today_games = standings.pop('_today', None)
    if today_games is None and api_key:
        print("▶ 今日の試合スケジュールを取得...")
        try:
            today_games = fetch_today_schedule(api_key, today_str)
            print(f"  {len(today_games)} 試合取得")
        except Exception as e:
            print(f"  試合スケジュール取得失敗: {e}")
            today_games = existing.get('today', [])
    elif today_games is None:
        today_games = existing.get('today', [])

    # ── 3. JSONを組み立てる ──
    result = {'updated': updated, 'today': today_games}

    for key in ('central', 'pacific'):
        teams = standings.get(key)
        if teams:
            history = build_history(existing, key, teams, today_label)
            result[key] = {
                'teams': [t['team'] for t in teams],
                'current': teams,
                'history': history,
            }
        elif key in existing:
            # データ取得失敗 → 既存を保持
            result[key] = existing[key]
            result['updated'] = existing.get('updated', updated) + ' (cached)'
            print(f"  {key}: 既存データを保持")

    # ── 4. 書き出し ──
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✓ 保存完了: {out_path}")


if __name__ == '__main__':
    main()
