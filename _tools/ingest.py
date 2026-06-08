#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ingest.py — clbs-youtube-research v2.0 素材収集レイヤー

人気動画TOPに対し、以下を一括取得する:
  - メタデータ（タイトル / 再生数 / 投稿日 / 尺 / チャンネル）
  - 文字起こし（字幕優先 → 無い/粗い時だけ Whisper 補完）
  - 上位コメント（いいね数順）
  - サムネイル画像

依存: yt-dlp（必須） / ffmpeg（Whisper用） / faster_whisper（任意・字幕無し動画の補完）

使い方:
  # 動画を明示指定（推奨：STEP2で特定したTOP動画を渡す）
  python3 ingest.py --out research/transcripts --videos URL1 URL2 ...

  # チャンネルから再生数上位を自動抽出
  python3 ingest.py --out research/transcripts --channel "https://www.youtube.com/@handle" --top 10

オプション:
  --sub-langs ja,en        字幕の優先言語（カンマ区切り、先頭優先）
  --max-comments 100       取得コメント上限
  --whisper-fallback       字幕が無い動画だけ Whisper で文字起こし（デフォルトON）
  --no-whisper             Whisper を一切使わない（字幕のみ・最軽量）
  --always-whisper         字幕があっても必ず Whisper で文字起こし（最高精度）
  --whisper-model small    Whisper モデル（tiny/base/small/medium/large-v3）
  --top 10                 --channel 指定時の上位本数
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd, **kw):
    """サブプロセス実行（標準出力を返す）。失敗しても例外を投げず (rc, out, err) を返す。"""
    p = subprocess.run(cmd, capture_output=True, text=True, **kw)
    return p.returncode, p.stdout, p.stderr


def ytdlp_json(url, extra=None):
    """yt-dlp -J でメタJSONを取得。"""
    cmd = ["yt-dlp", "-J", "--skip-download", "--no-warnings"]
    if extra:
        cmd += extra
    cmd += [url]
    rc, out, err = run(cmd)
    if rc != 0 or not out.strip():
        return None, err
    try:
        return json.loads(out), None
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"


# ---------------------------------------------------------------------------
# 字幕パース
# ---------------------------------------------------------------------------
def parse_json3(path):
    """YouTube json3 字幕を「タイムスタンプ付き行」のリストへ。"""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return []
    lines = []
    for ev in data.get("events", []):
        segs = ev.get("segs") or []
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text or text == "\n":
            continue
        t_ms = ev.get("tStartMs", 0)
        lines.append((t_ms / 1000.0, text))
    return lines


def dedup_lines(lines):
    """自動字幕のローリング重複を簡易除去。"""
    out = []
    prev = None
    for t, txt in lines:
        norm = re.sub(r"\s+", " ", txt).strip()
        if norm and norm != prev:
            out.append((t, norm))
            prev = norm
    return out


def fmt_ts(sec):
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def write_transcript(lines, path, source):
    """タイムスタンプ付き + プレーン本文の両方を書く。"""
    lines = dedup_lines(lines)
    ts_body = "\n".join(f"[{fmt_ts(t)}] {txt}" for t, txt in lines)
    plain = " ".join(txt for _, txt in lines)
    header = f"# transcript source: {source}\n\n"
    Path(path).write_text(header + ts_body + "\n", encoding="utf-8")
    Path(str(path).replace(".txt", ".plain.txt")).write_text(plain + "\n", encoding="utf-8")
    return len(lines)


# ---------------------------------------------------------------------------
# 字幕取得
# ---------------------------------------------------------------------------
def fetch_subs(url, vid_dir, sub_langs):
    """字幕（手動優先→自動）を json3 で取得。取得言語のコードを返す。無ければ None。"""
    tmpl = str(vid_dir / "sub.%(ext)s")
    langs = ",".join(sub_langs)
    # 手動字幕を優先、無ければ自動字幕
    for auto_flag in (["--write-subs"], ["--write-auto-subs"]):
        cmd = [
            "yt-dlp", "--skip-download", "--no-warnings",
            *auto_flag, "--sub-langs", langs, "--sub-format", "json3",
            "-o", tmpl, url,
        ]
        run(cmd)
        # 優先言語順に探す
        for lang in sub_langs:
            for cand in vid_dir.glob(f"sub.{lang}*.json3"):
                lines = parse_json3(cand)
                if lines:
                    return cand, lang, ("manual" if "--write-subs" in auto_flag else "auto")
        # 言語タグ揺れ対応（ja-JP 等）
        any_json3 = sorted(vid_dir.glob("sub.*.json3"))
        if any_json3:
            lines = parse_json3(any_json3[0])
            if lines:
                lang = any_json3[0].name.split(".")[1]
                return any_json3[0], lang, ("manual" if "--write-subs" in auto_flag else "auto")
    return None, None, None


