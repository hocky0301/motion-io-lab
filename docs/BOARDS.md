# ボードとピン割当

## XIAO ESP32S3（通常版。Sense ではない）: 対応

| 役割 | ピン | GPIO | 出典 |
|---|---|---|---|
| I2C SDA | D4 | GPIO5 | Seeed Studio, XIAO ESP32S3 Getting Started のピン表（2026-10-09 に取得）。https://wiki.seeedstudio.com/xiao_esp32s3_getting_started/ |
| I2C SCL | D5 | GPIO6 | 同上 |
| OE（出力無効、HIGH で無効） | D0 | GPIO1 | 同上（D0 は GPIO1、汎用 GPIO / ADC）。OE にこのピンを使うのは本リポジトリの選択 |

ファームは `-DBOARD_XIAO_ESP32S3`（指定が無ければ既定）でこの割当になる。`arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3` で通る（`docs/FIRMWARE_BUILD.md`）。

## ATOMS3 Lite: **未対応（TODO）**。`-DBOARD_ATOMS3_LITE` を付けるとコンパイルエラーにしてある

公式ページ（M5Stack, AtomS3-Lite、2026-10-09 に取得: https://docs.m5stack.com/en/core/AtomS3%20Lite ）で確認できた事実:

- SoC は ESP32-S3FN8、フラッシュ 8 MB、USB Type-C で書込みと通信。
- 仕様表の「IO Interface ×6」は `G5 / G6 / G7 / G8 / G38 / G39`。
- Grove（HY2.0-4P）の線色と信号: 黒 GND、赤 5 V、黄 G2、白 G1（表の見出しは `PORT.CUSTOM`）。
- 出力電圧 3.3 V、給電は Type-C の 5 V。
- 端子図（PinMap）は画像で、本文には文字の対応表が無い。回路図は PDF へのリンク。

対応しない理由:

1. 公式ページが示すのは「外へ出ている GPIO の番号」までで、**下側の 2.54 mm ヘッダの物理的な並び順は画像だけ**にあり、画像の内容は確認していない。
2. SDA / SCL / OE にどの GPIO を割り当てるかは配線の選択で、公式が I2C の既定ピンとして示した記述を確認できていない（Grove の `G1/G2` は表では `CUSTOM` と書かれている）。
3. OE を HIGH に保つ外付けプルアップの置き方（リセット中の電位）は、ボードごとに現物で確認が要る。

対応する場合の手順（現物で）: 端子図の画像と回路図 PDF で 3 本のピンを決める → テスターでリセット中の OE の電位を測る → `guarded_servo.ino` の `BOARD_ATOMS3_LITE` の分岐に割当と出典 URL を書く → `arduino-cli compile --fqbn esp32:esp32:m5stack_atoms3` で通す（FQBN は導入したコアの `arduino-cli board listall` で確認）。XIAO の割当をそのまま写さない。
