# 状態: 実装 / 試験済み / 実機で未検証 / 限界

日付: 2026-10-10（0.1.1 の確認は 2026-10-11）。版: 0.1.1（0.1.0 からの違いはテスト用の依存の下限だけ。下の表の結果は 0.1.0 のコードで取ったもので、0.1.1 でもコードは同じ）。確認した環境: macOS 26.5（arm64）、Python 3.11.7（`requirements-lock.txt`）。Python 3.12 の結果は末尾。

**実機（サーボ、PCA9685、実カメラ、Joy-Con、ボード上の ESP32）では何も動かしていない。** 「試験済み」はすべて Mac 上のホスト実行（仮想デバイス、合成のサーボ、描画した動画）。

## 実装した物と、その確認

| 部品 | 実装 | 試験で確認したこと | 試験 |
|---|---|---|---|
| 試行ログ v1（`triallog.py`） | 3 本の JSONL、原子的な公開、追記・上書き拒否、検証器 | 共通フィールド、`capture_time` の規則、無効な観測が 0 を持たない、seq の単調性（epoch ごと）、行番号つきのエラー、mode と link の組合せ、ID の形 | `test_triallog.py` |
| ファーム核（`guarded_servo_core.h`）とスケッチ | プロトコル v2。epoch、重複 seq、期限（受信側時計）、拒否は fault を立てない、`applied` と `commanded` を分ける、符号つき数値の拒否、入力あふれは STOP 1 回 | ホスト用にコンパイルした同じ核を、Python モデルと行ごとに比較（手書き 1 本＋場面ごとの台本 4 本＋乱数 60 本）。`HARDWARE_VERIFIED=false` で ARM 拒否、OE を最初に HIGH、`0xFD←0x00` が無い | `test_conformance.py`、`test_firmware_source.py` |
| 仮想デバイス（`vdevice.py`） | 核の Python モデル | 再接続後に再送した ARM+SET が拒否される、同じ接続内で DISARM した後の再送も拒否、重複 seq が無害で期限も延ばさない、期限切れで止まる、スルー制限が毎 tick 守られる、範囲外・不正数値は fault を立てない、乱数トラフィックで不変条件が壊れない | `test_vdevice.py` |
| リンク（`link.py`） | 仮想時間 / 実時間 / 疑似端末 / シリアル | pyserial で疑似端末の仮想デバイスと HELLO→ARM→SET→STATUS、ポートが無いときの明確なエラー | `test_link_pty.py` |
| 記録器・再生器 | 入力→整形→指令、締切方式、遅れた指令の破棄、再開（明示） | 入力と指令が別ストリーム、ホストの範囲とスルー、dry が何も送らない、再生が同じ指令列を再現、再生ごとに新しい epoch、締切方式のドリフトが小さく sleep 後方式は大きい、遅れた指令が破棄される、再開は `--resume` と `allow_clear` が要る、実機相当のリンクは人の確認が要る | `test_record_play.py` |
| 入力 | キーボード、タッチパッド（HTTP）、Joy-Con（標準入力）、台本 | トークン必須（loopback でも）、127.0.0.1 が既定、範囲外・非数・過大ボディの拒否、ブラウザ時刻とホスト受信時刻が別、端末の復元 | `test_inputs.py` |
| 観測（`observe.py`、`camera_probe.py`） | HSV マーカー、設定検証、動画ファイル、`media_pos_ms`、欠測の理由 | 描画動画の自己テスト（角度の RMS 誤差、後述）、欠測・2 個目の点は無効＋理由＋null、`capture_time_ns=null`、設定エラーが `KeyError` でない、上書き拒否、引数解析前に cv2 を読み込まない | `test_observe.py` |
| 解析（`analysis.py`、`session_report.py`） | 整列、遅れ推定、再現性、録音と再生の差 | 既知の遅れの復元（±3 ms）、スケール・オフセット・欠測に不変、端の外へ外挿しない、時間 0 の取り方 | `test_analysis.py` |
| デモ（`demo.py`） | 全工程の合成実行 | 31 試行すべて検証を通る、ラベル（synthetic）、失敗ケース、再開、時刻、基準との許容差一致、マニフェストのハッシュ | `test_demo.py` |
| 遷移データ（`prepare_real_dataset.py`） | 試行ログ→遷移 CSV→NPZ | 短い行、BOM、`valid` の打ち間違い、重複、単位の範囲、dt、上書き拒否（拡張子なしも）、ファイルの閉じ忘れ、最小セッション数 | `test_dataset.py` |
| CLI | 上記の入口 | 全サブコマンドの `--help`、既定が dry、実機の 3 つの鍵、マニフェスト | `test_cli.py` |
| 校正・整形・動作・ビジョン | | 線形校正と版、範囲・スルー、3 動作の形、射影変換と退化の拒否 | `test_shaping_motions_calibration.py`、`test_vision.py` |

