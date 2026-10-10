# 主張と証拠の対応（この repo で言えること／言えないこと）

この repo について言える文と、その根拠になるファイル・図・テスト・コマンドを 1 行ずつ対応させる。ここに書く数字は、**種別**の語をつけて読む。種別の意味:

- **設計**: コードの仕様。ソースと単体テストで確認できる。
- **合成**: 仮想デバイス・合成サーボ・描画動画の結果。「道具と指標が動く」ことの証拠で、実物の数値ではない。**この表の合成の文には、文中に「（合成）」を入れてある。消さずに読む。**
- **ホスト上の確認**: この Mac のコンパイル・実行で確かめた事実（実機ではない）。「実測」とは呼ばない。
- **空欄**: 実機を測るまで言えない。値は `docs/HARDWARE_RUN.md` の手順で埋める。

デモの数字の出どころは `reference_results/demo_summary.json`（`python -m motion_io_lab demo`、seed 7、5 回再生）。**seed 7 は 1 回の例**で、8 つの seed（1〜8）に対する中央値と範囲は `reference_results/seed_table.json`。以下の数字は、特に断らなければ seed 7 の値と、8 seed の範囲を併記する。再生成して同じ環境なら全数値が一致し、別の OpenCV のビルドでは動画を通る数値だけが許容差（`motion_io_lab/reference.py` の冒頭にその値と根拠）の中で動く。

## A. 言える: 設計とその確認

