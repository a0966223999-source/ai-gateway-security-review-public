# DAG v2 獨立工程 QA

日期：2026-11-13。審查者：`/root/dag_independent_qa`（獨立工程 agent，非人類 ACK）。

**判斷：候選工程 QA PASS，限下述離線範圍。正式驗收尚未完成。**

本次沒有修改受審程式、正式服務、原 SQL 實作或 Hub；沒有執行正式模型。v1 的既有 FAIL 記錄保留，本判斷針對以下 v2 候選雜湊。

## 獨立執行

使用 `/workspace/scratch/edebd832b1d2/gateway-live/backend/.venv/bin/python`，`XJ_DEER_BACKEND` 指向同一 backend，候選及 tests 目錄作為 PYTHONPATH，設定 `PYTHONDONTWRITEBYTECODE=1`。

- `python -m unittest test_dag_v2 -v`：**13 tests，14.903s，OK，無 skip**。
- 本輪先前獨立執行 `python -m unittest test_platform_codex_dag -q`：**9 tests，7.000s，OK，無 skip**。
- 增量獨立執行 `python -m unittest test_dag_v2.DurableTests.test_real_process_exit_after_root_resumes_saved_sql -v`：**1 test，7.818s，OK**。真子程序在 root 的 SQL 結果及 runtime 保存後 `os._exit(77)`；驗證退出碼 77、先前 trace 僅 A，重讀持久 intent 後完成 B/C，最終 trace 為 A/B/C 各一次。
- 增量獨立執行 `python -m unittest test_platform_codex_batch_rpc -v`：**1 test，0.070s，OK**。實際 FastMCP 註冊 schema 包含舊 `codex_batch_once` 與新 `codex_dag_once`，不接受 executable/prompt；原單項 schema 保留。
- 增量獨立執行 `python -m unittest test_dag_v2.DurableTests.test_hundred_node_chain_executes_all_once -v`：**1 test，48.932s，OK**。100 節點真 temporary SQL／synthetic CLI 依序執行，trace 各一次，所有 SQL rows 為 succeeded、attempt=1，終值為 100。新增 v2 測試共 15 項已分批獨立執行通過（13+1+1）。
- 最新 fixture 與 RPC schema 回歸獨立執行 `python -m unittest test_platform_codex_batch test_platform_codex_batch_rpc -q`：**12 tests，0.111s，OK**。fixture 差異僅對純獨立 coordinator suite 補 mock snapshot=None，既有斷言保留；DAG SQL 已由獨立 temporary SQL suite 覆蓋。
- 向後相容增量獨立執行 `test_legacy_loaded_tool_can_execute_and_recover_small_v2`、`test_legacy_partial_v2_selection_rejected`：**2 tests，4.467s，OK**。v2 小圖可由原 `ExecuteBatch/codex_batch_once` 全節點集合明確執行，終態收據恢復不 spawn；partial selection 被拒絕且不 spawn。新增測試累計 17 項已分批通過，並非本 agent 在最新增量一次重跑全部 17 項。
- 向後相容 active 恢復增量獨立執行 `test_legacy_waiting_intent_resumes_after_external_parent`：**1 test，4.063s，OK**。舊 mode 的 WAITING intent 持久保存後，供應者完成；由重建 Host 發現原 intent，再由恢復函式完成消費者，trace A/D 各一次。新增 v2 測試累計 **18 項分批獨立通過**；受審 production runtime 檔案與上一輪相容性審查相同。
- 原 repository 與 ORM 使用 temporary SQLite；CLI、authority、transport 為測試合成資料。

13 項新增測試實際覆蓋：4 節點執行；100 節點解析與 101 節點拒絕；畸形外部依賴；跨 batch 等待釋槽、供應者完成與消費者讀取原保存結果；保存 root 後重建 FreshBatchHost 並恢復且 root 不重送；uncertain native fence 不 spawn；結果摘要／completion／ACK 反例；錯誤 owner 與 payload digest；期限不延長；整批 cancelled 保存 halted；新 operation key 不接管既有 intent；容量不足不 reserve；實際 `fresh_batch_worker.serve` 在 8 個 waiting intents 下仍 poll 並執行 supplier（mock transport、真 temporary SQL 與 synthetic CLI）。

