# 指定批次的有界 Codex 背景執行

1. 先用 batch_host_status 核當代簽章心跳、parent 與 execution_available=true，並核使用者對該批次的執行授權。普通拆解／登記授權不自動變成執行授權。
2. 用 batch_status(include_plan=true,include_result=true,item_offset=0,item_limit=25) 獨立查原批次，選同一批次 1–3 個獨立且 pending/attempt=0 的原 item ID。依賴項合併或先只選可獨立執行者。
3. 當次 schema 有 batch_execute_batch 時，用全新 operation_key 明確呼叫；沒有此工具但 batch_task 的 schema 接受 codex_batch_once 時才可沿同一工具使用此模式。兩者都沒有就回 BATCH_BACKGROUND_TOOL_SCHEMA_NOT_AVAILABLE，保留原批次待驗，不嘗試未知欄位或新 HTTP 通道。

```json
{"parent_id":"<當代宿主>","operation_key":"<全新鍵>","execution_mode":"codex_batch_once","batch_id":"<原批次>","item_ids":["<原項目1>","<原項目2>"],"timeout_seconds":90}
```

入口不接受新 prompt、owner、command、path、profile 或 retry。原宿主先核全批所選項目，再由原 worker 逐項沿既有 codex_once、原 SQL once fence 與原程序所有權執行；同一時間只執行一項，最多三項、每項最多90秒，啟動總預算300秒。它不掃描所有待辦，不增加第二個 worker／隊列，不在重啟後重送。

4. MCP 回 PENDING_OR_UNKNOWN 時原 worker 可繼續背景處理；保留原 operation_key，只用 batch_receipt(operation_kind=execute) 查同鍵。不換鍵、不重建批次、不自動重試。回合結束後由宿主繼續不等於 Work 已收到喚醒，通知仍依原訂閱規則另核。
5. CODEX_BATCH_FINISHED 必須逐項有 original_result_saved=true、observed_execution_verified=true。再以全新讀鍵 batch_status(include_result=true,item_offset=0,item_limit=25) 核各項 succeeded、attempt=1、nonce／答案和原目標一致。逐項 CLI 成功不代簽獨立 QA。
6. CODEX_BATCH_HALTED_QUERY_ONLY、拒絕、停止、超時、撤權或未知結果均停止後續啟動；保留已保存結果與未開始待辦。只查原筆，勿為求完成而自動換鍵重跑。停止目前項目沿原 batch_stop；取消尚未開始項目沿原 pending_only=true，兩者分開核回執。

此功能只做到使用者明確交辦的背景批次協調。automatic_dispatch=false 表示普通 submit 及未選中的待辦仍不啟動，並不否認顯式背景批次可用。Router 動態選路、S61 lifespan/Registry/Fiber、原生 Work provider、DAG、AIO 真沙盒與 Codespace 休眠恢復仍各別驗收。