| ID | 言える文 | 種別 | 証拠 | 確認コマンド |
|---|---|---|---|---|
| C01 | 入力・指令・観測を別のログに分け、全行に単位・時計の種類・出どころを持たせた | 設計 | `docs/trial_schema_v1.json`、`motion_io_lab/triallog.py` | `pytest tests/test_triallog.py` |
| C02 | 画像の受信時刻を露光時刻と呼ばない。`capture_time_ns` は null、出どころは `unknown` | 設計 | `observe.py`、`test_observe.py::test_file_backend_uses_media_position_and_never_invents_capture_time` | `pytest tests/test_observe.py` |
| C03 | 欠測は 0 ではなく null と理由で記録する（`none_found` / `multiple_blobs`）。複数候補のとき、もっともらしい方を選ばない | 設計 | `test_observe.py::test_dropout_and_second_blob_frames_...`、`test_triallog.py::test_invalid_observation_must_not_carry_zero` | 同上 |
| C04 | 再接続のたびにデバイス（仮想デバイスと、そのモデルとして一致を確認したファームの核）が新しい世代（epoch）を出し、古い接続の ARM / SET を再送しても拒否される。デバイスが武装中で期限内に再接続した場合も、期限切れで止まった後も、再起動後も拒否される。実機では未確認 | 設計＋合成 | `guarded_servo_core.h`、`test_vdevice.py::test_replayed_arm_and_set_after_reconnect_...`、`summary.json` の `failure_cases`（3 つの再接続の型。4 本中 4 本とも `REJECTED reason=epoch`、プラントは動かない）。**武装したまま期限内に再接続した型が、epoch の確認だけを見せる**（`replayed_arm_and_set_after_quick_reconnect`: 再接続の前は armed=1 fault=0）。期限切れの型は、デバイスが先に止まってラッチしているので、epoch が無くても通らない | `pytest tests/test_vdevice.py tests/test_demo.py` |
| C05 | 同じ順序番号の再送は無害な確認応答になり、fault を立てず、期限も延ばさない | 設計＋合成 | `test_vdevice.py::test_duplicate_seq_is_a_harmless_acknowledgement`、`failure_cases` の `duplicate_seq_with_different_value` | 同上 |
| C06 | 範囲外・古い番号・不正な数値は拒否するだけで、止まらない。`-1` を巨大な数として読まない（符号つき・書式違いは `parse` で拒否） | 設計 | `test_vdevice.py::test_signed_or_malformed_numbers_...`、`::test_out_of_range_pulse_...` | 同上 |
| C07 | 期限はデバイスの時計で測る。期限が切れたら止めて、人が `CLEAR` するまで戻らない | 設計 | `test_vdevice.py::test_lease_expiry_...`、`::test_set_arriving_after_expiry_...`、`test_conformance.py::test_the_32_bit_millisecond_clock_wraps_the_same_way_in_both` | 同上 |
| C08 | ファームは起動時に出力無効、`HARDWARE_VERIFIED=false` で ARM を拒否し、`STATUS` は指令値（commanded）と適用値（applied）を別に報告する。適用値は測った位置ではない | 設計 | `guarded_servo.ino`、`test_firmware_source.py`、`test_vdevice.py::test_applied_value_is_reported_separately_from_commanded`、`test_stage_labels.py::test_status_keeps_commanded_and_applied_in_their_own_columns` | `pytest tests/test_firmware_source.py tests/test_stage_labels.py` |
| C09 | Python の仮想デバイスは、ボードにコンパイルされるのと同じヘッダから作ったホスト実行の核と、返事・I2C・OE の列が一致する（手書き 1 本＋場面ごとの台本 4 本＋乱数 60 本。I2C の書込み失敗と 32 ビット時計の折返しを含む）。**C++ コンパイラが無い機械ではこの検査は走らない**（テストはスキップされ、`scripts/verify_fresh_clone.sh` は失敗として扱う） | ホスト上の確認 | `tests/test_conformance.py` | `pytest tests/test_conformance.py` |
| C10 | `ALL_LED_OFF_H` へ 0 を書く処理は、全チャンネルの full-OFF を消すので削除した（データシート Table 8 / 7.3.3 / 7.3.4 に基づく。実機・オシロでは未確認） | 設計 | `docs/DESIGN.md` の 2.1、`test_vdevice.py::test_unused_channels_stay_full_off_...`、`test_firmware_source.py` | 同上 |
| C11 | 再生はホストの単調時計の締切で送る。遅れた指令は送らず記録する（送らなかった行は順序番号を持たない）。自動では再開しない | 設計 | `player.py`、`test_record_play.py`、`test_hardening.py` | `pytest tests/test_record_play.py` |
| C12 | 実機への出力は、`--port`、`--i-checked-hardware`、端末での文字入力の 3 つが無いと出ない。ファーム側の鍵（`HARDWARE_VERIFIED`）は別。`--i-checked-hardware` は自己申告で、疑似端末を張れば通る | 設計 | `cli.py`、`session.py`、`test_cli.py`、`test_record_play.py::test_hardware_style_link_needs_a_human_confirmation` | `pytest tests/test_cli.py tests/test_record_play.py` |
| C13 | スマホブラウザのタッチパッドは 127.0.0.1 が既定。LAN に開いてもトークンが要る。ブラウザの時計とホストの受信時刻は別の列 | 設計 | `inputs.py`、`test_inputs.py`、`test_robustness.py` | `pytest tests/test_inputs.py` |
| C14 | ファームは `esp32:esp32:XIAO_ESP32S3` でコンパイルが通る（300,121 bytes / 3,342,336、警告 0）。書込みはしていない | ホスト上の確認 | `docs/FIRMWARE_BUILD.md` | `arduino-cli compile --fqbn esp32:esp32:XIAO_ESP32S3 --warnings all code/firmware/guarded_servo` |
| C15 | 描画した動画をエンコードし直して読み戻す自己テストで、マーカーの角度誤差は RMS 0.06〜0.08°（5 つの乱数、マーカー位置のゆらぎ 0 の**理想条件**）。ゆらぎ 0.3 px で 0.13〜0.15°、1 px で 0.37〜0.42°。**合成画像上の検出の確認で、実カメラの精度ではない** | 合成 | `reference_results/observe_selftest.json`（`python scripts/observe_selftest.py`）、`test_observe.py::test_rendered_video_self_test_recovers_the_true_angle`（上限 RMS 0.17°、最大 0.34°を守る） | `pytest tests/test_observe.py` |
| C16 | ATOMS3 Lite のピン割当は、公式で確認できないので入れていない | 設計 | `docs/BOARDS.md`、`guarded_servo.ino`（`#error`） | `test_firmware_source.py` |
| C17 | 試行ログは追記も上書きもできない。途中で落ちたログは正規の名前で残らない。読み取り側は、非有限の数、非 UTF-8、ストリーム名の取り違え、時刻の逆行を行番号つきのエラーにする | 設計 | `test_triallog.py` | `pytest tests/test_triallog.py` |
| C18 | テストと一緒に選んだ欠陥（セット A）を、テストがすべて検出する。テストとは別に用意した変異（セット B）では、全テストで検出できる割合は下の数字（同値と判断して生き残らせた 8 件は `docs/MUTATION_CHECK.md` に名前つきで残している）。**独立な目安は B の割合。A は「テストが空でない」ことしか言わない** | ホスト上の確認 | `docs/MUTATION_CHECK.md`、`scripts/mutation_check.py`、`scripts/mutants_independent.py` | `python scripts/mutation_check.py` |
| C19 | ケーブルが抜けると、書込みだけでなく読み出しの失敗でも、再生・録音は「中断」として終わり、再開は明示したときだけ起こる（仮想デバイス上。実機の USB 抜きは未確認） | 設計＋合成 | `test_robustness.py::test_pulled_cable_...` | `pytest tests/test_robustness.py` |