# ---------------------------------------------------------------------------
# Whisper 補完
# ---------------------------------------------------------------------------
def whisper_transcribe(url, vid_dir, model_name):
    """音声DL→faster_whisperで文字起こし。lines を返す。"""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        print("    [warn] faster_whisper 未導入のため Whisper をスキップ", file=sys.stderr)
        return None
    audio_tmpl = str(vid_dir / "audio.%(ext)s")
    rc, _, err = run([
        "yt-dlp", "-x", "--audio-format", "mp3", "--no-warnings",
        "-o", audio_tmpl, url,
    ])
    audio = next(iter(vid_dir.glob("audio.mp3")), None)
    if not audio:
        print(f"    [warn] 音声DL失敗: {err[:200]}", file=sys.stderr)
        return None
    print(f"    Whisper ({model_name}) 文字起こし中...", file=sys.stderr)
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(audio), vad_filter=True)
    lines = [(seg.start, seg.text.strip()) for seg in segments if seg.text.strip()]
    try:
        audio.unlink()  # 音声は残さない
    except OSError:
        pass
    return lines


# ---------------------------------------------------------------------------
# コメント
# ---------------------------------------------------------------------------
def fetch_comments(url, vid_dir, max_comments):
    """コメントを取得し、いいね数順 top を返す。"""
    tmpl = str(vid_dir / "comments.%(ext)s")
    cmd = [
        "yt-dlp", "--skip-download", "--no-warnings",
        "--write-comments",
        "--extractor-args", f"youtube:max_comments={max_comments},all,100;comment_sort=top",
        "--write-info-json", "-o", tmpl, url,
    ]
    run(cmd)
    info = next(iter(vid_dir.glob("comments*.info.json")), None)
    if not info:
        return []
    try:
        data = json.loads(info.read_text(encoding="utf-8"))
    except Exception:
        return []
    comments = data.get("comments") or []
    cleaned = [{
        "author": c.get("author"),
        "text": c.get("text", "").strip(),
        "like_count": c.get("like_count") or 0,
        "is_reply": bool(c.get("parent") and c.get("parent") != "root"),
    } for c in comments if c.get("text")]
    cleaned.sort(key=lambda x: x["like_count"], reverse=True)
    try:
        info.unlink()
    except OSError:
        pass
    return cleaned[:max_comments]


def fetch_thumbnail(url, vid_dir):
    tmpl = str(vid_dir / "thumb.%(ext)s")
    run([
        "yt-dlp", "--skip-download", "--no-warnings",
        "--write-thumbnail", "--convert-thumbnails", "jpg",
        "-o", tmpl, url,
    ])
    thumb = next(iter(vid_dir.glob("thumb.jpg")), None)
    return thumb.name if thumb else None


