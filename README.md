# clbs-yt-research

YouTubeチャンネルの**異常値（バズ動画）をリサーチ・モデリングし、バズの方程式を言語化する** [Claude Code](https://claude.com/claude-code) スキルです。

「企画を出す」のではなく「**企画を出すための材料を揃える**」前段スキル。出力した `04_research_summary.yaml` を、台本スキル **[clbs-video-script-pro](https://github.com/conlab-clbs/clbs-video-script-pro)** や `clbs-sns` に渡すと、企画立案の精度が上がります。

---

## 特徴

- **第三者集計サイトのフォールバック取得** — YouTubeの直接Fetchは失敗しがちなので、yutura / noxinfluencer / digitalcreators / tuber-ch を複数照合して数値を確定。捏造せず、取れない値は `[要確認]` 明示。
- **異常値モデリング（心臓部）** — 人気動画TOPを4軸（タイトルテンプレ／フォーマット型／ポジショニング／サムネ要素）で分解し、**バズの方程式を1〜3行で言語化**。
- **継承 vs 破壊の切り分け** — 真似すべきテンプレと、飽和して壊すべきマンネリを分離。
- **競合ベンチマーク** — 3〜5社と比較し、対象がまだやっていない切り口（gap_opportunities）を抽出。
- **後段スキルへの引き渡し** — `04_research_summary.yaml` に戦略推奨まで構造化して出力。

## 出力ファイル（`research/` 配下）

| ファイル | 内容 |
|---|---|
| `01_channel_diagnosis.md` | 対象チャンネルの現状診断 |
| `02_viral_modeling.md` | 異常値のテンプレ・バズ方程式 |
| `03_competitor_benchmark.md` | 競合ベンチマークと差分 |
| `04_research_summary.yaml` | 後続スキルへの統合サマリ（単一定義源） |

## 使い方

Claude Code でこのスキルを入れた状態で「YouTubeリサーチして」「このチャンネル分析して」「競合リサーチ」などと話しかけると起動します。

1. **ヒアリング**（最小限）— 対象チャンネルURL／目的／競合（任意）
2. **STEP1** 現状診断（登録者・累計再生・ポジショニング・勢い）
3. **STEP2** 異常値モデリング（人気動画TOP→4軸分解→バズ方程式）
4. **STEP3** 競合ベンチマーク（差分＝gap_opportunities抽出）
5. **STEP4** 統合サマリ `04_research_summary.yaml` 出力

## パイプライン上の位置づけ

```
clbs-yt-research（リサーチ）
   ↓ 04_research_summary.yaml
clbs-video-script-pro（企画考案＋台本）or clbs-sns（多媒体展開）
   ↓
clbs-youtube-edit（編集）→ 完成動画
```

## ライセンス

[MIT](LICENSE)

## 関連

- 企画考案＋台本（後段）: [clbs-video-script-pro](https://github.com/conlab-clbs/clbs-video-script-pro)
- 編集（最終段）: [clbs-youtube-edit](https://github.com/conlab-clbs/clbs-youtube-edit)