另獨立純 mock 檢查：停止 batch 保存 halted、active intent 過期保存 halted、已完成收據於期限後原樣重送、依賴 WAITING 不保存 terminal；四者皆無 spawn。

## 修復核對

- 結果驗證核對原 SQL 完整 completion payload 的 canonical SHA256，並要求 completion succeeded、有效 ACK digest、非空 intent 及 child canonical。
- 未啟動／不明清單依原 SQL pristine 與驗證結果分類，避免把單純函式呼叫當作 native attempt。
- durable admission 保存於既有 BatchRow.execution_spec 的獨立欄位，保留 native_control 嚴格三欄契約；使用原 SQL writer lock 後讀取並寫入，綁定原 request digest、完整 binding、graph digest 與固定期限。
- 恢復只處理已 admitted 的原 scope；保存 succeeded 結果必須重新驗證；非 pristine 的不明節點 halt，不重送。
- 等待依賴與容量不足會釋放 coroutine，worker 仍 poll；已停止 batch 允許讀取並分類終態，dispatch gate 仍拒絕停止狀態。
- 相容性分支仍使用原 ExecuteBatch schema 的 1..3 節點限制且必須選取全圖；scope 保留原 mode/payload，使用原 model 驗證保存 body，恢復也按原 mode 解析。未將原 schema 放大至 100 節點；大圖使用 ExecuteDag。

本次範圍內沒有剩餘可確證的阻斷缺陷。

## 尚未驗收

- 100 節點在正式模型／正式宿主上的執行；工程環境 synthetic CLI 全鏈已完成獨立驗證。
- 原正式宿主重新啟動、執行中子程序尚存時的 abrupt termination、跨程序競爭／SQL lock 壓力。新增真 subprocess 非清理退出已驗證「root SQL 保存完成後」的硬退出邊界，不能替代所有 crash 視窗。
- 所有 8 個等待消費者最終公平完成；現有 worker 測試確認 supplier 不被等待者餓死。
- 真 HTTP／Redis transport、正式模型、正式部署、全新 Work 提交與跨 Work 讀回／恢復。
- 人類或指定責任人的最終 ACK。
- 舊 ExecuteBatch 模式在部分結果保存後的真 subprocess hard-exit 恢復。舊模式 WAITING active 恢復及 terminal 重送已測；新 ExecuteDag 模式的保存 root／hard-exit 恢復已測。

## 候選 SHA256

| 檔案 | SHA256 |
|---|---|
| platform_codex_dag.py | 3fa9d9cabd2776f511a30b63757d44a731836329b112955f0f306318722dabd0 |
| platform_dag_runtime.py | 8fe7cfbd5a1811a8b86290c31d71710513040db8865cf035f40189ed532a15a4 |
| fresh_batch_worker.py | 17d8975085104445039ee0f5216b98c5bd850fd4f470614bd93aeb23b14f4cb9 |
| fresh_batch_host.py | 5e57768840a4aa97aae74a0d14324804fc87df8d3e756eb719c1ef018327eb96 |
| platform_codex_executor.py | c642970debbd44275eaec251f1aee40cdbcc140bffa1accb6718b0d49c1a458c |
| tests/test_dag_v2.py | c0f4c0f4065ff2b25b2c9760b1d1b6376b59f68b02e285f0609d9d4c07171894 |
| tests/test_platform_codex_dag.py | 64a6e4f925a80c402de956d37051c50e0b6c9d3ef26a5791f14e632cc626cd42 |
| fresh_batch_rpc.py | da84bbce66335912874b97176511386077218c23face5648409732cf929a1dfa |
| tests/test_platform_codex_batch.py | b0b7ce7eae391b30c71540f87c9456e3814452c04023f9923befd319acabbba0 |
| tests/test_platform_codex_batch_rpc.py | 47bf9a43d24f5459e1c9da21146fdb021a38f4a3442299afcaeb16929f84bcab |

原 backend tree 無 Git metadata，不能確證其來源 commit；本輪相關 SQL 契約檔案 pin：

- model.py：`581ca36f39d43be5ace8e985d8f4c2041dc2dc5d06f4d8918f32b9e74c452715`
- sql.py：`1871084e8df3a5f8b8f0e44ef80e9805793cf9ff0acbbdff0a9fa86fc9ea7a5f`