**C18 の数字**（`docs/MUTATION_CHECK.md`、ホスト上の確認）: セット A は 32 件中 32 件を検出。セット B（テストとは別に用意した変異）は 165 件中 165 件を検出。ただしテストは、B で見つかった穴を塞ぐ形で増やした（`tests/test_more_edges.py`）ので、**165/165 は、まだ見つけていない穴が無いことを言うものではない**。同値と判断した 8 件は、実行して生き残ることを確かめてあり、理由は同じファイルに書いてある。この 32 / 165 / 8 は、公開する内容（コードとテストはこの版と同じ）に対して 2026-10-10 に `scripts/mutation_check.py --write` を 1 回通して出した数字（約 54 分、`docs/MUTATION_CHECK.md` は再生成しても同じ内容だった）。

## B. 合成デモの数値（必ず「合成」と書く）

条件: 台本の動き＋手ぶれ（σ = 0.01）を記録（LOOPBACK）→ 同じ指令列を 5 回再生（REPLAY）。各試行はホーン角 0 から始める。サーボは一次遅れ τ = 50 ms・不感帯・ゲイン 0.1°/us（試行ごとに 1 % 変動）、カメラは遅れ 70 ms・30 fps・欠測 3 %・2 個目の点 3 %、mp4 圧縮、マーカーは位置のゆらぎ 0 の理想の円。**仮定の全体は `summary.json` の `synthetic_assumptions` と `docs/DESIGN.md` の 4**（校正は構成上誤差 0、動画の 0 秒の位置は既知、フレームの位相は毎回同じ、を含む）。

