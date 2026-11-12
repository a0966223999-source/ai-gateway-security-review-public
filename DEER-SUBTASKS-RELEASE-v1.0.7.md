# Deer 子任務受控版 v1.0.7｜2026-11-12

已發布：原平台保存多個獨立子項，GPT/Work 按明確授權逐項啟動既有 read-only Codex；原 SQL 保存並獨立查回結果；同鍵查回不重跑；同批部分完成後，可只取消剩餘未嘗試的 pending 子項。

## 本輪真驗收

- 原 batch `native-batch-9fc335f8796f437e9c93bf909d44617e`，operation 前綴 `subtasks-live-1794490963644`。
- first：`batch-item-ea77c272ee8f428790dca8fbbc65ee16`，succeeded/attempt=1，原結果 nonce=前綴-first、sum=423。
- second：`batch-item-689b11b1abf44e09b85e4520d655b312`，succeeded/attempt=1，原結果 nonce=前綴-second、sum=557。
- cancel-only：`batch-item-63b68b0dc35a4afaab88972e8260319d`，cancelled/attempt=0，從未啟動。
- 第一項使用原執行鍵再次呼叫，返回相同 request/child/result SHA，獨立新讀鍵查回兩項 attempt 仍為1，取消項仍0。
- 舊整批取消 request `0a8f3f6fe4df6e9719938748996d7ff71be3baac4af71aa8bd0d551be5b4fd2b` 保留 UNKNOWN，沒有換鍵重播該舊語義。新 pending_only 模式有獨立目的／payload／取消鍵。
- 新待辦取消 request `a35a7e186eef3782d8953ab855b9b302cc3b6aff558a7558c846f176b013ad9f`；新獨立終態讀回 `5d01df682c10a9adb771faa62008aa029139f0fecc6e4d6c1944c58e5396eeee`。

## 已施工及發布

- runtime commit `23c87bb16464d13b2f8e8c929c14027c26898401`。
- 原 Codespace 僅安裝 fresh_batch_host.py、fresh_batch_rpc.py、platform_pending_cancel.py。啟動前核原2cabd850檔案無變更、原host config SHA/generation、同parent active items=0、唯一原worker命令。向114056發SIGTERM，沿原監督程序重載；沒有改私鑰／host權限／原config／Source／D2。
- 原 Vercel project 同分支 Preview `dpl_9P7i3EuuBeBqFtwj7XS9DtAnFgBG` READY，既有 MCP alias 已從原2cab部署切到此版。第一筆 pending_only 請求於舊alias版本前置拒絕 extra_forbidden；確認新部署沒有alias後才沿原流程切換，再以同新取消鍵提交一次，未繞驗證。
- 原私人外掛 1.0.7，release `pluginrel_6af5c738dc508191a65c22c50a831b52`；App binding、雙manifest展示/defaultPrompt與原helper大小讀回保持，僅版本與共用流程更新。

## 驗收精度

- 原宿主真 SQL 私有暫存庫4項新測例，在bf98deb版PASS：混合成功／執行中／待辦，撤回，異owner，attempt與pending矛盾拒絕。原場景所有權為明示合成測具，不是派工授權。
- 最終23c87bb版 test_platform*.py 11項PASS（3.548s），包含上述4項、6項服務契約及原SQL/合成CLI程序生命週期測試；合成CLI與本輪兩個真Codex回執分列。
- bf98deb版原宿主/control 16項PASS（0.431s）。首次因驗收環境缺原已固定的redis==6.4.0而5項匯入失敗；僅安裝到 /tmp/deer-pending-testdeps 後通過，沒有改原共享venv。
- 首次新測例因合成批次預設容量1而不能同時建立兩個原生attempt，修測例明確容量3後通過；未放鬆產品容量限制。
- 原始完整測試日誌仍在原宿主 /tmp/pending-tests.log、pending-tests2.log、pending-regression.log、pending-regression2.log、pending-final-tests.log。本包測試摘要是終端實讀觀察，不冒充已取回日誌bytes。真MCP請求及工具回件以evidence JSON保存。

## 邊界及待辦

只取消 pending、attempt=0、無啟動時間／租約且沒有 native attempt fence 的項目。重用原 batch writer鎖、完整七欄 binding與payloadDigest、原狀態聚合，不改完成結果、不停在途項、不重設once紀錄。

逐項受控執行已真驗；背景自動批次調度／Router動態選路／DAG／原生Work喚回／獨立QA／AIO容器隔離未由此版驗收。單項執行最長90秒、read-only；cancel_batch不是在途停止，宿主STOP是整個平台服務的撤回邊界，不能擅用它停某一項。

真沙盒仍受原Codespace儲存瓶頸阻擋。GPT本回合本地另實查沒有Docker CLI及 /var/run/docker.sock，不能把可用磁碟58GB當成已有沙盒承載。Gate1 DENY。

