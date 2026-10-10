# ファームのビルド確認（書込みはしていない）

2026-10-09、この Mac で `arduino-cli` を使って**コンパイルだけ**した。ボードには書込んでいない。ESP32 上での実行は確認していない。

| 項目 | 値 |
|---|---|
| arduino-cli | 1.5.1（Homebrew） |
| コア | `esp32:esp32` 3.3.12（`arduino-cli core install esp32:esp32`。ダウンロードは数 GB、数分） |
| FQBN | `esp32:esp32:XIAO_ESP32S3`（既定のオプション。USB Mode = Hardware CDC and JTAG、USB CDC On Boot = Enabled なので `Serial` は USB-C 経由） |
| コマンド | `arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 --warnings all code/firmware/guarded_servo` |
| 終了コード | 0 |
| プログラム領域 | 300,121 bytes（上限 3,342,336 の 8 %） |
| 静的 RAM | 23,416 bytes（上限 327,680 の 7 %、ローカル変数用に 304,264 bytes 残る） |
| 警告（`--warnings all`） | 0 件 |
| `HARDWARE_VERIFIED` | `false` のまま |

参考: `USB CDC On Boot = Disabled`（`:CDCOnBoot=cdc`）では 304,753 bytes / 23,224 bytes。

## ホスト側のビルド（同じコア）

`tests/firmware_host/harness.cpp` が `guarded_servo_core.h` を include して `clang++ -std=c++17 -Wall -Wextra -Werror` でコンパイルし、Python の仮想デバイスと返事を 1 行ずつ比べる（`tests/test_conformance.py`）。これはホストの CPU とスタブの HAL での実行で、ESP32 の実行ではない。

## ATOMS3 Lite

`-DBOARD_ATOMS3_LITE` を付けるとコンパイルが `#error` で止まる（確認済み）。理由は `docs/BOARDS.md`。

## 再現

```bash
arduino-cli core update-index && arduino-cli core install esp32:esp32     # 初回のみ。数分
arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 --warnings all code/firmware/guarded_servo
```

`scripts/verify_fresh_clone.sh` は、コアが入っていればこのコンパイルも実行し、無ければホスト側のビルドだけで進む。サイズはコアの版で変わる。