| ID | 言える文 | 値 | 図・ファイル |
|---|---|---|---|
| S-B01 | （合成）録音と再生の差（RMS、deg）のうち、検出器の雑音の床（動かないマーカーを 2 回読んだ差、0.065°）と同じ大きさなのは観測の段（0.06〜0.08°）だけ。指令の段（約 0.09〜0.12°）は床より大きいが、その源は解析の格子と送信時刻のずれ（S-B02）で、機械の差ではない。適用値の段は、sweep と step_hold では再生ごとに 0 か 0.1° 前後に偏り、look_wait_nod では 0.06〜0.09° にほぼ連続して乗る（5 回の平均では 0.02〜0.07°）。seed 7: sweep 指令 0.092 / 適用 0.017 / 観測 0.061、step_hold 0.115 / 0.033 / 0.062、look_wait_nod 0.093 / 0.070 / 0.080。8 seed の中央値と範囲（観測）: sweep 0.073（0.061〜0.100）、step_hold 0.063（0.055〜0.073）、look_wait_nod 0.074（0.056〜0.086）。**段の大小から「差がどこで生まれたか」は言えない**（下の S-B01b） | 合成 | `fig3_error_ladder.png`（5 本の点を打つ）、`summary.json` の `rms_deg_replay_vs_recording_*`、`seed_table.json` |
| S-B01b | （合成）注入した雑音源を 1 つずつ切る／強めると、対応する段が動く: 送信時刻のゆらぎを切ると指令の段が 0.093 → 0.000、録音の開始状態を持ち越すと観測の段が 0.069 → 0.129（最初の 0.5 秒を除くと 0.068 のまま）、ゲイン変動 5 % で観測の段 0.143・5 回の SD 0.084、マーカー位置のゆらぎ 1 px で観測の段 0.491。ゲイン変動 1 %、不感帯の 0〜0.5°、欠測の有無は、段の値をほとんど動かさない。3 動作 × 2 seed の平均 | 合成 | `reference_results/ablation.json`（`python scripts/ablation.py`）、`docs/DESIGN.md` の 5 |
| S-B02 | （合成）指令の段の値（0.09〜0.12°）は、同じ値の列を送った再生どうしの、送信時刻のずれ（約 1 ms）を 10 ms の格子で見た量で、機械の差ではない。サンプルごとに比べた差は 0.000、解析の格子を 2 / 5 / 10 / 20 / 40 ms にすると 0.05 / 0.07 / 0.09 / 0.13 / 0.19°（3 動作 × 2 seed の平均）。送信の遅れの p95 は 0.4 ms | 合成 | `summary.json` の `rms_deg_commanded_replay_vs_recording_by_sample`、`send_lateness_ms_p95`、`ablation.json` の `command_stage_by_grid_ms`、`docs/DESIGN.md` の 3.4 |
| S-B03 | （合成）5 回再生した観測の時点ごとの SD（pooled、ddof=1）は 0.048（sweep）/ 0.051（step_hold）/ 0.050（look_wait_nod）deg で、**検出器の雑音の床（動かないマーカーの SD 0.046°）とほぼ同じ**。床を引いた値は 0.013 / 0.021 / 0.020°（n = 5 の SD の相対誤差は約 35 %）。つまり、この SD は検出の雑音の大きさで、サーボの再現性を測れていない。8 seed で 0.043〜0.057 | 合成 | `fig2_repeatability.png`、`repeat_sd_deg.observed_deg`（`pooled_sd`、`noise_floor_sd`、`pooled_sd_minus_floor`）、`observation_noise_floor`、`seed_table.json` |
| S-B04 | （合成）指令から観測までの総遅れ（相互相関）は 130 / 125 / 116 ms、適用値から観測までは 113 / 89 / 99 ms。動作ごとの幅は、指令→観測が 116〜130 ms（seed 7。8 seed では 116〜131 ms）、適用値→観測が 89〜113 ms（seed 7。8 seed では 85〜113 ms）。幅が出るのは、波形に依存する推定器の性質で、ノイズの無いサーボの角度に同じ推定器を当てて作った期待値（その遅れ + 仮定したカメラ遅れ 70 ms）は 131 / 125 / 117 ms と 114 / 89 / 101 ms で、動画経由の推定はそれと、平均で 2 ms 以内、個々の再生でも約 3 ms 以内で一致する。**カメラ遅れ（70 ms と仮定）そのものは推定していない。相互相関の遅れはサーボの応答を含む総遅れ** | 合成 | `fig3_error_ladder.png` 右（× が期待値）、`lag_ms_*`、`lag_ms_*_expected`、`lag_error_ms_*` |
| S-B05 | （合成）指令と適用値の差（RMS）は 0.17 / 0.31 / 0.16 deg。ステップが入る動作（step_hold）で大きい | 合成 | `rms_deg_commanded_vs_applied` |
| S-B06 | （合成）50 us のステップで、ホストの制限なし（デバイスの 500 us/s だけ）だと指令と適用値の差は最大 50 us、適用値が 90 % に届くまで 115 ms。ホストが 400 us/s で整形すると最大 11 us（届くまで 135 ms） | 合成 | `fig6_hard_step.png`、`hard_step_case` |
| S-B07 | （合成。仮定した起床遅れのモデルの下での計算）時刻の守り方: 締切方式のドリフトは数 ppm 以下（seed 7 で −0.3 ppm、8 seed で −2.4〜+1.3 ppm。符号に意味は無い）、p95 の遅れ 0.39 ms。送った後に 1 周期 sleep する方式は同じ起床遅れモデルで 6900〜7700 ppm、30 秒で遅れが約 220 ms に達する。**実機の `time.sleep` の遅れは別に測る** | 合成 | `fig5_pacing.png`、`timing`、`seed_table.json` |
| S-B08 | （合成。仮想デバイス上、n = 1、seed 7）ケーブルを 600 ms 抜くと、デバイスは期限（120 ms）で止まってラッチする（終端の `latched=true` を確認。止まった時刻は測っていない）。再開は `continue`（動作を飛ばして合流）と `restart` のどちらも、HELLO（新 epoch）→ `allow_clear` の設定（人の CLEAR の代わり）→ ARM を経て完了し、再開後の最大の適用速度は 37.6 / 15.0 deg/s で、どちらもデバイスの上限 50 deg/s に収まる。**この速度は上限に収まる設計の確認で、2 方式の優劣の根拠ではない**。再接続中に再送した古い 4 行は 4 行とも拒否 | 合成 | `fig4_disconnect_resume.png`、`resume`、`resume_metrics` |
| S-B09 | （合成）観測の欠測数（5 回の再生の合計）: sweep 419 フレーム中 30、step_hold 298 中 15、look_wait_nod 420 中 14。これは**注入した欠測率（3 % + 3 %）の復元**で、検出器の成績ではない（理由は `none_found` と `multiple_blobs` のみ、値は null）。この無効フレームをまたぐ直線補間で埋めた格子点が、指標に入っている（sweep 13 %、step_hold 9 %、look_wait_nod 6 %） | 合成 | `observation_invalid_reasons`、`observation_bridged_fraction` |
| S-B10 | （合成）範囲外のパルスは拒否されて fault を立てず、次の有効な SET は受理される。負の順序番号は `parse` で拒否 | 合成 | `failure_cases` |

