# reference_results

コードが出した結果だけを置く。**すべて合成（`"source": "synthetic"`）**。仮想デバイス、合成のサーボ、描画した動画で作った結果で、実機の測定ではない。

- `demo_summary.json` ... 比較の基準（`python -m motion_io_lab compare-reference` が比べる。seed 7、5 回再生）
- `demo_report.md` ... 同じ内容の表
- `calibration_synthetic.json` ... 合成デモが仮定した角度換算（0.1 deg/us。測った値ではない）
- `environment.json` ... これを作った Python / numpy / OpenCV（`opencv_python_package` が pip の版）/ OS の版
- `seed_table.json` ... デモを seed 1〜8 で回した結果（seed ごとの値と、中央値・最小・最大）。seed 7 の基準は、この中の 1 回の例。`python scripts/seed_table.py`
- `ablation.json` ... 仮定した雑音源を 1 つずつ切る／強めたときの、段ごとの数字の動き。`python scripts/ablation.py`（`docs/DESIGN.md` の 5）
- `observe_selftest.json` ... 描画した動画をマーカー検出で読み戻した角度誤差（乱数 5 つ × マーカー位置のゆらぎ 0 / 0.3 / 1 px）。`python scripts/observe_selftest.py`

比較の考え方（`motion_io_lab/reference.py` の冒頭に根拠）:

1. まず**全数値が 1e-6 で一致するか**を見る（同じマシン・同じ版ならこうなる。pinned な `requirements-lock.txt` と、一番古い版の組 `requirements-lowest.txt` の両方で、基準と一致した）。
2. 一致しないとき、動画をデコードして得た数値（検出した角度とその派生、遅れ、無効フレーム数）だけに、固定の許容差（遅れ 8 ms、角度の RMS / SD 0.06 deg など）を与える。OpenCV 4.9 と 5.0 の差の実測の 2 倍で、別ビルドを通すための幅。回帰を見るための幅ではない。
3. 動画を通らない数値（指令・適用値、時刻の守り方、失敗ケース、期待遅れ）と、フレーム数は、常に 1e-6 / 完全一致。

回帰を見るときは `--require-identical`。更新は `python scripts/update_reference.py`（差分を読んでからコミットする）。
