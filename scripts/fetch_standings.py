#!/usr/bin/env python3
"""
NPB順位データ取得スクリプト
GitHub Actionsで毎日自動実行される
データ取得先: npb.jp/bis/eng (NPB公式英語版)
出力: data/standings.json
"""

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path

# 日本時間
JST = timezone(timedelta(hours=9))

TEAM_JA = {
    'Hanshin Tigers':                   '阪神',
    'Yomiuri Giants':                   '巨人',
    'Yokohama DeNA BayStars':           'DeNA',
    'Yokohama BayStars':                'DeNA',
    'Tokyo Yakult Swallows':            'ヤクルト',
    'Hiroshima Toyo Carp':              '広島',
    'Chunichi Dragons':                 '中日',
    'Fukuoka SoftBank Hawks':           'ソフトバンク',
    'Saitama Seibu Lions':              '西武',
    'Hokkaido Nippon-Ham Fighters':     '日本ハム',
    'Orix Buffaloes':                   'オリックス',
    'Chiba Lotte Marines':              'ロッテ',
    'Tohoku Rakuten Golden Eagles':     '楽天',
}

COLORS = {
    '阪神':'#e6b800','巨人':'#f04830','DeNA':'#4488dd',
    'ヤクルト':'#2db87a','広島':'#e83030','中日':'#2266cc',
    'ソフトバンク':'#f09000','西武':'#1a4fa0','日本ハム':'#3080d0',
    'オリックス':'#2255bb','ロッテ':'#888899','楽天':'#cc2222',
}

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'ja,en-US;q=0.7,en;q=0.3',
    'Connection': 'keep-alive',
}


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read()
    # encoding detection
    for enc in ('utf-8', 'shift_jis', 'euc-jp', 'latin-1'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')


def parse_npb_eng(html: str) -> list[dict]:
    """NPB英語版テーブルをパース"""
    # <td> タグ内のテキストを行ごとに取得
    rows = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    teams = []
    for row in rows:
        cells = re.findall(r'<td[^>]*>(.*?)</td>', row, re.DOTALL)
        cells = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
        if len(cells) < 7:
            continue
        # 行の最初のセルがチーム名のリンクを含む
        team_raw = cells[0]
        if not team_raw or team_raw.isdigit():
            continue
        team_ja = TEAM_JA.get(team_raw)
        if not team_ja:
            continue
        try:
            g  = int(cells[1])
            w  = int(cells[2])
            l  = int(cells[3])
            t  = int(cells[4])
            wr = round(w / (w + l), 3) if (w + l) > 0 else 0.0
            sv = w - l
        except (ValueError, IndexError):
            continue

        teams.append({
            'team': team_ja,
            'g': g, 'w': w, 'l': l, 'd': t,
            'wr': wr, 'sv': sv, 'magic': None,
        })
    return teams


def calc_gb(teams: list[dict]) -> list[dict]:
    """ゲーム差を計算して付与"""
    if not teams:
        return teams
    leader = teams[0]
    leader_wr = leader['wr']
    leader_games = leader['w'] + leader['l']

    result = []
    for i, t in enumerate(teams):
        if i == 0:
            t['gb'] = '-'
        else:
            # ゲーム差 = ((首位勝 - 対象勝) + (対象負 - 首位負)) / 2
            gb = ((leader['w'] - t['w']) + (t['l'] - leader['l'])) / 2
            t['gb'] = str(int(gb)) if gb == int(gb) else str(gb)
        result.append(t)
    return result


def load_existing(path: Path) -> dict:
    if path.exists():
        try:
            with open(path, encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def build_history(existing: dict, league_key: str, new_teams: list[dict], today_label: str) -> dict:
    """
    既存の履歴に今日のデータを追記（または末尾を更新）
    同じ日付なら上書き、新しい日付なら追加（最大20ポイント保持）
    """
    hist = existing.get(league_key, {}).get('history', {
        'labels': [],
        'wr': {},
        'sv': {},
    })

    labels = hist.get('labels', [])
    wr_map = hist.get('wr', {})
    sv_map = hist.get('sv', {})

    # 今日のラベルが既にあるか確認
    if labels and labels[-1] == today_label:
        # 末尾を上書き
        idx = -1
    else:
        # 新しいポイントを追加
        labels.append(today_label)
        idx = None  # 追加モード

    for t in new_teams:
        name = t['team']
        if name not in wr_map:
            wr_map[name] = []
        if name not in sv_map:
            sv_map[name] = []

        if idx == -1:
            # 末尾上書き
            if wr_map[name]:
                wr_map[name][-1] = t['wr']
                sv_map[name][-1] = t['sv']
            else:
                wr_map[name].append(t['wr'])
                sv_map[name].append(t['sv'])
        else:
            wr_map[name].append(t['wr'])
            sv_map[name].append(t['sv'])

    # 最大20ポイントに制限
    MAX = 20
    if len(labels) > MAX:
        labels = labels[-MAX:]
        for name in wr_map:
            wr_map[name] = wr_map[name][-MAX:]
        for name in sv_map:
            sv_map[name] = sv_map[name][-MAX:]

    return {'labels': labels, 'wr': wr_map, 'sv': sv_map}


def main():
    out_dir = Path(__file__).parent.parent / 'data'
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / 'standings.json'

    now_jst = datetime.now(JST)
    today_label = f"{now_jst.month}/{now_jst.day}"
    updated = now_jst.strftime('%Y/%m/%d %H:%M')

    existing = load_existing(out_path)

    result = {
        'updated': updated,
        'central': None,
        'pacific': None,
    }

    urls = {
        'central': 'https://npb.jp/bis/eng/2026/stats/std_c.html',
        'pacific': 'https://npb.jp/bis/eng/2026/stats/std_p.html',
    }

    success = True
    for league_key, url in urls.items():
        try:
            print(f"Fetching {league_key}: {url}")
            html = fetch_html(url)
            teams = parse_npb_eng(html)
            if not teams:
                raise ValueError("No team data parsed")
            teams = calc_gb(teams)
            history = build_history(existing, league_key, teams, today_label)
            result[league_key] = {
                'teams': [t['team'] for t in teams],
                'current': teams,
                'history': history,
            }
            print(f"  -> {len(teams)} teams OK")
        except Exception as e:
            print(f"  ERROR: {e}", file=sys.stderr)
            # フォールバック: 既存データをそのまま使用
            if league_key in existing:
                result[league_key] = existing[league_key]
                result['updated'] = existing.get('updated', updated) + ' (cached)'
                print(f"  -> Using cached data")
            else:
                success = False

    # 書き出し
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nSaved to {out_path}")
    if not success:
        sys.exit(1)


if __name__ == '__main__':
    main()