## C. 実機でしか言えないこと（空欄）

`docs/HARDWARE_RUN.md` の 4 の表を実機の値で埋めるまで、次の文は言えない。合成デモの値で代用しない。

- 実機で 1 軸を操作・保存・再生できた。
- 同じ指令の繰り返しで、実物がどれだけ同じ場所へ動いたか（再現性、戻り誤差）。動かさない試行の床を引いた値で。
- カメラの精度・遅延・成功率。パルス→角度の校正値（分度器の読み誤差つき）。
- 再送した古い行が実機でも動かさなかった。切断→再開が実機で通った。
- Joy-Con の姿勢・入力が取れた。iPhone のタッチパッドで操作できた。
- 非常停止の動作・接点定格・USB から動力を取らない構成の確認結果。

## D. 言えないこと

- 「実機で動いた」「実測した」「精度」「成功率」（実機の記録が無い）。ホスト上の確認や合成の結果を「実測」と呼ばない。
- 「再送で動かない」を実機の結果として（確認したのは仮想デバイスと、同じ核のホスト実行まで）。
- 「カメラ遅延は X ms」（出ているのは総遅れの相互相関）。
- 「録音と再生の差が、どの段で生まれたかが分かる」（デモの既定値では、観測の段は検出の床の高さで、指令の段は解析の格子と送信時刻のずれの量。どちらも、差の生まれた段を示さない。S-B01b）。
- 「サーボの再現性は 0.05°」（その数字は検出器の雑音の床）。
- 「ATOMS3 Lite に対応」。
- 「Joy-Con の姿勢が取れる」。
- 「安全」「認証」「二重化」（`--i-checked-hardware` は自己申告、非常停止の定格は未確認）。
- 「世界初」「最先端」の類。

## E. 版

- 対象: このファイルを含むコミット（初回公開）。ソフトのみ・実機未確認。版は `pyproject.toml` の 0.1.0。
- 確認環境: macOS 26.5（arm64）、Python 3.11.7、numpy 2.4.6、opencv-python 5.0.0.93（4.14.0.94 でもデモの全数値が一致。4.9 は対応外）、pyserial 3.5、matplotlib 3.11.2、arduino-cli 1.5.1、ESP32 コア 3.3.12。一番古い版の組（`requirements-lowest.txt`: numpy 2.0.2、opencv-python 4.14.0.94、matplotlib 3.9.4、pytest 8.0.2）でも、全 334 テストが通り、デモの全数値が基準と一致した。
- 日付: 2026-10-10。