# ---------------------------------------------------------------------------
# 1動画の処理
# ---------------------------------------------------------------------------
def process_video(url, out_dir, args):
    meta, err = ytdlp_json(url)
    if not meta:
        print(f"[skip] メタ取得失敗: {url}\n    {err}", file=sys.stderr)
        return None
    # ytsearch / playlist URL を渡された場合は先頭エントリへ降りる
    while meta.get("entries"):
        entries = [e for e in meta["entries"] if e]
        if not entries:
            print(f"[skip] エントリ空: {url}", file=sys.stderr)
            return None
        meta = entries[0]
    # 実watch URLへ解決（以降の字幕/コメント取得を確実にする）
    url = meta.get("webpage_url") or meta.get("url") or url
    vid = meta.get("id", "unknown")
    vid_dir = Path(out_dir) / vid
    vid_dir.mkdir(parents=True, exist_ok=True)
    title = meta.get("title", "")
    print(f"[ingest] {vid}  {title[:50]}", file=sys.stderr)

    record = {
        "id": vid,
        "url": meta.get("webpage_url", url),
        "title": title,
        "channel": meta.get("channel") or meta.get("uploader"),
        "view_count": meta.get("view_count"),
        "like_count": meta.get("like_count"),
        "comment_count": meta.get("comment_count"),
        "upload_date": meta.get("upload_date"),
        "duration_sec": meta.get("duration"),
        "transcript_source": None,
        "transcript_lang": None,
        "transcript_lines": 0,
        "comments_fetched": 0,
        "thumbnail": None,
    }

    # --- 文字起こし ---
    lines, source, lang = None, None, None
    if not args.always_whisper:
        sub_path, lang, kind = fetch_subs(url, vid_dir, args.sub_langs)
        if sub_path:
            lines = parse_json3(sub_path)
            source = f"subtitle:{kind}"
            try:
                sub_path.unlink()
            except OSError:
                pass
    if lines is None and (args.always_whisper or args.whisper_fallback):
        wl = whisper_transcribe(url, vid_dir, args.whisper_model)
        if wl:
            lines, source, lang = wl, f"whisper:{args.whisper_model}", "auto"
    if lines:
        n = write_transcript(lines, vid_dir / "transcript.txt", source)
        record.update(transcript_source=source, transcript_lang=lang, transcript_lines=n)
    else:
        print(f"    [warn] 文字起こし取得できず", file=sys.stderr)

    # --- コメント ---
    comments = fetch_comments(url, vid_dir, args.max_comments)
    if comments:
        (vid_dir / "comments.json").write_text(
            json.dumps(comments, ensure_ascii=False, indent=2), encoding="utf-8")
        md = "\n".join(
            f"- ❤{c['like_count']} {'↳' if c['is_reply'] else ''} {c['text']}"
            for c in comments)
        (vid_dir / "comments.md").write_text(md + "\n", encoding="utf-8")
        record["comments_fetched"] = len(comments)

    # --- サムネ ---
    record["thumbnail"] = fetch_thumbnail(url, vid_dir)

    # --- メタ保存 ---
    (vid_dir / "meta.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return record


# ---------------------------------------------------------------------------
# チャンネルから上位N本
# ---------------------------------------------------------------------------
def channel_top_videos(channel_url, top):
    """チャンネルの動画を flat 列挙し、再生数上位 top 本の URL を返す。"""
    base = channel_url.rstrip("/")
    if not base.endswith("/videos"):
        base += "/videos"
    meta, err = ytdlp_json(base, extra=["--flat-playlist"])
    if not meta:
        print(f"[error] チャンネル取得失敗: {err}", file=sys.stderr)
        return []
    entries = meta.get("entries") or []
    for e in entries:
        e["_vc"] = e.get("view_count") or 0
    entries.sort(key=lambda e: e["_vc"], reverse=True)
    urls = []
    for e in entries[:top]:
        vid = e.get("id")
        if vid:
            urls.append(f"https://www.youtube.com/watch?v={vid}")
    return urls


def main():
    ap = argparse.ArgumentParser(description="clbs-youtube-research 素材収集")
    ap.add_argument("--out", required=True, help="出力ディレクトリ（例: research/transcripts）")
    ap.add_argument("--videos", nargs="*", default=[], help="動画URL（複数可）")
    ap.add_argument("--channel", help="チャンネルURL（--top と併用）")
    ap.add_argument("--top", type=int, default=10, help="--channel時の上位本数")
    ap.add_argument("--sub-langs", default="ja,en", help="字幕優先言語（カンマ区切り）")
    ap.add_argument("--max-comments", type=int, default=100, help="コメント取得上限")
    ap.add_argument("--whisper-fallback", action="store_true", default=True,
                    help="字幕無し動画のみWhisper（デフォルトON）")
    ap.add_argument("--no-whisper", dest="whisper_fallback", action="store_false",
                    help="Whisperを使わない")
    ap.add_argument("--always-whisper", action="store_true", help="常にWhisperで文字起こし")
    ap.add_argument("--whisper-model", default="small", help="Whisperモデル名")
    args = ap.parse_args()
    args.sub_langs = [s.strip() for s in args.sub_langs.split(",") if s.strip()]

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    urls = list(args.videos)
    if args.channel:
        print(f"[channel] 上位{args.top}本を抽出中...", file=sys.stderr)
        urls += channel_top_videos(args.channel, args.top)
    if not urls:
        print("[error] --videos か --channel を指定してください", file=sys.stderr)
        sys.exit(1)

    records = []
    for u in urls:
        r = process_video(u, out, args)
        if r:
            records.append(r)

    # インデックス
    index_path = out / "index.json"
    index_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[done] {len(records)}本処理 → {index_path}", file=sys.stderr)
    # サマリ表
    print("\n=== 取得サマリ ===", file=sys.stderr)
    for r in records:
        print(f"  {r['id']} | 再生{r['view_count']} | 字幕{r['transcript_lines']}行({r['transcript_source']}) "
              f"| コメ{r['comments_fetched']} | {r['title'][:40]}", file=sys.stderr)


if __name__ == "__main__":
    main()