件数: 全 334 件が通る（約 1〜2 分）。突然変異テスト: 結果と数字（テストと一緒に選んだ欠陥と、テストとは別に用意した変異の別）は `docs/MUTATION_CHECK.md`。2026-10-10 に公開する内容に対して通し直した（A 32/32、B 165/165、同値 8/8 が生き残り）。

| 確認 | 結果 |
|---|---|
| ファームの `arduino-cli` コンパイル | 通る。300,121 bytes（8 %）、静的 RAM 23,416 bytes（7 %）、警告 0（`docs/FIRMWARE_BUILD.md`）。書込みはしていない |
| 描画動画での検出（mp4 圧縮つき） | 角度の RMS 誤差 0.06〜0.08°（5 つの乱数、マーカー位置のゆらぎ 0 の理想条件、61 フレーム、最大 0.16〜0.19°）。ゆらぎ 0.3 px で 0.13〜0.15°、1 px で 0.37〜0.42°（`reference_results/observe_selftest.json`）。合成画像上の検出の確認で、実カメラの精度ではない |
| Swift プローブ | `swiftc` で警告なしに通る。`--clock-check` のホスト時刻が Python の `time.monotonic_ns()` の区間に入る。接続 0 台で正常終了。Joy-Con では未実行 |
| ホスト時計の整合 | Swift（`CLOCK_UPTIME_RAW`）と Python の単調時計が同じ時計であることを 1 回の測定で確認 |

## 実機で未検証（現物の前でやる。手順は `docs/HARDWARE_RUN.md`）

1. XIAO + PCA9685 + MG90S での `ARM`/`SET` の実動作。`HARDWARE_VERIFIED` を自分で `true` にしてから。
2. 起動直後・リセット中の OE の電位（物理プルアップ）、サーボ動力が USB から取られていないこと、非常停止の接点定格・配線・復帰条件。
3. `ALL_LED_OFF_H` の修正（`0xFD←0x00` の削除）の効果。データシートに基づくコード上の修正で、オシロ・実機では未確認。ESP32 での `lroundf` の丸めと I2C の応答も未確認。
4. パルス幅→角度の校正（1450〜1550 us が何度か。ホーンの機械的な余裕を含む）。
5. 実カメラ: 取得経路、実際のフレーム間隔、カメラ番号とアクセス許可、照明に対する HSV 範囲、カメラの遅れ。スマホのカメラを Mac の映像入力として使う場合は、安定して映像を渡せるかも未確認。
6. Joy-Con: ペアリング、入力の見え方、姿勢が取れるか（`docs/JOYCON.md` の表）。
7. iPhone の Safari からタッチパッドへ（LAN、トークン付き URL、`performance.now` の時計差の実測）。
8. 切断→再開のうち、実機の USB 抜き差しでのボードのリセット、動力が残ったまま通信だけ切れたときの PCA9685 の出力保持。
9. 3 動作 × 5 回の実測、再現性、録音と再生の差、総遅れ。
10. ATOMS3 Lite のピン割当（未対応）。

## 限界

- 合成の数値は仮定の下の値（`docs/DESIGN.md` の 4）。実物の数字として引用しない。seed 7 の 1 回の例で、seed を変えると動く（`reference_results/seed_table.json`）。
- 録音と再生の差の段（指令 / 適用値 / 観測）の大小は、そのままでは「差の生まれた場所」を示さない（`docs/DESIGN.md` の 5、`docs/CLAIMS.md` S-B01b）。
- 1 軸、約 ±50 us の範囲、スルー 500 us/s。
- 指令の再現性は、10 ms の格子と ±1 ms の送信時刻のずれの影響を受ける（`docs/DESIGN.md` の 3.4）。
- 解析の時間合わせ（動画の 0 秒の位置）は仮定を引数で明示する形。
- `--i-checked-hardware` は自己申告。
- `docs/` の配線図は端子の役割を示す論理図で、写真つきの配線図ではない。

## Python 3.12 と、一番古い版の組

- `PYTHON=python3.12 scripts/verify_fresh_clone.sh`（Python 3.12.14、macOS arm64、ピン留めの依存）を、空のフォルダへの clone から最後まで通した（当時の版）。
- 一番古い版の組（`requirements-lowest.txt`: numpy 2.0.2、opencv-python 4.14.0.94、matplotlib 3.9.4、pytest 8.0.2、Python 3.11.7）: 全 334 テストが通り、デモの全数値が基準と一致した（2026-10-09）。
- 対応外: opencv-python 4.9.0.80（numpy 1.26.4、matplotlib 3.8.4 の組で試した）。自己テストの誤差が 0.037（正規化、上限 0.03）に増え、デモの観測の列が動き、校正ファイルの文字列（版のハッシュ）が numpy の -0.0 の違いで変わった（後者は直した）。numpy 1.26 は opencv-python 4.14 と同時に入らない。
